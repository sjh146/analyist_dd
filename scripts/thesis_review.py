#!/usr/bin/env python3
"""M6 테제원장 분기 리뷰 CLI — position_theses 조회/exit (thesis_verdicts 무접촉).

빌 애크먼식 테제원장(M6)의 분기 리뷰 도구. 조회 명령 3종(--list/--status/--report)과
테제 해지 명령(--exit)을 제공한다. 테제 재작성 --rewrite는 다음 todo(9)에서 추가한다.

원칙 (docs/테제원장_PLAN.md §1.2·§3, .omo/plans/thesis-ledger-m6-live.md §결정본):
- append-only 존중: thesis_verdicts는 조회(SELECT)만 — INSERT/UPDATE/DELETE 경로 0
  (DB 트리거가 UPDATE/DELETE 차단 — 07_thesis_ledger.sql:55-67, 코드 경로도 만들지 않음).
- 테제 마스터의 status/decision_log UPDATE는 분기 리뷰 경로(exit)로만 허용.
- decision_log는 JSONB 배열 append(덮어쓰기 금지): decision_log = decision_log || %s::jsonb.

사용법:
  python3 scripts/thesis_review.py --list
  python3 scripts/thesis_review.py --status <thesis_id>
  python3 scripts/thesis_review.py --report
  python3 scripts/thesis_review.py --exit <thesis_id> --reason "사유"

이 모듈은 todo 8 산출물(조회+exit)까지 포함한다. 후속: todo 9(--rewrite 추가),
todo 10(tests/test_thesis_review.py 작성·통과).
"""
# allow: SIZE_OK — onboarding.py 관례 계승(전 함수 한국어 docstring 필수 + 독립 CLI 단일 파일),
# todo 9가 같은 파일에 --rewrite를 추가 예정이라 분리 불가 (task: "신규 파일 1개만").
import argparse
import json
import logging
import os
import sys
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("thesis_review")

# ── PostgreSQL 접속 (thesis_onboarding/ackman_screener 관례: env-driven) ─────
PG_HOST = os.environ.get("POSTGRES_HOST", "postgres")
PG_PORT = int(os.environ.get("POSTGRES_PORT", 5432))
PG_DB = os.environ.get("POSTGRES_DB", "stock_trading")
PG_USER = os.environ.get("POSTGRES_USER", "stock_user")
PG_PASS = os.environ.get("POSTGRES_PASSWORD", "")

# ── 원장 테이블명 ────────────────────────────────────────────────────────────
POSITION_THESES_TABLE = "position_theses"
VERDICTS_TABLE = "thesis_verdicts"

# ── 표시 순서 (07_thesis_ledger.sql CHECK 제약 순서 그대로) ──────────────────
STATUS_ORDER = ("active", "exited", "thesis_broken")
VERDICT_ORDER = ("강화", "유지", "약화", "손상", "파기")


def get_pg_conn():
    """psycopg2 연결 생성 (lazy import — 테스트 환경에 psycopg2 없어도 import 가능)."""
    import psycopg2
    return psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PASS
    )


def _fmt_ts(v):
    """datetime/date/str/None → 표시 문자열 (datetime "YYYY-MM-DD HH:MM", date "YYYY-MM-DD")."""
    if v is None:
        return "-"
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M")
    if hasattr(v, "strftime"):  # datetime.date (verdict_date 등)
        return v.strftime("%Y-%m-%d")
    return str(v)


def _fmt_score(v):
    """DECIMAL 점수 → "+0.2500" (None → "-")."""
    if v is None:
        return "-"
    return f"{float(v):+.4f}"


# ── 조회 (todo 8 — thesis_verdicts는 SELECT 전용, 쓰기 경로 0) ───────────────
# 커넥션 close는 하지 않음 — 호출자(main)가 수명 관리 (thesis_onboarding 관례).


def list_theses(pg):
    """position_theses 전체 행(기본 컬럼) 조회 → dict 리스트 (읽기 전용).

    SELECT id, stock_code, status, created_at ... ORDER BY id.
    반환 키: id(int), stock_code, status, created_at(원형 보존). 예외 → [] + 로그.
    """
    cur = None
    try:
        cur = pg.cursor()
        cur.execute(
            f"SELECT id, stock_code, status, created_at "
            f"FROM {POSITION_THESES_TABLE} ORDER BY id"
        )
        return [
            {"id": int(row[0]), "stock_code": row[1], "status": row[2], "created_at": row[3]}
            for row in cur.fetchall()
        ]
    except Exception as e:
        logger.error(f"테제 목록 조회 실패: {e}")
        return []
    finally:
        if cur is not None:
            cur.close()


def get_thesis(pg, thesis_id):
    """position_theses 단건 조회 → dict | None (읽기 전용, 컬럼명 명시 SELECT).

    컬럼 12개 전부 명시 (SELECT * 금지 — 인덱스 접근 오류 방지, 가독성).
    반환 키: id, stock_code, strategy_name, thesis_text, disproof_criteria,
    intrinsic_value, entry_price, catalyst_events, status, decision_log,
    created_at, updated_at. 대상 없음/예외 → None + 로그.
    """
    cur = None
    try:
        cur = pg.cursor()
        cur.execute(
            f"SELECT id, stock_code, strategy_name, thesis_text, disproof_criteria, "
            f"intrinsic_value, entry_price, catalyst_events, status, decision_log, "
            f"created_at, updated_at FROM {POSITION_THESES_TABLE} WHERE id = %s",
            (thesis_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        (row_id, stock_code, strategy_name, thesis_text, disproof_criteria,
         intrinsic_value, entry_price, catalyst_events, status, decision_log,
         created_at, updated_at) = row
        return {
            "id": row_id, "stock_code": stock_code, "strategy_name": strategy_name,
            "thesis_text": thesis_text, "disproof_criteria": disproof_criteria,
            "intrinsic_value": intrinsic_value, "entry_price": entry_price,
            "catalyst_events": catalyst_events, "status": status,
            "decision_log": decision_log, "created_at": created_at,
            "updated_at": updated_at,
        }
    except Exception as e:
        logger.error(f"테제 조회 실패 (id={thesis_id}): {e}")
        return None
    finally:
        if cur is not None:
            cur.close()


def get_thesis_verdicts(pg, thesis_id, limit=None):
    """thesis_verdicts 판정 이력 조회 → dict 리스트 (읽기 전용 — INSERT/UPDATE/DELETE 0).

    SELECT verdict_date, verdict, verdict_score, evidence_summary, model_version
    WHERE thesis_id = %s ORDER BY verdict_date DESC, id DESC [LIMIT %s].
    limit None → 전체 (--status), limit=1 → 최근 1건 (--list/--report).
    예외 → [] + 로그 (fail-open).
    """
    sql = (
        f"SELECT verdict_date, verdict, verdict_score, evidence_summary, model_version "
        f"FROM {VERDICTS_TABLE} WHERE thesis_id = %s "
        f"ORDER BY verdict_date DESC, id DESC"
    )
    params = [thesis_id]
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)
    cur = None
    try:
        cur = pg.cursor()
        cur.execute(sql, params)
        return [
            {"verdict_date": row[0], "verdict": row[1], "verdict_score": row[2],
             "evidence_summary": row[3], "model_version": row[4]}
            for row in cur.fetchall()
        ]
    except Exception as e:
        logger.error(f"판정 이력 조회 실패 (thesis_id={thesis_id}): {e}")
        return []
    finally:
        if cur is not None:
            cur.close()


def thesis_report(pg):
    """원장 요약 집계 → dict (테제 수/판정 수/verdict 집계/활성 테제 최근 판정).

    읽기 전용 (thesis_verdicts SELECT만). 반환 키:
    - theses_by_status: {status: count} — STATUS_ORDER 전체 키 보장
    - verdict_total: int — thesis_verdicts 전체 행 수
    - verdicts_by_verdict: {verdict: count} — VERDICT_ORDER 전체 키 보장
    - active_latest: [{"id", "stock_code", "latest": 판정 dict | None}] (id 순)
    집계 실패 → 0 채움 + logger.error (콘솔 표시는 계속).
    """
    by_status = {s: 0 for s in STATUS_ORDER}
    by_verdict = {v: 0 for v in VERDICT_ORDER}
    total = 0
    active_rows = []
    cur = None
    try:
        cur = pg.cursor()
        cur.execute(
            f"SELECT status, COUNT(*) FROM {POSITION_THESES_TABLE} GROUP BY status"
        )
        for status, cnt in cur.fetchall():
            by_status[status] = int(cnt)
        cur.execute(f"SELECT COUNT(*) FROM {VERDICTS_TABLE}")
        total = int(cur.fetchone()[0])
        cur.execute(
            f"SELECT verdict, COUNT(*) FROM {VERDICTS_TABLE} GROUP BY verdict"
        )
        for verdict, cnt in cur.fetchall():
            by_verdict[verdict] = int(cnt)
        cur.execute(
            f"SELECT id, stock_code FROM {POSITION_THESES_TABLE} "
            f"WHERE status = 'active' ORDER BY id"
        )
        active_rows = cur.fetchall()
    except Exception as e:
        logger.error(f"원장 요약 집계 실패: {e}")
    finally:
        if cur is not None:
            cur.close()

    active_latest = []
    for row_id, stock_code in active_rows:
        latest = get_thesis_verdicts(pg, row_id, limit=1)
        active_latest.append({
            "id": row_id, "stock_code": stock_code,
            "latest": latest[0] if latest else None,
        })
    return {
        "theses_by_status": by_status,
        "verdict_total": total,
        "verdicts_by_verdict": by_verdict,
        "active_latest": active_latest,
    }


# ── 테제 해지 (todo 8 — 분기 리뷰 경로, append-only) ────────────────────────


def exit_thesis(pg, thesis_id, reason):
    """테제 해지 UPDATE 실행 → rowcount 반환 (0 → 호출자가 오류 처리).

    append-only 계약: decision_log 덮어쓰기 금지 — `decision_log = decision_log ||
    %s::jsonb` 배열 append ([{ts, type: exit, reason}]), status='exited',
    updated_at = now(). WHERE id = %s AND status != 'exited' (이미 exited → 0행).
    성공 시 commit. 예외 → rollback + logger.error + 0 (호출자가 오류 처리).
    """
    entry = json.dumps([{
        "ts": datetime.now().isoformat(),
        "type": "exit",
        "reason": reason,
    }], ensure_ascii=False)
    cur = None
    try:
        cur = pg.cursor()
        cur.execute(
            f"UPDATE {POSITION_THESES_TABLE} SET status = 'exited', "
            f"decision_log = decision_log || %s::jsonb, updated_at = now() "
            f"WHERE id = %s AND status != 'exited'",
            (entry, thesis_id),
        )
        pg.commit()
        logger.info(f"테제 exit UPDATE 완료 — id={thesis_id} (rowcount={cur.rowcount})")
        return cur.rowcount
    except Exception as e:
        logger.error(f"테제 exit UPDATE 실패 (id={thesis_id}): {e}")
        try:
            pg.rollback()
        except Exception:
            pass
        return 0
    finally:
        if cur is not None:
            cur.close()


# ── CLI (todo 8 — thesis_onboarding main 구조 계승) ─────────────────────────
# 하위 명령 오류 → logger.error + sys.exit(1) (비제로 exit).


def _cmd_list(args, pg):
    """--list: 테제 목록 + 각 행 최근 판정 1건 → 콘솔 테이블."""
    rows = list_theses(pg)
    if not rows:
        print("\n(테제 없음 — position_theses 비어 있음)")
        return
    print(f"\n테제 목록 ({POSITION_THESES_TABLE}, 총 {len(rows)}건)")
    print(f"{'id':<4} {'stock':<10} {'status':<14} {'created_at':<18} 최근 판정")
    print("-" * 78)
    for row in rows:
        latest = get_thesis_verdicts(pg, row["id"], limit=1)
        if latest:
            latest_txt = f"{_fmt_ts(latest[0]['verdict_date'])} {latest[0]['verdict']}"
        else:
            latest_txt = "-"
        print(f"{row['id']:<4} {row['stock_code']:<10} {row['status']:<14} "
              f"{_fmt_ts(row['created_at']):<18} {latest_txt}")


def _cmd_status(args, pg):
    """--status <thesis_id>: 테제 상세 + 판정 이력 전체 → 상세 출력.

    대상 없음 → logger.error + sys.exit(1).
    """
    thesis = get_thesis(pg, args.status)
    if thesis is None:
        logger.error(f"테제 없음 — 조회 불가: id={args.status}")
        sys.exit(1)
    print(f"\n테제 상세 — id: {thesis['id']}")
    print(f"  stock_code: {thesis['stock_code']}")
    print(f"  strategy_name: {thesis['strategy_name']}")
    print(f"  status: {thesis['status']}")
    print(f"  thesis_text: {thesis['thesis_text']}")
    print(f"  disproof_criteria: {thesis['disproof_criteria']}")
    print(f"  intrinsic_value: {thesis['intrinsic_value']}")
    print(f"  entry_price: {thesis['entry_price']}")
    print(f"  catalyst_events: {json.dumps(thesis['catalyst_events'], ensure_ascii=False, default=str)}")
    print(f"  decision_log: {json.dumps(thesis['decision_log'], ensure_ascii=False, default=str)}")
    print(f"  created_at: {_fmt_ts(thesis['created_at'])} | updated_at: {_fmt_ts(thesis['updated_at'])}")

    verdicts = get_thesis_verdicts(pg, args.status)
    print(f"\n판정 이력 ({VERDICTS_TABLE}, {len(verdicts)}건):")
    if not verdicts:
        print("  (판정 없음)")
        return
    for v in verdicts:
        print(f"  {_fmt_ts(v['verdict_date'])} {v['verdict']} "
              f"(score={_fmt_score(v['verdict_score'])}) — {v['evidence_summary'] or '-'}")


def _cmd_report(args, pg):
    """--report: 원장 요약 (테제 수/판정 수/verdict 집계/활성 테제 최근 판정)."""
    rep = thesis_report(pg)
    by_status = rep["theses_by_status"]
    total_theses = sum(by_status.values())
    print("\n테제원장 요약 (M6 분기 리뷰)")
    print(f"테제 수: 총 {total_theses} "
          f"(active {by_status['active']} / exited {by_status['exited']} / "
          f"thesis_broken {by_status['thesis_broken']})")
    print(f"판정 수: {rep['verdict_total']}")
    print("verdict별 집계:")
    for vname in VERDICT_ORDER:
        print(f"  {vname}: {rep['verdicts_by_verdict'][vname]}")
    print("활성 테제별 최근 판정:")
    if not rep["active_latest"]:
        print("  (활성 테제 없음)")
        return
    for item in rep["active_latest"]:
        latest = item["latest"]
        if latest:
            print(f"  #{item['id']} {item['stock_code']} — "
                  f"{_fmt_ts(latest['verdict_date'])} {latest['verdict']}")
        else:
            print(f"  #{item['id']} {item['stock_code']} — (판정 없음)")


def _cmd_exit(args, pg):
    """--exit <thesis_id> --reason: 사전 조회 → UPDATE append → rowcount 검증.

    사유(--reason) 필수. 테제 없음/이미 exited/UPDATE rowcount 0 →
    logger.error + sys.exit(1) (비제로 exit).
    """
    reason = (args.reason or "").strip()
    if not reason:
        logger.error('--reason 사유 필수 — 예: --exit 3 --reason "테제 달성"')
        sys.exit(1)
    thesis = get_thesis(pg, args.exit_id)
    if thesis is None:
        logger.error(f"테제 없음 — exit 불가: id={args.exit_id}")
        sys.exit(1)
    if thesis["status"] == "exited":
        logger.error(f"이미 exited 상태 — exit 불가: id={args.exit_id}")
        sys.exit(1)
    updated = exit_thesis(pg, args.exit_id, reason)
    if updated == 0:
        logger.error(f"테제 exit 실패 — 대상 없음 또는 이미 exited: id={args.exit_id}")
        sys.exit(1)
    print(f"테제 exit 완료 — thesis_id: {args.exit_id} (reason: {reason})")


def main():
    """CLI 진입점 (thesis_onboarding main 구조 계승).

    4개 하위 명령: --list/--status/--report(조회), --exit(해지, append-only).
    모든 명령은 PG 연결 필요 — get_pg_conn → try/finally pg.close().
    하위 명령 오류 → 비제로 exit. 명령 미지정 → 도움말 출력 후 정상 종료.
    """
    ap = argparse.ArgumentParser(description="M6 테제원장 분기 리뷰 — 조회/해지 CLI")
    ap.add_argument("--list", action="store_true", help="테제 목록 + 최근 판정 요약")
    ap.add_argument("--status", type=int, default=None, metavar="THESIS_ID",
                    help="테제 상세 + 판정 이력 전체")
    ap.add_argument("--report", action="store_true", help="원장 요약 (테제/판정 집계)")
    ap.add_argument("--exit", dest="exit_id", type=int, default=None, metavar="THESIS_ID",
                    help="테제 해지 (status='exited' + decision_log append)")
    ap.add_argument("--reason", type=str, default=None, help="해지 사유 (--exit와 함께)")
    args = ap.parse_args()

    if not any((args.list, args.status is not None, args.report, args.exit_id is not None)):
        ap.print_help()
        return

    pg = get_pg_conn()
    try:
        if args.list:
            _cmd_list(args, pg)
        elif args.status is not None:
            _cmd_status(args, pg)
        elif args.report:
            _cmd_report(args, pg)
        else:
            _cmd_exit(args, pg)
    finally:
        pg.close()


if __name__ == "__main__":
    main()

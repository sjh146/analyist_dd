#!/usr/bin/env python3
"""R41 — 시장패턴 원장이 '그 세션을 다 본 값'으로 기록됐는지 검사한다(읽기 전용).

WHY (실측 2026-11-13 19:41): `scripts/market_pattern_ledger.py --today`(크론 19:40)는 DB에
그 시점 적재된 봉만으로 세션 지표를 기록한다. 그런데 18:55 KRX OpenAPI 가 이름해석 실패로
중단되면 디스패처가 KIS 폴백(종목당 1콜 ≈ 3.5시간)으로 넘어가고, 19:40 시점 DB 에는 시장의
**21%** 만 있다 → 원장에 `scored=819 / trade_value 12.25조 / regime=flat_choppy` 가 **정상값처럼**
기록됐다(전일 3828 / 22.53조 / broad_up). 이 값은 어떤 결함 표시도 없이 소비자(CEO·엔지니어)에게
'그날 시장'으로 읽히고, 다음 세션 수익률(`next_chg_median_pct`) 계산의 기준도 오염시킨다.

WHY 확장 (실측 2026-11-16 00:3x): 종전 검사는 **마지막 레코드 하나만** DB 봉수와 비교했다.
그래서 ① 원장에 아예 없는 세션(실측: 10-12~11-06 20세션 + 11-12 = 21세션 누락 — 호스트 4주
오프라인 후 `--today` 만 돌아 영원히 안 채워짐), ② `scored` 는 정상인데 `trade_value_sum` 만
0 으로 굳은 행(실측 11-10: 원장 0 vs DB 20.735조)을 **둘 다 통과**시켰다 — 실패할 수 없는 check.
→ 판정을 '창(기본 최근 30세션) 전체'로 넓히고, 누락·부분기록·거래대금 결손을 각각 센다.

수리 경로(자율): `market_pattern_ledger.py --backfill <N>` 은 멱등(같은 세션 덮어쓰기)이므로
그대로 재실행하면 결함이 0 이 된다 — '고치면 통과'하는 check 다.

WHY 3차 확장 (실측 2026-11-18 16:5x): 이 검사는 **DB 를 진실로 삼는다** — 그래서 DB 자신이
불완전하면 구조적으로 눈이 먼다. 2026-11-16 은 root crontab 소실(R42)로 KIS 일봉이 안 돌고
yfinance 스윕만 적재돼 `trading_value` 가 3,724/3,725 NULL(거래대금 합 0.001조)이었는데,
검사는 원장의 ~0 값과 DB 의 ~0 값을 비교해 **비율 1.0 = 통과**로 흘렸다(합계가 양쪽 다 0 인
자기참조). 다른 날짜 기준선은 NULL 22~30% 이므로 문턱을 50% 로 두고 '원천결손'을 결함으로 센다.
→ 이 결함의 수리 경로는 원장 재실행이 아니라 **DB 재수집**(`krx_daily.py --from <날짜> --to <날짜>`,
2콜)이다.

종료코드: 0 = 결함 0 / 1 = 결함 있음 / 2 = 판정 불가(원장 없음·DB 조회 실패).
Stdout 의 **마지막 수치**가 결함 건수다(구동기 eval_check 가 마지막 수치를 check_value 로 읽는다).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, time as dtime, timedelta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(REPO, "data", "reports", "market_pattern_ledger.jsonl")
TOLERANCE = 0.10  # DB 값의 10% 까지는 정상(상장폐지·거래정지·신규상장 차이)
NULL_TV_DEFECT = 0.50  # 거래대금 NULL 비율이 이 이상이면 '그 세션은 DB 자체가 불완전'(기준선 0.22~0.30)
WINDOW_SESSIONS = int(os.environ.get("MPC_WINDOW_SESSIONS") or 30)
# 원천(KRX 일봉) 담당 시각 — daily_bars.sh 가 평일 18:55 에 `krx_daily.py --days 7` 을 돌리는데
# 그 경로는 **D-1 까지**만 적재한다(당일 봉은 발행 전 = 미완성 봉 가드). 따라서 세션 S 의
# trading_value 는 S+1 18:55(+소요 ~35분)에야 채워진다.
SOURCE_DUE_HOUR, SOURCE_DUE_MIN = 19, 30
# 원장 기록 담당 시각 — `market_pattern_tick.sh`(평일 19:40)는 **그 시점 DB 에 있는 최신 세션**을
# 기록하므로 세션 S 는 S+1 평일 19:40(+소요 ~10분)에야 원장에 들어온다. 그 전의 '누락'은 결번이 아니다.
LEDGER_DUE_HOUR, LEDGER_DUE_MIN = 19, 50


def _session_date(session):
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(str(session), fmt).date()
        except ValueError:
            continue
    return None


def source_due_at(session):
    """그 세션의 trading_value 를 원천(KRX 일봉)이 담당하기 시작하는 시각 = 다음 평일 19:30."""
    d = _session_date(session)
    if d is None:
        return None
    day = d + timedelta(days=1)
    while day.weekday() >= 5:  # daily_bars 크론은 평일(1-5)만 돈다
        day += timedelta(days=1)
    return datetime.combine(day, dtime(SOURCE_DUE_HOUR, SOURCE_DUE_MIN))


def source_pending(session, now=None):
    """원천이 아직 그 세션을 담당하지 못한 '정상 과도기'인가.

    WHY (실측 2026-11-20 18:47): 11-19 봉은 KIS 경로(전일 22:5x)만 적재돼 trading_value 가
    3,722/3,722 NULL 이었다. KRX 일봉은 11-20 18:55 에 11-19 를 담당할 예정이므로 이 상태는
    8분 뒤 사라지는 정상 과도기인데, 검사가 이를 '원천결손'으로 세어 상시 위반을 만들었다.
    판정은 '마땅히 있어야 하는데 없는' 것만 세야 한다 — 담당 시각 전의 NULL 은 결번이 아니다.
    """
    due = source_due_at(session)
    if due is None:
        return False
    return (now or datetime.now()) < due


def ledger_due_at(session):
    """그 세션이 원장에 기록될 시각 = 다음 평일 19:40(+소요)."""
    d = _session_date(session)
    if d is None:
        return None
    day = d + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return datetime.combine(day, dtime(LEDGER_DUE_HOUR, LEDGER_DUE_MIN))


def ledger_pending(session, now=None):
    """원장 기록 창이 아직 오지 않은 '정상 과도기'인가.

    WHY (실측 2026-11-21 04:5x): `--today`(19:40)는 그 시점 DB 의 최신 세션을 기록하므로
    원장은 구조적으로 **한 세션 뒤진다**(11-20 세션은 11-23 19:40 에 들어온다). 종전 검사는
    이를 '누락'으로 세어 주말·평일 새벽 틱마다 상시 1건 미달을 만들었다 — 고칠 수 없는 결함을
    신호로 부르면 문턱이 아니라 잡음이다. 담당 시각 전의 누락만 유예하고, 그 뒤에도 없으면 결함.
    """
    due = ledger_due_at(session)
    if due is None:
        return False
    return (now or datetime.now()) < due



def read_records(path=LEDGER):
    """세션 → 레코드. 원장은 세션별 덮어쓰기이므로 뒤에 나온 줄이 이긴다."""
    if not os.path.exists(path):
        return None
    recs = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                recs[str(d["session"])] = d
            except (ValueError, KeyError):
                continue
    return recs


def db_sessions(limit=WINDOW_SESSIONS):
    """최근 limit 세션의 (session, 마감봉 수, 거래대금 합, 거래대금 NULL 비율) — 미완성/무거래 봉은 분모에서 뺀다."""
    sys.path.insert(0, os.path.join(REPO, "scripts"))
    import psycopg2  # noqa: E402

    conn = psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=os.environ.get("POSTGRES_PORT", "5434"),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
        connect_timeout=10,
    )
    try:
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(
            """SELECT trade_date,
                      COUNT(*) FILTER (WHERE volume > 0) AS n,
                      COALESCE(SUM(trading_value), 0) AS tv,
                      CASE WHEN COUNT(*) > 0
                           THEN COUNT(*) FILTER (WHERE trading_value IS NULL)::float / COUNT(*)
                           ELSE 0 END AS null_ratio
                 FROM market_data
                WHERE trade_date IN (SELECT DISTINCT trade_date FROM market_data
                                      WHERE volume > 0
                                      ORDER BY trade_date DESC LIMIT %s)
                GROUP BY 1 ORDER BY 1""",
            (limit,),
        )
        out = [(str(r[0]), int(r[1] or 0), float(r[2] or 0), float(r[3] or 0)) for r in cur.fetchall()]
        cur.close()
    finally:
        conn.close()
    return out


def judge(records, sessions, tol=TOLERANCE, now=None):
    """세션별 판정 → (결함 목록, 요약 줄 목록). 결함 = 누락 · 부분기록 · 거래대금 결손 · 원천결손.

    sessions 원소는 (session, n, tv) 3-튜플도 허용한다(호출부·구 테스트 호환).
    원천(KRX 일봉)이 아직 담당하지 못한 세션의 NULL 은 결함이 아니라 '유예' 줄로 남긴다(은폐 금지).
    """
    defects = []
    lines = []
    for row in sessions:
        session, n, tv = row[0], row[1], row[2]
        null_ratio = float(row[3]) if len(row) > 3 and row[3] is not None else None
        rec = records.get(session)
        if rec is None:
            if ledger_pending(session, now):
                due = ledger_due_at(session)
                lines.append(f"  · 유예(원장 기록 전) {session} — DB {n}봉, "
                             f"원장 담당 {due:%m-%d %H:%M} 이후 판정")
                continue
            defects.append((session, "누락", f"DB {n}봉인데 원장에 세션 없음"))
            continue
        scored = rec.get("scored")
        if scored is None:
            defects.append((session, "부분기록", "scored 없음"))
            continue
        ratio = float(scored) / n if n else 0.0
        if n and ratio < (1 - tol):
            defects.append((session, "부분기록", f"scored={scored} / DB {n}봉 = {ratio:.3f}"))
        source_broken = null_ratio is not None and null_ratio >= NULL_TV_DEFECT
        if source_broken and source_pending(session, now):
            # 정상 과도기 — 원천이 아직 그 세션을 담당하기 전이다(담당 시각 이후면 아래에서 결함으로 센다).
            due = source_due_at(session)
            lines.append(f"  · 유예(원천 미도달) {session} — DB trading_value NULL {null_ratio:.1%}, "
                         f"원천 담당 {due:%m-%d %H:%M} 이후 판정")
            continue
        if source_broken:
            # DB 자신이 불완전하면 원장↔DB 비교는 자기참조다(양쪽 다 0 → 비율 1.0 = 거짓 통과).
            defects.append((session, "원천결손",
                            f"DB trading_value NULL {null_ratio:.1%} (≥{NULL_TV_DEFECT:.0%}) "
                            f"— 수리: krx_daily.py --from {session.replace('-', '')} --to {session.replace('-', '')}"))
        rec_tv = rec.get("trade_value_sum")
        if tv > 0 and not source_broken:
            if not rec_tv:
                defects.append((session, "거래대금", f"원장 0/null vs DB {tv / 1e12:.3f}조"))
            elif float(rec_tv) < tv * (1 - tol):
                defects.append((session, "거래대금",
                                f"원장 {float(rec_tv) / 1e12:.3f}조 < DB {tv / 1e12:.3f}조 ×0.9"))
        if n:
            tv_disp = f"{float(rec_tv) / 1e12:.3f}" if rec_tv else "null"
            lines.append(f"R41: {session} scored={scored} db_volume_gt0={n} ratio={ratio:.3f} "
                         f"tv={tv_disp}조(DB {tv / 1e12:.3f}조)")
    return defects, lines


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    path = LEDGER
    if "--ledger" in argv:
        path = argv[argv.index("--ledger") + 1]
    window = WINDOW_SESSIONS
    if "--window" in argv:
        try:
            window = int(argv[argv.index("--window") + 1])
        except (IndexError, ValueError):
            window = WINDOW_SESSIONS

    records = read_records(path)
    if records is None:
        print("R41: 판정 불가 — 원장 없음", file=sys.stderr)
        return 2
    if not records:
        print("R41: 판정 불가 — 원장이 비어 있음", file=sys.stderr)
        return 2
    try:
        sessions = db_sessions(window)
    except Exception as e:  # noqa: BLE001
        print(f"R41: 판정 불가 — DB 조회 실패({type(e).__name__}: {e})", file=sys.stderr)
        return 2
    if not sessions:
        print("R41: 판정 불가 — DB 에 마감된 봉이 없음", file=sys.stderr)
        return 2

    defects, lines = judge(records, sessions, now=datetime.now())
    # 부분기록 판정 근거(최신 세션)는 항상 한 줄 남긴다 — 통과해도 '무엇을 보고 통과했는지' 남겨야 한다.
    base = next((l for l in reversed(lines) if l.startswith("R41: ")), None)
    print(base or "R41: 비교 가능한 세션이 없음")
    for l in lines:
        if l.startswith("  · 유예"):
            print(l)
    for session, kind, why in defects[:10]:
        print(f"  ✗ [{kind}] {session} — {why}")
    if len(defects) > 10:
        print(f"  … 그 외 {len(defects) - 10}건")
    kinds = {}
    for _, kind, _ in defects:
        kinds[kind] = kinds.get(kind, 0) + 1
    detail = " · ".join(f"{k} {v}" for k, v in sorted(kinds.items())) or "결함 없음"
    print(f"R41: 창 {len(sessions)}세션 / 원장 {len(records)}세션 → {'미달' if defects else '충족'} "
          f"({detail}) | 공백 {len(defects)}건")
    return 0 if not defects else 1


if __name__ == "__main__":
    sys.exit(main())

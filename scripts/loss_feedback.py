#!/usr/bin/env python3
"""손실 피드백 — 청산된 손실 거래를 원인별로 분류해 기록하고, 담당 역할에 되돌린다.

왜 필요한가 (2026-11-17 실측):
  `trader_fills` 에 손실 5건(−21,873원)이 쌓여 있는데, 그 손실이 **왜** 났는지 남는 곳이 없다.
  트레이더 원장에는 "몇 건/얼마"만 있고, 리서처·엔지니어 백로그로 되돌아가는 경로도 없다.
  그래서 같은 유형의 손실이 반복된다(예: 원익IPS 는 'reconcile: broker flat' 으로 6.7시간 만에 청산).

분류 규칙(결정적 — 재현 가능, 우선순위 순):
  1 rule_gap   : 손실폭 ≤ -7% 인데 exit_reason 에 stop 이 없음 → 손절 규칙이 작동하지 않았다
  2 timing     : hold_hours < 2 또는 exit_reason 이 reconcile(대조로 사후 확정) → 청산 시점/경로 문제
  3 signal     : ml_prob < 0.55 (소비자 문턱 미달 신호로 진입) 또는 ml_prob 없음
  4 market_beta: 그날 유니버스 평균이 -0.5% 이하이고 종목-시장 차이가 ±1%p 이내 → 시장이 눌렀다
  5 execution  : exit_reason 이 owner_exit/수동 → 규칙 밖 청산
  6 other      : 위에 안 걸림

출력:
  · data/reports/loss_reviews.jsonl (append, 중복은 (fill_date, code, exit_ts) 키로 방지)
  · DB 테이블 trade_loss_reviews (없으면 생성)
  · role_bus 이벤트(원인별 담당: rule_gap→trader, timing→trader, signal→engineer, market_beta→researcher)
  · stdout 요약(크론이 Discord 로 전달 — 새 손실이 없으면 아무것도 출력하지 않는다)

사용:
  POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434 python3 scripts/loss_feedback.py --days 30
  ... --dry-run     DB/JSONL 쓰기 없이 분류만
  ... --all         이미 기록된 손실도 다시 분류(JSONL 중복 방지 유지)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime

import psycopg2

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "data", "reports", "loss_reviews.jsonl")
ROLE_BUS = os.path.join(REPO, "scripts", "role_bus.py")

OWNER = {
    "rule_gap": "trader",
    "timing": "trader",
    "signal": "engineer",
    "market_beta": "researcher",
    "execution": "trader",
    "other": "trader",
}
ACTION = {
    "rule_gap": "손절 규칙 미작동 — 주문 실패/감시 공백 여부 확인(브리지 로그·loop_state)",
    "timing": "청산 시점 문제 — 진입 직후/대조 청산 원인 확인(신호 신선도·부분체결)",
    "signal": "신호 품질 — ml_prob<문턱 진입 경로 점검(R1 문턱·피드 score_kind)",
    "market_beta": "시장 베타 — 게이트(HEAT/지수 필터) 비용 재측정, 종목 선별이 알파를 줬는지 확인",
    "execution": "규칙 밖 청산 — owner_exit 사유 기록·재발 방지",
    "other": "수동 검토",
}


def pg():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def ensure_table(cur) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS trade_loss_reviews (
            id SERIAL PRIMARY KEY,
            fill_date DATE NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            screener TEXT,
            exit_ts TEXT,
            net_pnl NUMERIC,
            ret_pct NUMERIC,
            hold_hours NUMERIC,
            ml_prob NUMERIC,
            exit_reason TEXT,
            market_ret_pct NUMERIC,
            rel_pct NUMERIC,
            cause TEXT NOT NULL,
            owner TEXT NOT NULL,
            action TEXT,
            detail JSONB,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE (fill_date, code, exit_ts)
        )""")
    # 2026-11-17: 손절 손실의 원인(종목선택 vs 시장베타)을 저장할 상대약세 열
    cur.execute("ALTER TABLE trade_loss_reviews ADD COLUMN IF NOT EXISTS rel5_pct NUMERIC")
    conn_note = None  # noqa: F841  (스키마 변경은 커밋 시 반영)


def market_return(cur, day) -> float | None:
    """그날 유니버스 평균 수익률(전일 종가 대비) — 시장 베타 판정용."""
    cur.execute("""
        WITH d AS (SELECT %s::date AS t),
             prev AS (SELECT (SELECT max(trade_date) FROM market_data WHERE trade_date < (SELECT t FROM d)) AS p)
        SELECT round(avg(r)::numeric, 3) FROM (
            SELECT (max(close_price) FILTER (WHERE trade_date=(SELECT t FROM d))
                    / NULLIF(max(close_price) FILTER (WHERE trade_date=(SELECT p FROM prev)),0) - 1) * 100 AS r
            FROM market_data
            WHERE trade_date IN ((SELECT t FROM d), (SELECT p FROM prev))
            GROUP BY stock_code) x
        WHERE r IS NOT NULL""", (day,))
    r = cur.fetchone()[0]
    return float(r) if r is not None else None


def rel5_pct(cur, code: str, until_day) -> float | None:
    """종목의 최근 5거래일 누적 수익률 − 같은 기간 유니버스 평균 (상대 약세 측정).

    손절은 '설계된 방어'지만, 손절을 맞았다는 사실만으로는 원인이 안 갈린다.
    시장이 버티는데 그 종목만 빠졌다면 원인은 **종목 선택(신호 품질)** 이고,
    시장이 같이 빠졌다면 베타다. 이 함수가 그 둘을 가른다.
    """
    cur.execute("""SELECT trade_date, close_price FROM market_data
                   WHERE stock_code=%s AND trade_date <= %s::date
                   ORDER BY trade_date DESC LIMIT 6""", (code, until_day))
    rows = cur.fetchall()
    if len(rows) < 6:
        return None
    d_new, p_new = rows[0]
    d_old, p_old = rows[-1]
    if not p_old:
        return None
    stock_ret = (float(p_new) / float(p_old) - 1.0) * 100.0
    cur.execute("""
        SELECT round(avg(r)::numeric, 3) FROM (
          SELECT (max(close_price) FILTER (WHERE trade_date=%s)
                  / NULLIF(max(close_price) FILTER (WHERE trade_date=%s),0) - 1) * 100 AS r
          FROM market_data WHERE trade_date IN (%s, %s) GROUP BY stock_code) z
        WHERE r IS NOT NULL""", (d_new, d_old, d_new, d_old))
    u = cur.fetchone()[0]
    if u is None:
        return None
    return round(stock_ret - float(u), 3)


def classify(row: dict) -> tuple[str, str]:
    ret = float(row["ret_pct"] or 0)
    hold = float(row["hold_hours"] or 0)
    prob = row["ml_prob"]
    reason = (row["exit_reason"] or "").lower()
    mkt = row["market_ret_pct"]
    rel = row["rel_pct"]

    if ret <= -7.0 and "stop" not in reason:
        return "rule_gap", "손실폭 -7% 이하인데 stop 사유 아님"
    if hold < 2.0 or "reconcile" in reason:
        return "timing", f"보유 {hold:.1f}시간 / 사유 {row['exit_reason']}"
    # 손절은 설계된 방어다. 그 손절이 '종목 선택 실패'인지 '시장 베타'인지는 상대 약세로 가른다.
    rel5 = row.get("rel5_pct")
    if "stop" in reason and rel5 is not None and rel5 <= -3.0:
        return "signal", f"손절 + 5거래일 상대 약세 {rel5:+.1f}%p (시장은 버팀) — 종목 선택 실패"
    if prob is None or float(prob) < 0.55:
        return "signal", f"ml_prob={prob} (문턱 0.55)"
    if "stop" in reason and rel5 is not None and rel5 > -3.0 and (row.get("market_ret_pct") or 0) <= -0.5:
        return "market_beta", f"손절 + 5거래일 상대 {rel5:+.1f}%p · 시장 {row['market_ret_pct']:+.2f}%"
    if mkt is not None and mkt <= -0.5 and rel is not None and abs(rel) <= 1.0:
        return "market_beta", f"시장 {mkt:+.2f}% · 상대 {rel:+.2f}%p"
    if "owner_exit" in reason or "manual" in reason:
        return "execution", "규칙 밖 수동 청산"
    if "stop" in reason:
        return "signal", f"손절 청산(사유 {row['exit_reason']}) — 상대약세 측정 불가, 종목 선택 실패로 기록"
    return "other", "분류 규칙 미해당"


def load_fills(cur, days: int):
    cur.execute("""
        SELECT fill_date, code, name, screener, exit_ts, net_pnl, ret_pct, hold_hours, ml_prob, exit_reason
        FROM trader_fills
        WHERE exit_ts IS NOT NULL AND net_pnl < 0 AND fill_date >= CURRENT_DATE - %s
        ORDER BY fill_date DESC""", (days,))
    cols = [c.name for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def already_reviewed() -> set[tuple]:
    seen = set()
    if not os.path.exists(OUT):
        return seen
    with open(OUT, encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
                seen.add((str(d.get("fill_date")), d.get("code"), str(d.get("exit_ts"))))
            except Exception:
                continue
    return seen


def post_bus(role: str, task: str, note: str) -> None:
    try:
        subprocess.run([sys.executable, ROLE_BUS, "post", "--role", role, "--kind", "handoff",
                        "--task", task, "--note", note], capture_output=True, text=True, timeout=30)
    except Exception:
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--all", action="store_true", help="이미 기록된 손실도 재분류(JSONL 중복은 유지)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--quiet-if-none", action="store_true", help="새 손실 없으면 침묵(크론용)")
    a = ap.parse_args()

    conn = pg(); cur = conn.cursor()
    rows = load_fills(cur, a.days)
    if not rows:
        if not a.quiet_if_none:
            print(f"[loss] 최근 {a.days}일 손실 청산 없음")
        return 0

    seen = already_reviewed()
    new_rows = []
    skipped_noise = 0
    for r in rows:
        # 노이즈 컷: -100원 미만 손실(수수료·반올림 수준)은 원인 분석 대상이 아니다.
        if float(r["net_pnl"] or 0) > -100:
            skipped_noise += 1
            continue
        key = (str(r["fill_date"]), r["code"], str(r["exit_ts"]))
        if key in seen and not a.all:
            continue
        mkt = market_return(cur, r["fill_date"])
        entry = float(r["ret_pct"] or 0)
        r["market_ret_pct"] = mkt
        r["rel_pct"] = round(entry - mkt, 3) if mkt is not None else None
        # 손절 손실의 원인 판별용: 손실이 확정된 날(없으면 진입일) 기준 5거래일 상대 약세
        exit_day = str(r["exit_ts"])[:10] if r["exit_ts"] else str(r["fill_date"])
        r["rel5_pct"] = rel5_pct(cur, r["code"], exit_day)
        cause, why = classify(r)
        # ── "왜 떨어졌나" 근거 팩(가격·수급·언론·공시·재무·거시·미국장) — 청산일 기준 as-of
        try:
            import evidence as evmod
            ev = evmod.collect(cur, r["code"], exit_day)
            r["evidence_text"] = evmod.narrative(ev)
            r["evidence_score"] = ev.get("score")
        except Exception as e:  # noqa: BLE001
            r["evidence_text"] = f"(근거 수집 실패: {type(e).__name__})"
            r["evidence_score"] = None
        r["cause"], r["why"] = cause, why
        r["owner"] = OWNER[cause]
        r["action"] = ACTION[cause]
        new_rows.append((key, r))

    if not new_rows:
        if not a.quiet_if_none:
            print(f"[loss] 최근 {a.days}일 손실 {len(rows)}건 — 모두 이미 기록됨")
        return 0

    ensure_table(cur)
    if not a.dry_run:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        with open(OUT, "a", encoding="utf-8") as f:
            for key, r in new_rows:
                if key not in seen:          # JSONL 은 append-only — 같은 거래를 두 번 쓰지 않는다
                    f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
                cur.execute("""INSERT INTO trade_loss_reviews
                    (fill_date, code, name, screener, exit_ts, net_pnl, ret_pct, hold_hours, ml_prob,
                     exit_reason, market_ret_pct, rel_pct, rel5_pct, cause, owner, action, detail)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                    ON CONFLICT (fill_date, code, exit_ts) DO UPDATE SET
                      cause=EXCLUDED.cause, owner=EXCLUDED.owner, action=EXCLUDED.action,
                      rel5_pct=EXCLUDED.rel5_pct, detail=EXCLUDED.detail""",
                            (r["fill_date"], r["code"], r["name"], r["screener"], r["exit_ts"],
                             r["net_pnl"], r["ret_pct"], r["hold_hours"], r["ml_prob"], r["exit_reason"],
                             r["market_ret_pct"], r["rel_pct"], r.get("rel5_pct"),
                             r["cause"], r["owner"], r["action"],
                             json.dumps({"why": r["why"], "evidence": r.get("evidence_text"),
                                 "evidence_score": r.get("evidence_score")}, ensure_ascii=False)))
        conn.commit()

    total = sum(float(r["net_pnl"] or 0) for _k, r in new_rows)
    by_cause: dict[str, list[float]] = {}
    for _k, r in new_rows:
        by_cause.setdefault(r["cause"], []).append(float(r["net_pnl"] or 0))

    print(f"[손실 피드백] 새 손실 {len(new_rows)}건 / 합계 {total:,.0f}원 (최근 {a.days}일 기준)")
    for cause, vals in sorted(by_cause.items(), key=lambda kv: sum(kv[1])):
        print(f"  · {cause:12s} {len(vals)}건 {sum(vals):,.0f}원 → {OWNER[cause]}: {ACTION[cause]}")
    for _k, r in new_rows[:5]:
        print(f"    - {r['fill_date']} {r['name']}({r['code']}) {float(r['ret_pct'] or 0):+.2f}% "
              f"{float(r['net_pnl'] or 0):,.0f}원 · {r['cause']} · {r['why']}")
        if r.get("evidence_text"):
            print(f"      왜: {str(r['evidence_text'])[:400]}")

    if not a.dry_run:
        # 원인별로 한 번씩만 인계(같은 원인 중복 인계 방지)
        for cause in by_cause:
            codes = [r["code"] for _k, r in new_rows if r["cause"] == cause][:5]
            post_bus(OWNER[cause], f"loss_review:{cause}",
                     f"{cause} 손실 {len(by_cause[cause])}건({','.join(codes)}) — {ACTION[cause]}")

    cur.close(); conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

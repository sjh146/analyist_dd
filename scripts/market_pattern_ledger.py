#!/usr/bin/env python3
"""market_pattern_ledger.py — 시장 전체 패턴을 **매일 기록**하는 원장(역할 지도에 비어 있던 자리).

WHY (2026-10-06, 사용자 요청): 지금 역할들에는 "시장 자체를 관측·기록하는" 자리가 없다.
- 엔지니어는 시장 정보를 **실험 입력**으로만 쓴다(시장상대 라벨·breadth 피처·국면 조건화 CG126/127).
- 그래서 "게이트가 왜 닫혔나 / 오늘 시장은 어떤 상태였나 / 그 상태에서 다음날 무엇이 일어났나"를
  수치로 남긴 기록물이 없다 → CEO·엔지니어가 다음 수를 고를 때 시장 상태를 근거로 못 쓴다.

이 원장은 **DB에 이미 있는 일봉만으로** 세션 단위 시장 지표를 기록한다(추가 수집·호출 없음, 토큰 0):
  breadth(상승/하락 비율) · ADR · 등락률 분포(중앙·평균·상하위 10% 평균) · 거래대금 추이
  · 국면 라벨(사전 정의 규칙) · HEAT 프록시 · 다음 세션 분포(사후 검증용, 다음 실행에서 채움)

원칙: 값을 지어내지 않는다(없으면 null + 사유) · 멱등(같은 날짜는 덮어씀) · 컬럼명은 런타임 탐지.

사용:
  python3 scripts/market_pattern_ledger.py --backfill 15   # 최근 15세션 기록
  python3 scripts/market_pattern_ledger.py --today         # 최신 세션 1건(틱용)
  python3 scripts/market_pattern_ledger.py --show          # 최근 기록 출력
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(REPO, "data", "reports", "market_pattern_ledger.jsonl")

# 후보 컬럼명(런타임 탐지) — DB 스키마가 바뀌어도 조용히 죽지 않게.
COLS = {
    "code": ("stock_code", "code", "symbol"),
    "date": ("trade_date", "date", "dt"),
    "close": ("close", "close_price", "clpr", "stck_prpr"),
    "volume": ("volume", "vol", "acml_vol"),
    "value": ("trade_value", "value", "amount", "acml_tr_pbmn", "trading_value"),
}


def _connect():
    import psycopg2
    env = {}
    p = os.path.join(REPO, ".env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8", errors="replace"):
            m = re.match(r"^\s*([A-Z_]+)\s*=\s*(.*)$", line)
            if m:
                env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST") or env.get("POSTGRES_HOST") or "127.0.0.1",
        port=int(os.environ.get("POSTGRES_PORT") or env.get("POSTGRES_PORT") or 5434),
        dbname=env.get("POSTGRES_DB") or "stock_trading",
        user=env.get("POSTGRES_USER") or "stock_user",
        password=env.get("POSTGRES_PASSWORD") or os.environ.get("POSTGRES_PASSWORD"),
    )


def detect_columns(conn):
    with conn.cursor() as cur:
        cur.execute("select column_name from information_schema.columns "
                    "where table_name='market_data'")
        have = {r[0] for r in cur.fetchall()}
    out = {}
    for key, cands in COLS.items():
        for c in cands:
            if c in have:
                out[key] = c
                break
    return out


def session_metrics(conn, cols, date):
    c_code, c_date = cols.get("code"), cols.get("date")
    c_close = cols.get("close")
    with conn.cursor() as cur:
        # 전일 종가 대비 당일 등락률(세션 종목 전체)
        cur.execute(
            """with d as (
                 select {code} as code, {close} as close, {date} as dte,
                        lag({close}) over (partition by {code} order by {date}) as prev
                 from market_data where {date} <= %s
               )
               select count(*) filter (where prev is not null),
                      count(*) filter (where close > prev),
                      count(*) filter (where close < prev),
                      count(*) filter (where close = prev),
                      percentile_cont(0.5) within group (order by (close/prev-1)*100)
                        filter (where prev > 0),
                      avg((close/prev-1)*100) filter (where prev > 0),
                      percentile_cont(0.9) within group (order by (close/prev-1)*100)
                        filter (where prev > 0),
                      percentile_cont(0.1) within group (order by (close/prev-1)*100)
                        filter (where prev > 0)
               from d where dte = %s and prev > 0""".format(code=c_code, close=c_close,
                                                                date=c_date),
            (date, date))
        row = cur.fetchone()
        n, up, down, flat, med, avg, p90, p10 = row
        value_col = cols.get("value")
        traded = None
        if value_col:
            cur.execute("select sum({v}) from market_data where {d} = %s".format(
                v=value_col, d=c_date), (date,))
            traded = cur.fetchone()[0]
    return {"session": str(date), "scored": n, "up": up, "down": down, "flat": flat,
            "breadth_pct": round(100.0 * up / n, 1) if n else None,
            "adr": round(up / down, 2) if down else None,
            "chg_median_pct": round(float(med), 3) if med is not None else None,
            "chg_mean_pct": round(float(avg), 3) if avg is not None else None,
            "chg_p90_pct": round(float(p90), 3) if p90 is not None else None,
            "chg_p10_pct": round(float(p10), 3) if p10 is not None else None,
            "trade_value_sum": float(traded) if traded is not None else None}


def regime_label(m):
    """사전 정의 규칙(사후 변경은 커밋으로 기록). 국면은 '관측 라벨'이지 매매 신호가 아니다."""
    b, med = m.get("breadth_pct"), m.get("chg_median_pct")
    if b is None or med is None:
        return "unknown"
    if b >= 60 and med > 0:
        return "broad_up"
    if b <= 40 and med < 0:
        return "broad_down"
    if abs(med) < 0.3:
        return "flat_choppy"
    return "mixed"


def read_ledger():
    out = {}
    if os.path.exists(LEDGER):
        for line in open(LEDGER, encoding="utf-8"):
            line = line.strip()
            if line:
                try:
                    d = json.loads(line)
                    out[d["session"]] = d
                except (ValueError, KeyError):
                    continue
    return out


def write_ledger(records):
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    with open(LEDGER, "w", encoding="utf-8") as f:
        for k in sorted(records):
            f.write(json.dumps(records[k], ensure_ascii=False) + "\n")


def fill_next_session(records):
    """사후 검증: 각 세션에 '다음 세션의 시장 중앙 등락률'을 채운다(그때 알 수 있었던 게 아니다)."""
    keys = sorted(records)
    for a, b in zip(keys, keys[1:]):
        records[a]["next_session"] = b
        records[a]["next_chg_median_pct"] = records[b].get("chg_median_pct")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="시장 전체 패턴 원장(DB 일봉만 사용)")
    ap.add_argument("--backfill", type=int, default=0, help="최근 N세션 기록")
    ap.add_argument("--today", action="store_true", help="최신 세션 1건만")
    ap.add_argument("--show", type=int, default=0, help="최근 N건 출력")
    a = ap.parse_args(argv)

    records = read_ledger()
    if a.show:
        for k in sorted(records)[-a.show:]:
            d = records[k]
            print("{session} | {regime:<11} | breadth {b}% ADR {adr} | 중앙 {m}% 평균 {v}% "
                  "| p90 {p9} p10 {p1} | 다음세션 중앙 {nxt}".format(
                      session=d["session"], regime=d.get("regime"),
                      b=d.get("breadth_pct"), adr=d.get("adr"), m=d.get("chg_median_pct"),
                      v=d.get("chg_mean_pct"), p9=d.get("chg_p90_pct"),
                      p1=d.get("chg_p10_pct"), nxt=d.get("next_chg_median_pct")))
        return 0
    if not (a.backfill or a.today):
        ap.print_help()
        return 0

    conn = _connect()
    cols = detect_columns(conn)
    need = ("code", "date", "close")
    if any(c not in cols for c in need):
        print("필수 컬럼 탐지 실패: {0}".format(cols), file=sys.stderr)
        return 2
    with conn.cursor() as cur:
        cur.execute("select distinct {d} from market_data order by {d} desc limit %s".format(
            d=cols["date"]), (a.backfill or 1,))
        dates = [r[0] for r in cur.fetchall()]
    added = 0
    for d in sorted(dates):
        m = session_metrics(conn, cols, d)
        if not m.get("scored"):
            continue
        prev = records.get(m["session"])
        if prev:                     # 보존할 사후 필드 유지
            for k in ("next_session", "next_chg_median_pct"):
                if k in prev:
                    m[k] = prev[k]
        m["regime"] = regime_label(m)
        records[m["session"]] = m
        added += 1
    fill_next_session(records)
    write_ledger(records)
    print(json.dumps({"recorded": added, "ledger_sessions": len(records),
                      "latest": records[sorted(records)[-1]] if records else None},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

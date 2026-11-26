#!/usr/bin/env python3
"""분봉(minute_bars) 결손 상시 감시 — '그날 분봉이 실제로 다 들어왔는가'(읽기 전용).

WHY (실측 2026-11-23 23:15): KIS 분봉 수집 크론(`/home/jhshi/cron/kis_minute.sh`, 평일 23:00)이
자격증명 거부(EGW00103)로 300종목 **전부 실패**(bars=0)했는데 프로세스가 0 으로 끝나
`cron_kis_minute.log` 에는 "KIS 수집 완료"만 남았고 자기신고 행도 없었다. KIS 당일분봉 API 는
**그날 안에만** 데이터를 주므로 11-23 분봉은 영구 결손이다(11-24 부터는 정상 117,300행).
아무 지표도 울리지 않았고, 3일 뒤 이 검사로 처음 드러났다.

WHY 일봉 검사로는 못 잡는다: `scripts/data_gap.py check`(R35)는 `market_data`(KRX 일봉)만 본다.
분봉은 별도 경로(서비스 kis-collector)라 그 검사의 시야 밖이고, 소비자는
`KisMinuteProvider`(daytrading_performance 30분 가격·intraday features)다 — 결손이면 그 세션의
분봉 기반 측정이 조용히 비거나 부분값이 된다.

판정(수치만):
  · 결손(missing)  = 그 세션 minute_bars 행 0
  · 부분(partial)  = 행은 있는데 종목수 < 100 또는 종목당 평균 봉수 < 200
                     (정상 실측 = 300종목 × 391봉 = 117,300행, 종목당 391봉)
  · 대상(창)       = `market_data` 에서 거래량>0 인 최근 20세션 중, 기준선(JUDGE_FROM) 이후이면서
                     분봉 담당 시각이 지난 것만
                     (세션 S 의 수집은 S 23:00 시작 → 약 4시간 → S+1 04:00 부터 판정 가능)

종료코드: 0 = 결함 0 / 1 = 결함 있음 / 2 = 측정 불가(DB 오류).
Stdout 의 **마지막 수치 하나**가 결함 세션 수다(구동기 eval_check 규칙).

기준선(JUDGE_FROM=2026-11-24): 그 전 세션은 XR26(30봉 절단)·11-23 AppKey 거부로 이미 결손이며
복구 불가하다 — 영구 결함을 매 틱 다시 세면 '고칠 수 없는 check' 가 된다(문턱 규칙 위반).
그 이전 세션은 표에 '기준선 이전'으로만 남기고 판정에서 뺀다.

복구: 결손 세션은 원천 API 가 당일분만 주므로 **복구 불가**다(Naver 백필은 close 만 담는다 —
`minute_close_bars`). 이 검사의 목적은 다음 결손을 같은 날 잡는 것이다.
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, time as dtime, timedelta

WINDOW_SESSIONS = int(os.environ.get("MBG_WINDOW_SESSIONS") or 20)
JUDGE_FROM = datetime.strptime(os.environ.get("MBG_JUDGE_FROM") or "2026-11-24", "%Y-%m-%d").date()
DUE_HOUR, DUE_MIN = 4, 0
PARTIAL_MIN_STOCKS = 100
PARTIAL_MIN_BARS_PER_STOCK = 200
log = logging.getLogger("minute_bar_gap_check")


def _conn():
    import psycopg2
    host = os.environ.get("POSTGRES_HOST") or "127.0.0.1"
    port = os.environ.get("POSTGRES_PORT") or "5434"
    if host == "postgres" and not os.path.exists("/.dockerenv"):
        host, port = "127.0.0.1", os.environ.get("POSTGRES_HOST_PORT", "5434")
    return psycopg2.connect(
        host=host, port=int(port),
        dbname=os.environ.get("POSTGRES_DB") or "stock_trading",
        user=os.environ.get("POSTGRES_USER") or "stock_user",
        password=os.environ.get("POSTGRES_PASSWORD") or "stock_password",
        connect_timeout=10,
    )


def due_at(session):
    """세션 S 의 분봉이 판정 가능해지는 시각 = S+1 04:00 (23:00 시작 + 약 4시간)."""
    day = session + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return datetime.combine(day, dtime(DUE_HOUR, DUE_MIN))


def main():
    now = datetime.now()
    try:
        conn = _conn()
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT trade_date FROM market_data
                 WHERE volume > 0 AND trade_date < CURRENT_DATE
                 GROUP BY trade_date ORDER BY trade_date DESC LIMIT %s
                """,
                (WINDOW_SESSIONS,),
            )
            sessions = [r[0] for r in cur.fetchall()]
            if not sessions:
                print("측정 불가: market_data 에 거래 세션이 없다")
                return 2
            cur.execute(
                """
                SELECT trade_date, count(*) AS rows_, count(DISTINCT stock_code) AS stocks
                  FROM minute_bars WHERE trade_date = ANY(%s)
                 GROUP BY trade_date
                """,
                (sessions,),
            )
            stats = {r[0]: (int(r[1]), int(r[2])) for r in cur.fetchall()}
        conn.close()
    except Exception as e:
        log.error("DB 조회 실패: %s: %s", type(e).__name__, e)
        print(f"측정 불가: DB 조회 실패 ({type(e).__name__}: {e})")
        return 2

    legacy = [s for s in sessions if s < JUDGE_FROM]
    judged = [s for s in sessions if s >= JUDGE_FROM and now >= due_at(s)]
    print(f"[분봉 결손 검사] {now:%Y-%m-%d %H:%M} · 창 {len(sessions)}세션 중 판정 {len(judged)}세션")
    missing, partial = [], []
    for s in sorted(judged):
        rows_, stocks = stats.get(s, (0, 0))
        per_stock = (rows_ / stocks) if stocks else 0.0
        if rows_ == 0:
            verdict = "결손"
            missing.append(s)
        elif stocks < PARTIAL_MIN_STOCKS or per_stock < PARTIAL_MIN_BARS_PER_STOCK:
            verdict = "부분"
            partial.append(s)
        else:
            verdict = "정상"
        print(f"  {s}  rows={rows_:>7,}  stocks={stocks:>4}  종목당={per_stock:6.1f}봉  {verdict}")
    for s in sorted(sessions):
        if s in legacy:
            print(f"  {s}  (기준선 이전 — XR26 30봉 절단/11-23 결손, 복구 불가)")
        elif s not in judged:
            print(f"  {s}  (판정 전 — 분봉 수집 담당 시각 {due_at(s):%m-%d %H:%M} 이후 판정)")
    defects = len(missing) + len(partial)
    print(f"결함 {defects}세션 (결손 {len(missing)} · 부분 {len(partial)})")
    print(defects)
    return 0 if defects == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

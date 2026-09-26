#!/usr/bin/env python3
"""naver_daily_backfill — Naver 일봉 API 로 market_data 과거 이력을 백필한다.

WHY (2026-09-26 실측, L5 근본원인 + 사용자 승인 ①)
  market_data 가 **2025-06-16 ~ 2026-09-23 (314거래일)** 뿐이라 두 곳이 막혀 있었다:
    ① 팩터 백테스트: 유니버스 필터의 min_listing_days=252 가 2026-04 이전 구간에서 전 종목을 탈락
       (깔때기 실측: 시총 1878 → 거래대금 862 → PIT재무 830 → **상장 0**)
    ② ML 모델: 학습 창·walk-forward 폴드 수가 같은 314거래일에 묶여 있다
  재무제표는 2023~2026 4년치가 이미 있는데 **가격만 1년**이라, 가격만 채우면 둘 다 풀린다.

왜 Naver 인가 (실측 2026-09-26)
  `https://api.finance.naver.com/siseJson.naver?symbol=005930&requestType=1&startTime=...&endTime=...`
  → 1종목 1년치가 **0.098초 / 15KB** 로 반환된다(키 불필요). 종목당 1콜로 전 구간을 받을 수 있어
  KIS 일봉(호출 제한·토큰 이슈)보다 압도적으로 빠르다. 4,340종목 × 1콜 ≈ 36분(0.5s 간격).

설계
  - 멱등: ON CONFLICT (stock_code, trade_date) DO NOTHING (UNIQUE 제약 확인됨).
  - trading_value 는 Naver 응답에 없다 → **close × volume 근사**로 채우고 주석에 한계를 남긴다.
    (유니버스 필터의 avg_trading_value 조건이 이 컬럼을 쓰므로 NULL 로 두면 또 막힌다.)
  - 체크포인트: 종목 인덱스를 state 파일에 저장 → 중단 후 이어서.
  - 예의: 0.3~0.6초 지터, 429/5xx 백오프, 연속 실패 시 중단.
  - 자기신고: dq_claim.record_claim (소스 수신/파서 생성/실제 삽입 3분리).

사용
  cd /home/jhshi/analyist_dd && set -a && . ./.env && set +a
  export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434 PROJ_DIR=/home/jhshi/analyist_dd
  /usr/bin/python3 scripts/naver_daily_backfill.py --start 2023-01-01 --end 2025-06-15 --limit 3 --dry-run
  /usr/bin/python3 scripts/naver_daily_backfill.py --start 2023-01-01 --end 2025-06-15
"""

import argparse
import json
import os
import random
import re
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import psycopg2          # noqa: E402
import requests         # noqa: E402

PG = dict(host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
          port=int(os.environ.get("POSTGRES_PORT", "5434")),
          dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
          user=os.environ.get("POSTGRES_USER", "stock_user"),
          password=os.environ.get("POSTGRES_PASSWORD", ""))

URL = "https://api.finance.naver.com/siseJson.naver"
ROW = re.compile(r'\[\s*"(\d{8})"\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,'
                 r'\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)')
STATE_DEFAULT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "data/naver_backfill_state.json")


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def fetch_day(code, start, end, retries=3):
    """종목 1개의 일봉을 받아 [(code, date, o, h, l, c, vol, value), ...] 반환."""
    params = {"symbol": code, "requestType": 1, "startTime": start, "endTime": end, "timeframe": "day"}
    r = None
    for attempt in range(retries):
        try:
            r = requests.get(URL, params=params, timeout=20,
                             headers={"User-Agent": "Mozilla/5.0 (research; analyist_dd)"})
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            break
        except requests.RequestException as exc:
            if attempt == retries - 1:
                raise
            log(f"  {code} 재시도 {attempt + 1}: {type(exc).__name__}")
            time.sleep(3 * (attempt + 1))
    if r is None:
        # 모든 시도가 429/5xx 였던 경우 — r 이 할당되지 않았으므로 명시적으로 실패시킨다
        # (그냥 진행하면 r.text 에서 NameError 로 죽는다: 린트가 잡은 실제 결함).
        raise RuntimeError(f"{code}: 응답 없음(재시도 {retries}회 모두 429/5xx)")
    rows = []
    for m in ROW.finditer(r.text):
        d, o, h, l, c, v = m.groups()
        try:
            o, h, l, c, v = float(o), float(h), float(l), float(c), float(v)
        except ValueError:
            continue
        if c <= 0:
            continue
        rows.append((code, f"{d[:4]}-{d[4:6]}-{d[6:]}", o, h, l, c, int(v), c * v))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--end", default="2025-06-15", help="기존 이력 시작(2025-06-16) 직전까지")
    ap.add_argument("--limit", type=int, default=0, help="처리 종목 수 제한(0=전체)")
    ap.add_argument("--codes", default="", help="특정 종목만(콤마)")
    ap.add_argument("--max-calls", type=int, default=20000)
    ap.add_argument("--sleep", type=float, default=0.45)
    ap.add_argument("--state-file", default=STATE_DEFAULT)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    t0 = time.time()

    s, e = a.start.replace("-", ""), a.end.replace("-", "")
    conn = psycopg2.connect(**PG)
    cur = conn.cursor()

    if a.codes:
        codes = [c.strip() for c in a.codes.split(",") if c.strip()]
    else:
        # 이미 market_data 가 있는 종목만 대상(불필요한 호출 방지) — stocks 와 FK 정합.
        cur.execute("""SELECT s.stock_code FROM stocks s
                       WHERE EXISTS (SELECT 1 FROM market_data m WHERE m.stock_code = s.stock_code)
                       ORDER BY s.stock_code""")
        codes = [r[0] for r in cur.fetchall()]
    if a.limit:
        codes = codes[:a.limit]

    start_idx = 0
    if os.path.exists(a.state_file):
        try:
            with open(a.state_file, encoding="utf-8") as f:
                st = json.load(f)
            if st.get("start") == a.start and st.get("end") == a.end:
                start_idx = int(st.get("idx", 0))
        except (OSError, json.JSONDecodeError, ValueError):
            pass
    log(f"대상 {len(codes)}종목 / {a.start}~{a.end} / 시작 인덱스 {start_idx} / dry_run={a.dry_run}")

    cur.execute("SELECT COUNT(*) FROM market_data")
    before = cur.fetchone()[0]
    calls = src = parsed = inserted = failed = 0

    for i in range(start_idx, len(codes)):
        code = codes[i]
        if calls >= a.max_calls:
            log(f"예산 소진({calls}콜) — 다음 실행에서 이어서")
            break
        try:
            rows = fetch_day(code, s, e)
            calls += 1
        except Exception as exc:      # noqa: BLE001
            failed += 1
            calls += 1
            log(f"  {code} 실패: {type(exc).__name__}: {str(exc)[:60]}")
            if failed >= 20:
                log("연속 실패 20건 — 중단")
                break
            time.sleep(1.0)
            continue
        src += 1
        parsed += len(rows)
        if rows and not a.dry_run:
            cur.executemany(
                """INSERT INTO market_data (stock_code, trade_date, open_price, high_price,
                                            low_price, close_price, volume, trading_value)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (stock_code, trade_date) DO NOTHING""", rows)
            conn.commit()
            inserted += max(cur.rowcount, 0)
        if i % 200 == 0 or i == len(codes) - 1:
            log(f"  {i + 1}/{len(codes)} {code}: {len(rows)}행 (누적 삽입 {inserted}, 호출 {calls})")
        # ⚠ dry-run 은 체크포인트를 **전진시키지 않는다** — 실측: dry-run 이 idx 를 올려
        #    이어지는 실 실행이 전부 건너뛰었다(0콜). 검증과 실행을 섞지 않는다.
        if not a.dry_run:
            with open(a.state_file, "w", encoding="utf-8") as f:
                json.dump({"start": a.start, "end": a.end, "idx": i + 1,
                           "ts": datetime.now().isoformat()}, f)
        time.sleep(a.sleep + random.uniform(0, 0.15))

    cur.execute("SELECT COUNT(*) FROM market_data")
    after = cur.fetchone()[0]
    delta = after - before
    log(f"완료: 호출 {calls} / 소스 응답 {src}종목 / 파서 행 {parsed} / 실제 삽입 {inserted} / "
        f"테이블 델타 {delta} ({before}→{after}) / 실패 {failed} / {time.time() - t0:.0f}s")
    if src > 0 and parsed == 0:
        log("★ 소스는 응답했는데 파서가 0행 — 응답 형식 변경 의심(파서 점검 필요)")

    if not a.dry_run:
        try:
            from dq_claim import record_claim
            record_claim(conn, "naver_daily_backfill", "market_data",
                         claimed_rows=parsed, persisted_rows=delta, source_rows=src,
                         note=f"window={a.start}~{a.end} calls={calls} inserted={inserted}")
            conn.commit()
        except Exception as exc:      # noqa: BLE001
            log(f"자기신고 생략: {exc}")
    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

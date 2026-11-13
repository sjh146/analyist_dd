#!/usr/bin/env python3
"""R41 — 시장패턴 원장의 세션 기록이 '그 세션을 다 본 값'인지 검사한다(읽기 전용).

WHY (실측 2026-11-13 19:41): `scripts/market_pattern_ledger.py --today`(크론 19:40)는 DB에
그 시점 적재된 봉만으로 세션 지표를 기록한다. 그런데 18:55 KRX OpenAPI 가 이름해석 실패로
중단되면 디스패처가 KIS 폴백(종목당 1콜 ≈ 3.5시간)으로 넘어가고, 19:40 시점 DB 에는 시장의
**21%** 만 있다 → 원장에 `scored=819 / trade_value 12.25조 / regime=flat_choppy` 가 **정상값처럼**
기록됐다(전일 3828 / 22.53조 / broad_up). 이 값은 어떤 결함 표시도 없이 소비자(CEO·엔지니어)에게
'그날 시장'으로 읽히고, 다음 세션 수익률(`next_chg_median_pct`) 계산의 기준도 오염시킨다.

검사: 원장 마지막 세션의 `scored` 를 **지금 DB 의 그 세션 봉수(거래량>0)** 와 비교해
10% 이상 모자라면 결함 1(미달)로 센다. 적재가 끝난 뒤 `market_pattern_ledger.py --today` 를
다시 돌리면 같은 세션을 덮어써(멱등) 0 이 된다 — '고치면 통과'하는 check 다.

종료코드: 0 = 결함 0 / 1 = 결함 있음 / 2 = 판정 불가(원장 없음·DB 조회 실패).
"""
from __future__ import annotations

import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(REPO, "data", "reports", "market_pattern_ledger.jsonl")
TOLERANCE = 0.10  # DB 봉수의 10% 까지는 정상(상장폐지·거래정지·신규상장 차이)


def last_record(path=LEDGER):
    if not os.path.exists(path):
        return None
    rec = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
    return rec


def db_rows(session):
    """그 세션의 '마감된 봉'(거래량>0) 수 — 미완성/무거래 봉은 분모에서 뺀다."""
    sys.path.insert(0, os.path.join(REPO, "scripts"))
    import psycopg2  # noqa: E402

    host = os.environ.get("POSTGRES_HOST", "127.0.0.1")
    port = os.environ.get("POSTGRES_PORT", "5434")
    conn = psycopg2.connect(
        host=host, port=port,
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
        connect_timeout=10,
    )
    try:
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM market_data WHERE trade_date=%s AND volume>0", (session,))
        n = int(cur.fetchone()[0])
        cur.close()
    finally:
        conn.close()
    return n


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    path = LEDGER
    if "--ledger" in argv:
        path = argv[argv.index("--ledger") + 1]
    rec = last_record(path)
    if rec is None:
        print("R41: 판정 불가 — 원장 없음", file=sys.stderr)
        return 2
    session = rec.get("session")
    scored = rec.get("scored")
    if session is None or scored is None:
        print(f"R41: 판정 불가 — 마지막 레코드에 session/scored 없음 ({session}/{scored})", file=sys.stderr)
        return 2
    try:
        db = db_rows(session)
    except Exception as e:  # noqa: BLE001
        print(f"R41: 판정 불가 — DB 조회 실패({type(e).__name__}: {e})", file=sys.stderr)
        return 2
    if db <= 0:
        print(f"R41: 판정 불가 — {session} 봉이 DB 에 없음", file=sys.stderr)
        return 2
    await_ratio = scored / db
    defect = 1 if scored < db * (1 - TOLERANCE) else 0
    print(f"R41: {session} scored={scored} db_volume_gt0={db} ratio={await_ratio:.3f} "
          f"(허용 ≥{1 - TOLERANCE:.2f}) → {'미달' if defect else '충족'} "
          f"| 공백 {defect}건")
    return 0 if defect == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""R48 — KRX OpenAPI 가 **직전 거래일 봉**을 언제부터 주는지 시각별로 실측한다.

WHY (2026-11-23 21:1x 실측): 18:55 `krx_daily.sh` 는 `end = today - 1` 로 잘라 돌기 때문에
DB 의 market_data 는 **항상 하루 뒤진다**(11-23 21:10 현재 최신 11-20). 그런데 같은 시각
API 에 **당일(11-23)** 을 물으면 0행, **11-20** 은 935행(KOSPI)이 온다 → 당일봉 미발행이
소스 한계인 것은 확인됐다. 남은 질문은 **T-1 봉이 아침 몇 시부터 발행되는가** 이다.
그 답이 '07:30 이전'이면 08:40 피드 발행 전에 T-1 봉을 넣을 수 있어(로더 1회 추가) 신호가
하루 신선해지고, '장 마감 후 늦게'면 지금 구조가 최선이다.

문턱 기본 500행/마켓 — 실측 2026-11-20: KOSPI 935행 · KOSDAQ 1,836행.
읽기 전용 · 호출 2회(마켓별 1회) · DB 쓰기 없음. 마지막 줄에 숫자 하나(가용 마켓 수)를
찍어 `researcher_cycle` 의 check 가 그대로 판정한다.
"""
import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import krx_daily as k  # noqa: E402

MIN_ROWS = int(os.environ.get("R48_MIN_ROWS", "500"))


def last_session(today, holidays):
    """오늘 이전의 마지막 평일(휴장 파일 제외)."""
    d = today - timedelta(days=1)
    for _ in range(14):
        if d.weekday() < 5 and d.isoformat() not in holidays:
            return d
        d -= timedelta(days=1)
    return d


def main():
    key = os.environ.get("KRX_API_KEY", "").strip()
    if not key:
        print("R48: KRX_API_KEY 미설정 — 판단 불가")
        print(0)
        return 1
    holidays = set(k.load_json(k.HOLIDAY_PATH, []))
    today = date.today()
    target = last_session(today, holidays)
    base = k.resolve_base(key)
    out = {"probed_at": today.isoformat(), "target_session": target.isoformat(),
           "min_rows": MIN_ROWS, "markets": {}}
    ok = 0
    for mk, path in k.MARKETS.items():
        rows = k.fetch_day(base, key, path, target.strftime("%Y%m%d")) or []
        n = len(rows)
        out["markets"][mk] = n
        if n >= MIN_ROWS:
            ok += 1
    print(json.dumps(out, ensure_ascii=False))
    print(f"R48 직전거래일({target}) 봉 가용 마켓수 {ok} (문턱 마켓당 {MIN_ROWS}행)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

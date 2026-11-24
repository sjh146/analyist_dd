"""close 피드 후보의 signal_date 나이를 세는 읽기 전용 프로브(T49).

WHY(트레이더 환류 2026-11-23): 발행본 close 후보 20건 중 3건이 84~300일 낡은 signal_date 를
달고 나가 소비자 후보별 신선도 게이트(close 4일)에서 즉시 차단됐다. 스크리너 단위 lag 프로브
(`feed_signal_lag`)는 **다수값**만 보므로 소수 이상치를 못 잡는다 — 후보 단위로 세야 한다.

Stdout 의 **마지막 수치**가 결함 건수다(구동기 eval_check 가 마지막 수치를 check_value 로 읽는다).
"""
import datetime
import json
import os
import sys

PROJ = os.environ.get("PROJ_DIR", "/home/jhshi/analyist_dd")
FEED = os.path.join(PROJ, "data", "feed", "screener_latest.json")
MAX_AGE_DAYS = int(os.environ.get("T49_MAX_AGE_DAYS", "4"))
SCREENER = os.environ.get("T49_SCREENER", "close")


def main():
    with open(FEED, encoding="utf-8") as f:
        feed = json.load(f)
    gen = datetime.date.fromisoformat(str(feed["generated_at"])[:10])
    items = ((feed.get("candidates") or {}).get(SCREENER) or {}).get("items") or []
    stale, unknown = [], []
    for c in items:
        raw = c.get("signal_date")
        if not raw:
            unknown.append(c)
            continue
        try:
            age = (gen - datetime.date.fromisoformat(str(raw)[:10])).days
        except ValueError:
            unknown.append(c)
            continue
        if age > MAX_AGE_DAYS:
            stale.append((c, age))
    print(f"T49: {SCREENER} 후보 {len(items)}건 · 기준일 {gen} · 문턱 {MAX_AGE_DAYS}일")
    print(f"T49: signal_date 없음/오형식 {len(unknown)}건")
    for c, age in stale:
        print(f"  낡음 {c.get('stock_code')} signal_date={c.get('signal_date')} ({age}일)")
    print(len(stale))
    return 0


if __name__ == "__main__":
    sys.exit(main())

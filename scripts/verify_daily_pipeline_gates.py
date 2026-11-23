#!/usr/bin/env python3
"""일일 파이프라인 관문 점검 — 오늘 배선한 4개 경로가 '실제 행'으로 돌았는지 확인한다.

WHY (2026-11-21 배선): 배선만 하고 실발행을 안 본 상태가 이 스택의 반복 함정이다
(자기신고만 남기고 침묵하는 크론 러너 — R27). 2026-11-21 에 넣은 네 가지를 매일 한 줄씩 판정한다:

  G1 daytrading 후보 생성   : feed candidates.daytrading items > 0            (cron 09:10/12:10/14:10)
  G2 close 상한가 제외      : reports/close_latest.json 의 screener_meta 기록 (cron 14:40)
  G3 피드 신선도            : close/swing items > 0 · generated_at 이 최근 거래일 (cron 08:40/20:30)
  G4 당일 일봉 재시도       : 재시도 로그에 그날 판정 줄                     (cron 22:35)

판정은 로그 문구가 아니라 **산출물·행수**로 한다(로그는 자기신고일 뿐이다).
주말·휴장일이면 전 관문이 '거래일 아님'으로 침묵한다.

사용:
  python3 scripts/verify_daily_pipeline_gates.py             # 최근 거래일 기준
  python3 scripts/verify_daily_pipeline_gates.py --date 2026-11-20
  python3 scripts/verify_daily_pipeline_gates.py --json
종료코드: 0 = 전부 통과(또는 휴장), 2 = 하나 이상 FAIL(래퍼가 알림 판단에 쓴다).
"""
import argparse
import glob
import json
import os
import re
import sys
from datetime import date, datetime, timedelta

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEED = os.path.join(PROJ, "data", "feed", "screener_latest.json")
CLOSE_LATEST = os.path.join(PROJ, "reports", "close_latest.json")
DAYTRADING_LOG = os.path.join(PROJ, "reports", "cron_daytrading_pipeline.log")
RETRY_LOG = os.path.join(PROJ, "reports", "cron_same_day_bars_retry.log")
HOLIDAYS = os.path.join(PROJ, "data", "krx_holidays.json")


def load_json(p):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def holidays():
    d = load_json(HOLIDAYS)
    if isinstance(d, list):
        return set(d)
    if isinstance(d, dict):
        return set(d.get("days", []))
    return set()


def last_trading_day(today=None):
    """오늘(또는 지정일) 이전의 최근 거래일 — 주말·휴장 제외."""
    d = today or date.today()
    hol = holidays()
    for _ in range(15):
        if d.weekday() < 5 and d.isoformat() not in hol:
            return d
        d -= timedelta(days=1)
    return d


def log_has(path, day):
    """로그에 그 날짜(YYYY-MM-DD) 줄이 있는지."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return any(day in ln for ln in f)
    except OSError:
        return False


def gate_daytrading(day):
    feed = load_json(FEED) or {}
    items = ((feed.get("candidates") or {}).get("daytrading") or {}).get("items") or []
    gen = str(feed.get("generated_at") or "")
    fresh = gen[:10] >= day
    ok = len(items) > 0 and fresh
    return {
        "gate": "G1 daytrading 후보 생성",
        "ok": ok,
        "detail": f"items={len(items)} generated_at={gen or '없음'}"
                  + ("" if fresh else " (기준일 이전 발행)"),
        "how": "cron 09:10/12:10/14:10 · reports/cron_daytrading_pipeline.log",
    }


def gate_close_limit_up(day):
    d = load_json(CLOSE_LATEST) or {}
    meta = d.get("screener_meta") if isinstance(d, dict) else None
    ok = isinstance(meta, dict) and isinstance(meta.get("excluded_limit_up"), int)
    detail = (f"screener_meta={'있음' if ok else '없음'} "
              f"excluded_limit_up={(meta or {}).get('excluded_limit_up')} "
              f"max_day_change={(meta or {}).get('max_day_change')} "
              f"date={d.get('date')}")
    return {
        "gate": "G2 close 상한가 제외 메타",
        "ok": ok,
        "detail": detail,
        "how": "cron 14:40 close_job → run_close_job.attach_screener_meta (R26)",
    }


def gate_feed_freshness(day):
    feed = load_json(FEED) or {}
    cand = feed.get("candidates") or {}
    counts = {k: len((cand.get(k) or {}).get("items") or []) for k in ("close", "swing")}
    gen = str(feed.get("generated_at") or "")
    ok = all(v > 0 for v in counts.values()) and gen[:10] >= day
    return {
        "gate": "G3 피드 신선도(close/swing)",
        "ok": ok,
        "detail": f"{counts} generated_at={gen or '없음'}",
        "how": "cron 08:40/20:30 feed_publish (원천은 런타임 산출물 — R31)",
    }


def gate_retry_log(day):
    ok = log_has(RETRY_LOG, day)
    return {
        "gate": "G4 당일 일봉 재시도 판정",
        "ok": ok,
        "detail": ("로그에 판정 줄 있음" if ok else "그날 판정 줄 없음"),
        "how": "cron 22:35 same_day_bars_retry (R37) · reports/cron_same_day_bars_retry.log",
    }


def gate_signal_dates_trading_day(day):
    """G5 — 발행된 모든 전략의 signal_date 가 **거래일**인가.

    WHY(2026-11-23 실측): job-runner 컨테이너 TZ 가 UTC 라 swing 잡의 `date.today()` 가
    월요일 08:30 실행에서 일요일(11-22)을 찍었다 → 비거래일 signal_date 가 피드로 나갔다.
    거래일 판정은 주말 + data/krx_holidays.json 으로 하고, 값이 하나라도 비거래일이면 FAIL.
    """
    feed = load_json(FEED) or {}
    cand = feed.get("candidates") or {}
    bad, seen = [], {}
    for strat, blob in cand.items():
        items = (blob or {}).get("items") or []
        for it in items:
            sd = str(it.get("signal_date") or "")
            if not sd:
                continue
            seen.setdefault(strat, sd)
            try:
                y, m, d = (int(x) for x in sd.split("-")[:3])
                dt = date(y, m, d)
            except (ValueError, TypeError):
                bad.append(f"{strat}:{sd}(형식불가)")
                continue
            if dt.weekday() >= 5 or sd in holidays():
                bad.append(f"{strat}:{sd}(비거래일)")
    ok = not bad
    return {
        "gate": "G5 signal_date 거래일",
        "ok": ok,
        "detail": (f"전략별 {seen}" if ok else f"비거래일 {sorted(set(bad))[:5]} / 전략별 {seen}"),
        "how": "job-runner 잡의 signal_date 계산(최근 거래일) · 20:30/08:40 발행 후 확인",
    }


def main():
    ap = argparse.ArgumentParser(description="일일 파이프라인 관문 점검")
    ap.add_argument("--date", default=None, help="기준 거래일 YYYY-MM-DD (기본: 최근 거래일)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    today = date.today()
    if args.date:
        y, m, d = (int(x) for x in args.date.split("-"))
        day = date(y, m, d)
    else:
        day = today if (today.weekday() < 5 and today.isoformat() not in holidays()) \
            else last_trading_day(today)
    day_s = day.isoformat()

    # 오늘 밤 22:35 관문이 아직 안 돈 시각이면 그 관문은 '미도래'로 라벨한다(거짓 FAIL 방지).
    gates = [gate_daytrading(day_s), gate_close_limit_up(day_s), gate_feed_freshness(day_s),
             gate_signal_dates_trading_day(day_s)]
    now = datetime.now()
    retry_due = day == today and now.hour >= 23  # 22:35 실행 + 수집 시간 여유
    g4 = gate_retry_log(day_s)
    if day == today and not retry_due and not g4["ok"]:
        g4["ok"] = True
        g4["detail"] = "미도래(22:35 이전 실행 아님)"
        g4["pending"] = True
    gates.append(g4)

    failed = [g for g in gates if not g["ok"]]
    if args.json:
        print(json.dumps({"date": day_s, "gates": gates, "failed": len(failed)},
                         ensure_ascii=False, indent=1))
    else:
        print(f"[{now.strftime('%F %H:%M')}] 관문 점검 기준일 {day_s}")
        for g in gates:
            mark = "OK  " if g["ok"] else "FAIL"
            extra = " (미도래)" if g.get("pending") else ""
            print(f"  {mark} {g['gate']}: {g['detail']}{extra}")
            if not g["ok"]:
                print(f"       → {g['how']}")
        print(f"  결과: {len(gates) - len(failed)}/{len(gates)} 통과")
    return 0 if not failed else 2


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""close 경로 이론 성과 실측 — 종가 스크리너 후보의 '다음 세션 시가 갭' (매 거래일 09:20).

왜 필요한가: HEAT 상한(+15%)이 close 진입을 막았을 때 그 결정이 옳았는지는
"그 후보를 종가에 사서 다음 시가에 팔았으면 얼마였나"로만 판정된다.
차단 당일의 등락(실측 2026-10-02 +1.97%)은 **이미 지나간 이동**이라 답이 아니다 —
close 프로파일의 진입은 종가 동시호가 직전이므로 그 상승은 우리 것이 아니다.

누적 기록: `data/reports/close_path_gap_history.jsonl` (세션당 1줄, 회의록 되돌림 조건의 근거).
휴장일에는 새 거래일 행이 없으므로 **조용히 종료**한다(출력 없음 → 크론 침묵).

사용: python3 scripts/close_path_gap_probe.py [--feed <path>]
"""
import argparse
import datetime as dt
import glob
import json
import os
import re
import subprocess
import sys

REPO = "/home/jhshi/analyist_dd"
HIST = os.path.join(REPO, "data", "reports", "close_path_gap_history.jsonl")
CURL = "/mnt/c/Windows/System32/curl.exe"


def naver_daily(code, days=14):
    """최근 일봉 (날짜, 시가, 고가, 저가, 종가) 리스트 — Windows curl 경유(프록시 회피)."""
    end = dt.date.today().strftime("%Y%m%d")
    start = (dt.date.today() - dt.timedelta(days=days * 2)).strftime("%Y%m%d")
    url = (f"https://api.finance.naver.com/siseJson.naver?symbol={code}"
           f"&requestType=1&startTime={start}&endTime={end}&timeframe=day")
    try:
        r = subprocess.run([CURL, "-s", "--noproxy", "*", "-m", "15", url],
                           capture_output=True, text=True, timeout=30)
        out = (r.stdout or "")
    except Exception:  # noqa: BLE001
        return []
    rows = re.findall(r'\["(\d{8})",\s*([\d.]+),\s*([\d.]+),\s*([\d.]+),\s*([\d.]+)', out)
    return [(d, float(o), float(h), float(lo), float(c)) for d, o, h, lo, c in rows]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--feed", default=os.path.join(REPO, "data", "feed", "screener_latest.json"))
    a = ap.parse_args()

    try:
        feed = json.load(open(a.feed, encoding="utf-8"))
        items = feed["candidates"]["close"]["items"]
    except Exception as e:  # noqa: BLE001
        print(f"[close-gap] 피드 읽기 실패: {e}")
        return 0
    if not items:
        return 0

    signal_date = str(items[0].get("signal_date") or "")
    gaps, session, missing = [], None, []
    for it in items:
        code = str(it["stock_code"])
        rows = naver_daily(code)
        if len(rows) < 2:
            missing.append(code)
            continue
        d_prev, _, _, _, c_prev = rows[-2]
        d_today, o_today, _, _, _ = rows[-1]
        # 새 세션(= signal_date 이후 거래일)이 생겼을 때만 측정한다
        sig = signal_date.replace("-", "")
        if not (d_today > sig and d_prev <= sig):
            continue
        session = d_today
        gaps.append({"code": code, "name": it.get("stock_name"), "score": it.get("score"),
                     "prev_close": c_prev, "open": o_today,
                     "gap_pct": round((o_today / c_prev - 1) * 100, 2)})
    if not gaps:
        return 0  # 휴장·신규 세션 없음 → 침묵

    g = sorted(x["gap_pct"] for x in gaps)
    rec = {"ts": dt.datetime.now().isoformat(timespec="seconds"), "signal_date": signal_date,
           "session": session, "n": len(gaps), "missing": missing,
           "avg_gap_pct": round(sum(g) / len(g), 2), "median_gap_pct": g[len(g) // 2],
           "up": sum(1 for x in g if x > 0), "down": sum(1 for x in g if x <= 0),
           "items": sorted(gaps, key=lambda x: -x["gap_pct"])}
    os.makedirs(os.path.dirname(HIST), exist_ok=True)
    with open(HIST, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    hist = [json.loads(l) for l in open(HIST, encoding="utf-8") if l.strip()]
    cum = [h["avg_gap_pct"] for h in hist]
    print(f"[close경로 갭 {rec['session']}] n={rec['n']} 평균 {rec['avg_gap_pct']:+.2f}% "
          f"(중앙 {rec['median_gap_pct']:+.2f}%) 상승 {rec['up']}/{rec['n']} · "
          f"누적 {len(cum)}세션 평균 {sum(cum)/len(cum):+.2f}% — 표본 {len(cum)}/5세션"
          + (f" · 조회실패 {len(missing)}건" if missing else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())

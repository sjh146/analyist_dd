#!/usr/bin/env python3
"""net_guard 경보 — 차단·예산 소진·가드 오류를 조용할 때 침묵하고 문제일 때만 알린다.

WHY: 가드가 차단을 감지해도 **아무도 안 보면** 스택은 조용히 데이터를 잃는다(수집기는 중단되고,
크론 로그에는 '완료'처럼 남는다). 이 틱은 상태·이벤트를 읽어 ①현재 쿨다운 중인 호스트
②예산 소진/임박 ③최근 24시간 차단·전송오류 ④가드 자체 오류를 한 줄씩 보고한다.

종료코드: 문제 없음 0(출력 없음) / 경보 2 — 틱 래퍼가 '변화가 있을 때만' Discord 로 전달한다.

사용:
    python3 scripts/net_guard_alert.py                 # 최근 24시간 창
    python3 scripts/net_guard_alert.py --window-hours 6
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import net_guard  # noqa: E402


def read_events(window_hours: float) -> list:
    if not os.path.exists(net_guard.EVENTS):
        return []
    since = dt.datetime.now() - dt.timedelta(hours=window_hours)
    out = []
    for line in open(net_guard.EVENTS, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
            ts = dt.datetime.fromisoformat(ev.get("ts", ""))
        except ValueError:
            continue
        if ts >= since:
            out.append(ev)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="수집 호출 정책(net_guard) 경보")
    ap.add_argument("--window-hours", type=float, default=24.0)
    a = ap.parse_args(argv)

    now = dt.datetime.now()
    lines, alarm = [], False

    # 1) 현재 쿨다운 중인 호스트
    for name in sorted(os.listdir(net_guard.STATE_DIR)) if os.path.isdir(net_guard.STATE_DIR) else []:
        if not name.endswith(".json"):
            continue
        st = net_guard._roll_read(os.path.join(net_guard.STATE_DIR, name))
        key = st.get("key") or name[:-5]
        until = float(st.get("blocked_until", 0) or 0)
        if until > now.timestamp():
            left = (until - now.timestamp()) / 60
            alarm = True
            lines.append(f"★차단 중 [{key}] {st.get('blocked_reason', '')[:90]} "
                         f"(해제까지 {left:.0f}분)")
        budget = st.get("budget")
        calls = int(st.get("calls", 0) or 0)
        if budget and calls >= budget * 0.9:
            alarm = True
            lines.append(f"예산 임박 [{key}] {calls}/{budget}콜")
        if st.get("guard_error"):
            alarm = True
            lines.append(f"가드 오류 [{key}] {str(st.get('guard_error'))[:90]}")

    # 2) 최근 이벤트 집계
    evs = read_events(a.window_hours)
    blocked = [e for e in evs if e.get("event") == "blocked"]
    errors = [e for e in evs if e.get("event") == "guard_error"]
    transient = [e for e in evs if e.get("event") == "transient"]
    if blocked:
        alarm = True
        by_key: dict = {}
        for e in blocked:
            by_key.setdefault(e.get("key", "?"), []).append(e.get("reason", "")[:70])
        for k, rs in by_key.items():
            lines.append(f"최근 {a.window_hours:.0f}h 차단 {len(rs)}회 [{k}] — {rs[-1]}")
    if errors:
        alarm = True
        lines.append(f"가드 오류 {len(errors)}건 — 최근: {errors[-1].get('error', '')[:90]}")
    if transient:
        lines.append(f"일시오류(재시도로 흡수) {len(transient)}건")

    if not lines:
        return 0
    head = f"[수집 호출 정책] {now.strftime('%m-%d %H:%M')}"
    print(head)
    for l in lines:
        print(f"  · {l}")
    print("  (해제: python3 scripts/net_guard.py clear --key <키> --note '사유')")
    return 2 if alarm else 0


if __name__ == "__main__":
    sys.exit(main())

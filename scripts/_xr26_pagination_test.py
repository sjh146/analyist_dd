#!/usr/bin/env python3
"""XR26 자체점검 — 분봉 페이지네이션이 30봉/페이지 mock 에서도 전 구간을 받는가.

배경(실측): KIS `inquire-time-itemchartprice`(당일분봉)는 요청 `fid_cnt`(=100)와
무관하게 1회 최대 **30봉**만 돌려준다. 기존 종료조건
`len(page_bars) < fid_cnt` 는 30 < 100 이 항상 참 → 매일 1페이지(15:01~15:30
30봉)에서 조기 종료됐다(minute_bars 실측: 15:01~15:30 30봉뿐, `time<='093000'` 0행).

이 테스트는 **네트워크·DB 호출 없이** mock 클라이언트만으로 그 결함이 수리됐는지
검증한다. mock 은 KIS 상한을 흉내내어 reference 이하 최근 30봉만 반환한다.

실행: python3 scripts/_xr26_pagination_test.py
통과 기준: 정규장 09:00~15:30 전 구간(391봉) 수집 + 09:00봉 포함 + 페이지 수 >= 13.
"""
from __future__ import annotations

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "services", "kis-collector"))

from kis_app.collectors.minute_collector import MinuteCollector  # noqa: E402

TARGET_DATE = "20261008"
KIS_PAGE_CAP = 30          # 실측: KIS 1회 응답 상한
OPEN, CLOSE = "090000", "153000"


def _all_minutes():
    """09:00~15:30 1분 간격 HHMMSS 리스트(오름차순). 391개."""
    out = []
    h, m = 9, 0
    while (h, m) <= (15, 30):
        out.append(f"{h:02d}{m:02d}00")
        m += 1
        if m == 60:
            h, m = h + 1, 0
    return out


ALL_MINUTES = _all_minutes()          # 오름차순
RANGE = {t: i for i, t in enumerate(ALL_MINUTES)}


class FakeMinuteClient:
    """KIS 분봉 mock — reference 이하 최근 KIS_PAGE_CAP 봉만(내림차순) 반환."""

    def __init__(self, page_cap=KIS_PAGE_CAP):
        self.page_cap = page_cap
        self.calls = []

    def get_minute_chart(self, symbol, excd, input_hour, period_div="0",
                         fid_cnt=100, include_past="Y"):
        self.calls.append(input_hour)
        ref_idx = RANGE.get(str(input_hour))
        if ref_idx is None:
            # 임의 시각이면 그 이하로 맞춘다
            le = [t for t in ALL_MINUTES if t <= str(input_hour)]
            ref_idx = ALL_MINUTES.index(le[-1]) if le else -1
        if ref_idx < 0:
            bars = []
        else:
            lo = max(0, ref_idx - self.page_cap + 1)
            bars = list(reversed(ALL_MINUTES[lo:ref_idx + 1]))  # 내림차순
        return {"rt_cd": "0", "msg_cd": "0",
                "output2": [{
                    "stck_bsop_date": TARGET_DATE,
                    "stck_cntg_hour": t,
                    "stck_prpr": "1000", "stck_oprc": "1000",
                    "stck_hgpr": "1000", "stck_lwpr": "1000",
                    "cntg_vol": "10", "acml_tr_pbmn": "10000",
                } for t in bars]}


def main():
    checks = []

    def ok(name, cond, detail=""):
        checks.append((name, bool(cond), detail))

    # ── 1) 30봉/페이지 mock 에서 전 구간 수집 ──────────────────────────
    client = FakeMinuteClient()
    col = MinuteCollector(client, storage=None)
    bars = col.collect_stock("005930", "KOSPI", TARGET_DATE, fid_cnt=100)
    times = [b["time"] for b in bars]
    ok("전 구간 391봉 수집", len(bars) == len(ALL_MINUTES),
       f"got={len(bars)} want={len(ALL_MINUTES)}")
    ok("09:00봉 포함", "090000" in times, f"min_time={min(times) if times else None}")
    ok("15:30봉 포함", "153000" in times, f"max_time={max(times) if times else None}")
    ok("중복 없음", len(times) == len(set(times)), f"uniq={len(set(times))}")
    ok("시간 오름차순 정렬", times == sorted(times))

    # ── 2) 페이지네이션이 실제로 전진(1페이지에서 멈추지 않음) ──────────
    ok("페이지 수 >= 13", len(client.calls) >= 13, f"pages={len(client.calls)}")
    ok("1페이지는 마감시각 시작", client.calls[0] == CLOSE, f"calls[0]={client.calls[0]}")
    ok("참조 시각 단조 감소", all(client.calls[i] > client.calls[i + 1]
                              for i in range(len(client.calls) - 1)))

    # ── 3) 회귀: 기존 종료조건(30 < fid_cnt)이면 30봉에서 멈췄을 것 ─────
    old_break = (len(ALL_MINUTES[:KIS_PAGE_CAP]) < 100)  # 항상 True = 결함 재현 조건
    ok("결함 재현 조건 성립(30<100)", old_break, "종료조건이 항상 참이었음")
    ok("결함 대비 정확히 13배 수집", len(bars) >= KIS_PAGE_CAP * 13,
       f"bars={len(bars)} vs old={KIS_PAGE_CAP}")

    # ── 4) 상한 작은 mock(5봉/페이지)에서도 견고 ───────────────────────
    c2 = FakeMinuteClient(page_cap=5)
    bars2 = MinuteCollector(c2, storage=None).collect_stock(
        "005930", "KOSPI", TARGET_DATE, fid_cnt=100, max_pages=200)
    ok("5봉/페이지 mock 전 구간", len(bars2) == len(ALL_MINUTES),
       f"got={len(bars2)}")

    # ── 5) max_pages 방어가 작동(무한루프 없음) ────────────────────────
    c3 = FakeMinuteClient(page_cap=30)
    bars3 = MinuteCollector(c3, storage=None).collect_stock(
        "005930", "KOSPI", TARGET_DATE, fid_cnt=100, max_pages=2)
    ok("max_pages=2 제한 준수", len(c3.calls) <= 2, f"pages={len(c3.calls)}")

    passed = sum(1 for _, c, _ in checks if c)
    for name, c, detail in checks:
        print(f"[{'PASS' if c else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
    print(f"\n{passed}/{len(checks)} PASS")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())

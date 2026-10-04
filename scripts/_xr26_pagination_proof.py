#!/usr/bin/env python3
"""XR26 오프라인 재현·수리 증명 — 분봉 페이지네이션 조기종료 (KIS 호출 없음).

WHY (2026-10-05, 엔지니어)
- DB 실측: minute_bars = 45,120행 · 6거래일(2026-09-23~10-02) · 301종목 · time 15:01~15:30
  (distinct time = 30) · **time <= '093000' 행 0개** → 종목당 하루 30봉만 적재된다.
- 코드 실측: `kis_client.get_minute_chart` 는 `fid_cnt` 인자를 **받기만 하고 params 에 넣지 않는다**
  (services/kis-collector/kis_app/client/kis_client.py:392-410) → KIS 당일분봉조회는 1회 30봉 상한으로
  응답한다. 그런데 `MinuteCollector.collect_stock` 의 종료조건은
  `if len(page_bars) < int(fid_cnt) or oldest == MARKET_OPEN_TIME: break` 이고 fid_cnt 기본값이 100
  (minute_collector.py:90, :23) → **첫 페이지(30봉)에서 항상 break** 한다.
  게다가 `MAX_PAGES_DEFAULT = 10`(minute_collector.py:24)은 '100건×4페이지' 가정 위에 세워져 있어
  30봉/페이지에서는 391봉을 담을 수 없다(10×30=300봉 → 10:02 이후만).
- 이 스크립트는 **그들의 실제 모듈을 import** 해서 mock 클라이언트로 그 두 결함을 재현하고,
  상수만 바꾼 호출로 전 구간(391봉)이 담기는 것을 증명한다. 실 KIS 호출·DB 쓰기 없음(읽기 전용 실행).

사용: python3 scripts/_xr26_pagination_proof.py
"""
from __future__ import annotations

import importlib.util
import sys
import types

REPO = "/home/jhshi/analyist_dd"
COLLECTOR = f"{REPO}/services/kis-collector"
MOD_PATH = f"{COLLECTOR}/kis_app/collectors/minute_collector.py"

MARKET_OPEN = "090000"
MARKET_CLOSE = "153000"
TARGET_DATE = "20260929"
STOCK = "033780"

# ── 정규장 1분봉 총 개수 (09:00~15:30 포함) ────────────────────────────────
def _expected_full_bars() -> int:
    def to_min(s):
        return int(s[:2]) * 60 + int(s[2:4])
    return to_min(MARKET_CLOSE) - to_min(MARKET_OPEN) + 1


def _load_minute_collector():
    """kis_app 패키지를 import 하되, 무거운 의존성은 건드리지 않는다."""
    if COLLECTOR not in sys.path:
        sys.path.insert(0, COLLECTOR)
    try:
        from kis_app.collectors.minute_collector import MinuteCollector  # noqa: WPS433
        import kis_app.collectors.minute_collector as mod
        return mod, MinuteCollector, "kis_app import"
    except Exception as e:  # noqa: BLE001 — 폴백: 최소 스텁으로 모듈만 로드
        reason = f"{type(e).__name__}: {e}"
        pkg = types.ModuleType("kis_app")
        pkg.__path__ = [f"{COLLECTOR}/kis_app"]
        sys.modules["kis_app"] = pkg
        coll = types.ModuleType("kis_app.collectors")
        coll.__path__ = [f"{COLLECTOR}/kis_app/collectors"]
        sys.modules["kis_app.collectors"] = coll
        utils = types.ModuleType("kis_app.utils")

        def add_minutes(hhmmss, delta):
            t = int(hhmmss[:2]) * 60 + int(hhmmss[2:4]) + int(delta)
            return f"{t // 60:02d}{t % 60:02d}00"

        utils.add_minutes = add_minutes
        utils.to_date = lambda s: s
        utils.to_float = lambda v: None if v in (None, "") else float(v)
        utils.to_int = lambda v: None if v in (None, "") else int(float(v))
        sys.modules["kis_app.utils"] = utils
        daily = types.ModuleType("kis_app.collectors.daily_collector")
        daily.market_to_excd = lambda m: {"KOSPI": "J", "KOSDAQ": "K"}.get(m, "J")
        sys.modules["kis_app.collectors.daily_collector"] = daily
        spec = importlib.util.spec_from_file_location("kis_app.collectors.minute_collector", MOD_PATH)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod, mod.MinuteCollector, f"스텁 폴백 ({reason})"


class MockKisClient:
    """KIS 당일분봉조회 흉내 — **input_hour 기준 과거로 최대 api_max 봉**만 돌려준다.

    실제 KIS 는 fid_cnt 를 보내지 않으면 30봉 상한으로 응답한다(그리고 실제 클라이언트가 보내지
    않는다). `sent_fid_cnt` 를 기록해 '파라미터가 전달되지 않음'을 증거로 남긴다.
    """

    def __init__(self, api_max=30):
        self.api_max = api_max
        self.calls = 0
        self.sent_fid_cnt = []

    def get_minute_chart(self, symbol, excd, input_hour, period_div="0",
                         fid_cnt=100, include_past="Y"):
        self.calls += 1
        self.sent_fid_cnt.append(fid_cnt)
        def to_min(s):
            return int(s[:2]) * 60 + int(s[2:4])
        out = []
        t = to_min(input_hour)
        floor = to_min(MARKET_OPEN)
        while t >= floor and len(out) < self.api_max:
            hhmmss = f"{t // 60:02d}{t % 60:02d}00"
            out.append({
                "stck_bsop_date": TARGET_DATE, "stck_cntg_hour": hhmmss,
                "stck_prpr": "10000", "stck_oprc": "10000", "stck_hgpr": "10010",
                "stck_lwpr": "9990", "cntg_vol": "100", "acml_tr_pbmn": "1000000",
            })
            t -= 1
        return {"output2": out}


class _Storage:
    def get_universe(self):
        return [(STOCK, "KOSPI")]


def main() -> int:
    mod, Collector, how = _load_minute_collector()
    full = _expected_full_bars()
    print(f"[XR26] 모듈 로드: {how}")
    print(f"[XR26] 상수 실측: FID_CNT_DEFAULT={mod.FID_CNT_DEFAULT} · "
          f"MAX_PAGES_DEFAULT={mod.MAX_PAGES_DEFAULT}")
    print(f"[XR26] 정규장 1분봉 기대 개수 = {full}")
    print()

    results = []

    # ── 케이스 A: 현행 기본값 그대로 → 결함 재현 ──────────────────────────
    cA = MockKisClient(api_max=30)
    barsA = Collector(cA, _Storage()).collect_stock(STOCK, "KOSPI", TARGET_DATE)
    timesA = sorted(b["time"] for b in barsA)
    print(f"A) 현행 기본값(fid_cnt={mod.FID_CNT_DEFAULT}, max_pages={mod.MAX_PAGES_DEFAULT})")
    print(f"   → 봉 {len(barsA)}개 · {timesA[0]}~{timesA[-1]} · KIS 호출 {cA.calls}회 · "
          f"클라이언트가 받은 fid_cnt={cA.sent_fid_cnt}")
    okA = (len(barsA) == 30 and timesA[0] == "150100")
    results.append(("A 현행 = 30봉만(15:01~15:30) 재현", okA))

    # ── 케이스 B: 상수만 수리(fid_cnt=30, max_pages=20) → 전 구간 ──────────
    cB = MockKisClient(api_max=30)
    barsB = Collector(cB, _Storage()).collect_stock(
        STOCK, "KOSPI", TARGET_DATE, fid_cnt=30, max_pages=20)
    timesB = sorted(b["time"] for b in barsB)
    n_open = sum(1 for t in timesB if t <= "093000")
    print(f"B) 수리(fid_cnt=30, max_pages=20)")
    print(f"   → 봉 {len(barsB)}개 · {timesB[0]}~{timesB[-1]} · KIS 호출 {cB.calls}회 · "
          f"09:30 이전 {n_open}봉")
    okB = (len(barsB) == full and timesB[0] == MARKET_OPEN and n_open > 0)
    results.append((f"B 수리 = 전 구간 {full}봉(09:00~15:30) 수집", okB))

    # ── 케이스 C: fid_cnt 만 고치고 max_pages 를 두면 → 여전히 잘린다 ──────
    cC = MockKisClient(api_max=30)
    barsC = Collector(cC, _Storage()).collect_stock(
        STOCK, "KOSPI", TARGET_DATE, fid_cnt=30, max_pages=mod.MAX_PAGES_DEFAULT)
    timesC = sorted(b["time"] for b in barsC)
    print(f"C) fid_cnt=30 이지만 max_pages={mod.MAX_PAGES_DEFAULT} 유지")
    print(f"   → 봉 {len(barsC)}개 · {timesC[0]}~{timesC[-1]} · KIS 호출 {cC.calls}회")
    okC = len(barsC) == 30 * mod.MAX_PAGES_DEFAULT and len(barsC) < full
    results.append((f"C max_pages={mod.MAX_PAGES_DEFAULT} 로는 {full}봉 미달(상향 필요)", okC))

    # ── 케이스 D: 마지막 페이지가 짧게 오는 경우에도 무한루프 없이 종료 ────
    class ShortLastPage(MockKisClient):
        def get_minute_chart(self, *a, **k):
            if self.calls >= 5:      # 5페이지(150봉)를 채운 뒤 빈 응답을 준다
                self.calls += 1
                return {"output2": []}
            return super().get_minute_chart(*a, **k)

    cD = ShortLastPage(api_max=30)
    barsD = Collector(cD, _Storage()).collect_stock(
        STOCK, "KOSPI", TARGET_DATE, fid_cnt=30, max_pages=20)
    print(f"D) 5페이지 뒤 빈 응답 → 중단 확인: 봉 {len(barsD)}개 · 호출 {cD.calls}회")
    okD = len(barsD) == 150 and cD.calls == 6
    results.append(("D 빈 페이지에서 무한루프 없이 종료(150봉·6콜)", okD))

    # ── 케이스 E: 커버리지 배수 ─────────────────────────────────────────────
    gain = full / 30.0
    print(f"E) 커버리지: 30봉 → {full}봉 = {gain:.1f}배")
    results.append(("E 커버리지 13배(30→391)", abs(gain - 13.03) < 0.1))

    print()
    n_fail = 0
    for name, ok in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        n_fail += 0 if ok else 1
    print()
    print(f"[XR26] {len(results) - n_fail}/{len(results)} PASS")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())

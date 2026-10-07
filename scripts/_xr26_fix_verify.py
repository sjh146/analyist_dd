#!/usr/bin/env python3
"""XR26 수리 패치 검증 — `data/reports/xr26_minute_pagination_fix.patch` (실 KIS 호출·DB 쓰기 없음).

WHY (2026-10-08, 엔지니어)
- `services/kis-collector/kis_app/collectors/minute_collector.py` 는 수집기 소유라 이 역할이
  직접 고칠 수 없다(다른 역할 소유 파일 수정 금지). 승인 즉시 적용할 수 있도록 **정확한 diff**를
  만들어 두고, 그 패치가 (a) 현 파일에 문제없이 적용되고 (b) 기본 경로에서 결함을 실제로 고치는지를
  오프라인으로 증명한다.
- 결함: `collect_stock` 종료조건 `len(page_bars) < int(fid_cnt)` 인데 KIS 당일분봉조회는 fid_cnt 를
  무시하고 30행 고정 응답 → 첫 페이지에서 항상 break. `MAX_PAGES_DEFAULT=10` 도 30행/페이지
  기준으로는 부족(391분 ÷ 30 = 13.03페이지).
- 수리: ① 상수 FID_CNT_DEFAULT 30 · MAX_PAGES_DEFAULT 14 ② 종료 판정을 '요청값'이 아니라
  '직전 페이지 실측 크기(page_size)'로.

사용: python3 scripts/_xr26_fix_verify.py
"""
from __future__ import annotations

import importlib.util
import pathlib
import shutil
import subprocess
import sys
import tempfile
import types

REPO = pathlib.Path(__file__).resolve().parents[1]
REL = "services/kis-collector/kis_app/collectors/minute_collector.py"
SRC = REPO / REL
PATCH = REPO / "data/reports/xr26_minute_pagination_fix.patch"

MARKET_OPEN = "090000"
MARKET_CLOSE = "153000"
TARGET_DATE = "20260929"
STOCK = "033780"


def expected_full_bars() -> int:
    def to_min(s):
        return int(s[:2]) * 60 + int(s[2:4])
    return to_min(MARKET_CLOSE) - to_min(MARKET_OPEN) + 1


def load_module(mod_path: pathlib.Path):
    """minute_collector 를 스텁 의존성과 함께 로드한다(_xr26_pagination_proof.py 와 같은 방식).

    컨테이너/호스트 어디서든 무거운 kis_app 의존성(news/DB 등)을 건드리지 않기 위해
    kis_app.utils·daily_collector 만 최소 스텁으로 채운다.
    """
    pkg = types.ModuleType("kis_app")
    pkg.__path__ = [str(mod_path.parents[2])]
    sys.modules["kis_app"] = pkg
    coll = types.ModuleType("kis_app.collectors")
    coll.__path__ = [str(mod_path.parent)]
    sys.modules["kis_app.collectors"] = coll
    utils = types.ModuleType("kis_app.utils")

    def add_minutes(hhmmss, delta):
        t = int(hhmmss[:2]) * 60 + int(hhmmss[2:4]) + int(delta)
        return f"{t // 60:02d}{t % 60:02d}00"

    setattr(utils, "add_minutes", add_minutes)
    setattr(utils, "to_date", lambda s: s)
    setattr(utils, "to_float", lambda v: None if v in (None, "") else float(v))
    setattr(utils, "to_int", lambda v: None if v in (None, "") else int(float(v)))
    sys.modules["kis_app.utils"] = utils
    daily = types.ModuleType("kis_app.collectors.daily_collector")
    setattr(daily, "market_to_excd", lambda m: {"KOSPI": "J", "KOSDAQ": "K"}.get(m, "J"))
    sys.modules["kis_app.collectors.daily_collector"] = daily
    spec = importlib.util.spec_from_file_location("kis_app.collectors.minute_collector", str(mod_path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def make_client(api_max: int):
    """KIS 당일분봉조회 흉내 — input_hour 기준 과거로 api_max 봉만 돌려준다."""

    class MockClient:
        def __init__(self):
            self.api_max = api_max
            self.calls = 0

        def get_minute_chart(self, symbol, excd, input_hour, period_div="0",
                             fid_cnt=100, include_past="Y"):
            self.calls += 1
            def to_min(s):
                return int(s[:2]) * 60 + int(s[2:4])
            out, t, floor = [], to_min(input_hour), to_min(MARKET_OPEN)
            while t >= floor and len(out) < self.api_max:
                out.append({
                    "stck_bsop_date": TARGET_DATE,
                    "stck_cntg_hour": f"{t // 60:02d}{t % 60:02d}00",
                    "stck_prpr": "10000", "stck_oprc": "10000", "stck_hgpr": "10010",
                    "stck_lwpr": "9990", "cntg_vol": "100", "acml_tr_pbmn": "1000000",
                })
                t -= 1
            return {"output2": out}

    return MockClient()


class _Storage:
    def get_universe(self):
        return [(STOCK, "KOSPI")]


def main() -> int:
    full = expected_full_bars()
    results = []

    if not PATCH.exists():
        print(f"[FAIL] 패치 파일 없음: {PATCH}")
        return 1

    # 1) 현 파일에 클린 적용되는가
    r = subprocess.run(["git", "apply", "--check", "-p1", str(PATCH)],
                       cwd=str(REPO), capture_output=True, text=True)
    results.append(("현 파일에 git apply --check 통과(승인 즉시 적용 가능)", r.returncode == 0))
    if r.returncode != 0:
        print(r.stderr)

    # 2) 현행(미적용) 모듈은 결함이 그대로인가 — 기본 인자로 30봉
    mod_now = load_module(SRC)
    c0 = make_client(30)
    bars_now = mod_now.MinuteCollector(c0, _Storage()).collect_stock(STOCK, "KOSPI", TARGET_DATE)
    t_now = sorted(b["time"] for b in bars_now)
    print(f"[현행] 기본값 수집 = {len(bars_now)}봉 · {t_now[0]}~{t_now[-1]} · 호출 {c0.calls}회")
    results.append(("현행: 기본 경로가 30봉(15:01~15:30)만 — 결함 재현", len(bars_now) == 30))

    # 3) 패치 적용 사본 로드 — 기본 인자만으로 전 구간이 담기는가
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="xr26verify_"))
    try:
        dst = tmp / REL
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SRC, dst)
        ap = subprocess.run(["patch", "-p1", "-s", "-i", str(PATCH)], cwd=str(tmp),
                            capture_output=True, text=True)
        results.append(("패치가 수집기 사본에 조용히 적용", ap.returncode == 0))
        if ap.returncode != 0:
            print(ap.stdout, ap.stderr)
            raise SystemExit(1)

        # 모듈 캐시를 비우고 패치 사본 로드
        for k in [k for k in list(sys.modules) if k.startswith("kis_app")]:
            del sys.modules[k]
        mod_fix = load_module(dst)
        print(f"[수리] 상수 = FID_CNT_DEFAULT={mod_fix.FID_CNT_DEFAULT} · "
              f"MAX_PAGES_DEFAULT={mod_fix.MAX_PAGES_DEFAULT}")

        c1 = make_client(30)   # 실제 KIS: 30행/페이지
        bars1 = mod_fix.MinuteCollector(c1, _Storage()).collect_stock(STOCK, "KOSPI", TARGET_DATE)
        t1 = sorted(b["time"] for b in bars1)
        n_open = sum(1 for t in t1 if t <= "093000")
        print(f"[수리] 기본값 수집 = {len(bars1)}봉 · {t1[0]}~{t1[-1]} · 호출 {c1.calls}회 · "
              f"09:30 이전 {n_open}봉")
        results.append((f"수리: 기본 경로 = 전 구간 {full}봉(09:00~15:30)", len(bars1) == full))
        results.append(("수리: 09:00 개장봉 포함(현재 0행)", t1[0] == MARKET_OPEN and n_open > 0))
        results.append(("수리: 호출 14회 = ceil(391/30) 페이지네이션 전진", c1.calls == 14))

        # 4) 페이지 크기가 30이 아닌 경우(비정형)도 전 구간을 담는다
        c2 = make_client(57)
        bars2 = mod_fix.MinuteCollector(c2, _Storage()).collect_stock(STOCK, "KOSPI", TARGET_DATE)
        t2 = sorted(b["time"] for b in bars2)
        print(f"[수리] 57행/페이지 비정형 = {len(bars2)}봉 · {t2[0]}~{t2[-1]} · 호출 {c2.calls}회")
        results.append(("수리: 페이지 크기 57 에서도 전 구간(요청값 하드코딩 아님)", len(bars2) == full))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    n_fail = 0
    for name, ok in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        n_fail += 0 if ok else 1
    print()
    print(f"[XR26-FIX] {len(results) - n_fail}/{len(results)} PASS")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())

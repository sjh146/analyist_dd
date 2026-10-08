#!/usr/bin/env python3
"""L5c 자체점검 — 팩터 유니버스 필터의 PIT 수리 전/후 대조 (한 파일에서).

무엇을 검증하나
  ① 현행 universe.py 는 asof 를 반영하지 않는다(= '현재값' 조회 누수) — 같은 입력에서
     서로 다른 asof 두 날짜가 **동일한 유니버스**를 만든다(누수 재현).
  ② 패치본은 asof 에 따라 유니버스가 달라지고, 그 집합은 현행의 **부분집합**이다
     (미래 유동성 종목이 과거 시점에서 제외된다 = 낙관 편향 제거).
  ③ 스토리지가 PIT 게터를 안 가진 경우(기존 tests/test_universe.py 의 MockStorage 스텁)
     패치본은 **종전과 동일**하게 동작한다(하위호환 — 기존 테스트 회귀 방지).

실행(어디서든)
  python3 scripts/_l5c_pit_verify.py            # 호스트
  docker exec -e PYTHONPATH=/app stock_xgboost_ml python /app/scripts/_l5c_pit_verify.py
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SA = os.path.join(PROJ, "services", "strategy-agents")
UNIVERSE_REL = "services/strategy-agents/app/factors/universe.py"
UNIVERSE_SRC = os.path.join(PROJ, UNIVERSE_REL)
PATCH_NAME = "l5c_pit_universe.patch"


def find_patch():
    """패치 파일 위치 — 컨테이너(/app)에는 data/reports 가 마운트되지 않으므로 후보를 넓힌다."""
    cands = [os.environ.get("L5C_PATCH"),
             os.path.join(PROJ, "data", "reports", PATCH_NAME),
             os.path.join(PROJ, "app", "reports", PATCH_NAME),
             os.path.join(os.path.dirname(PROJ), "data", "reports", PATCH_NAME)]
    for c in cands:
        if c and os.path.exists(c):
            return c
    return os.path.join(PROJ, "data", "reports", PATCH_NAME)


PATCH = find_patch()

# tests/test_universe.py 는 pytest 를 import 만 하고 쓰지 않는다(이 스택엔 pytest 가 없다).
# 직접 호출 러너에서 import 단계가 죽지 않도록 최소 스텁을 등록한다.
if "pytest" not in sys.modules:
    import types as _types
    _pytest_stub = _types.ModuleType("pytest")
    _pytest_stub.mark = lambda *a, **k: (lambda f: f)  # type: ignore[attr-defined]
    _pytest_stub.raises = None  # type: ignore[attr-defined]
    _pytest_stub.fixture = lambda *a, **k: (lambda f: f)  # type: ignore[attr-defined]
    sys.modules["pytest"] = _pytest_stub

# app.factors.financial_snapshot 을 import 하려면 strategy-agents 루트가 sys.path 에 있어야 한다.
if SA not in sys.path:
    sys.path.insert(0, SA)

CASES = []
FAILS = []


def check(name, cond, detail=""):
    CASES.append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name, (" — " + detail) if detail else ""))
    if not cond:
        FAILS.append(name)


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, path
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def apply_patch_to_temp():
    """패치를 임시 사본에 적용해 '패치본' 파일 경로를 만든다(대상 repo 파일은 건드리지 않는다)."""
    tmp = tempfile.mkdtemp(prefix="l5c_verify_")
    rel = os.path.join(tmp, "services", "strategy-agents", "app", "factors")
    os.makedirs(rel, exist_ok=True)
    dst = os.path.join(rel, "universe.py")
    shutil.copyfile(UNIVERSE_SRC, dst)
    r = subprocess.run(["patch", "-p1", "--forward", "--silent", "-i", PATCH],
                       cwd=tmp, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("patch 적용 실패: %s\n%s" % (r.stdout, r.stderr))
    return dst, tmp


# --- 누수 모형 스토리지 ------------------------------------------------------------
CAP_NOW = {"A": 1e11, "B": 1e11, "C": 6e10, "D": 1e11}
PX_NOW = {"A": 10000.0, "B": 10000.0, "C": 10000.0, "D": 10000.0}
PX_ASOF = {"A": 10000.0, "B": 5000.0, "C": 5000.0, "D": 10000.0}
TV_NOW = {"A": 2e9, "B": 2e9, "C": 2e9, "D": 2e9}
# B 는 과거에 거래대금이 얇았고, C 는 과거 시총이 하한 미달이다(둘 다 '미래 유동성' 종목).
TV_ASOF = {
    "2025-12-30": {"A": 2e9, "B": 0.3e9, "C": 2e9, "D": 2e9},
    "2026-06-30": {"A": 2e9, "B": 2e9, "C": 2e9, "D": 2e9},
}
FIN = {"report_date": "2000-01-01", "revenue": 100}
STOCKS = [{"stock_code": c, "market": "KOSPI"} for c in ("A", "B", "C", "D")]
D1, D2 = "2025-12-30", "2026-06-30"


class PITStorage:
    """PIT 게터(거래대금 asof·가격 asof)를 가진 스토리지 — 실제 PostgresStorage 모양."""

    def get_market_caps(self):
        return dict(CAP_NOW)

    def get_avg_trading_value(self, code, days=30):
        return TV_NOW.get(code)

    def get_avg_trading_value_asof(self, code, days=30, asof_date=None):
        return TV_ASOF.get(str(asof_date), TV_NOW).get(code)

    def get_price_series_asof(self, code, days=1, asof_date=None):
        px = PX_ASOF.get(code)
        return [px] if px is not None else []

    def get_latest_price(self, code):
        return PX_NOW.get(code)

    def get_financial_statements(self, code, asof_date=None):
        return [dict(FIN, stock_code=code)]

    def get_first_trade_date(self, code):
        return "2000-01-02"


class TvOnlyStorage(PITStorage):
    """거래대금 asof 게터만 있고 가격 PIT 게터가 없는 스토리지(None 속성)."""

    get_price_series_asof = None
    get_latest_price = None


class LegacyStub:
    """기존 tests/test_universe.py 의 MockStorage 와 동일한 표면(가격·asof 게터 없음)."""

    def get_market_caps(self):
        return dict(CAP_NOW)

    def get_avg_trading_value(self, code, days=30):
        return TV_NOW.get(code)

    def get_financial_statements(self, code, asof_date=None):
        return [dict(FIN, stock_code=code)]

    def get_first_trade_date(self, code):
        return "2000-01-02"


def run(mod, storage, asof):
    return set(mod.filter_universe(storage, list(STOCKS), asof_date=asof))


def s(x):
    return "[" + ", ".join(sorted(x)) + "]"


def run_legacy_suite(patched_mod):
    """기존 tests/test_universe.py 를 패치본 universe 로 치환해 직접 실행한다(pytest 불필요).

    반환: (total, ok, fails). 테스트가 요구하는 스토리지 스텁에는 PIT 게터가 없으므로
    패치본의 폴백 경로가 그대로 쓰인다 = '하위호환 회귀 없음'의 실증.
    """
    import types

    for pkg, pkgpath in (("app", os.path.join(SA, "app")),
                         ("app.factors", os.path.join(SA, "app", "factors"))):
        if pkg not in sys.modules:
            m = types.ModuleType(pkg)
            m.__path__ = [pkgpath]
            sys.modules[pkg] = m
    saved = sys.modules.get("app.factors.universe")
    sys.modules["app.factors.universe"] = patched_mod
    try:
        tpath = os.path.join(SA, "tests", "test_universe.py")
        spec = importlib.util.spec_from_file_location("l5c_legacy_test_universe", tpath)
        assert spec is not None and spec.loader is not None, tpath
        tm = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tm)
        total = ok = 0
        fails = []
        for cname in dir(tm):
            cls = getattr(tm, cname)
            if not (isinstance(cls, type) and cname.startswith("Test")):
                continue
            inst = cls()
            for meth in dir(inst):
                if not meth.startswith("test_"):
                    continue
                total += 1
                try:
                    getattr(inst, meth)()
                    ok += 1
                except Exception as e:  # noqa: BLE001
                    fails.append("%s.%s: %s" % (cname, meth, e))
        return total, ok, fails
    finally:
        if saved is not None:
            sys.modules["app.factors.universe"] = saved
        else:
            sys.modules.pop("app.factors.universe", None)


def main() -> int:
    print("[L5c] 팩터 유니버스 PIT 수리 자체점검 — 현행 vs 패치본")
    if not os.path.exists(UNIVERSE_SRC):
        print("  [SKIP] 대상 소스가 없음: %s" % UNIVERSE_SRC)
        print("         strategy-agents 소스는 stock_xgboost_ml 컨테이너에 마운트되지 않는다"
              "(마운트: services/xgboost-ml→/app, <repo>/scripts→/app/scripts) → 호스트에서 실행하라.")
        return 2
    if not os.path.exists(PATCH):
        print("  [SKIP] 패치 파일을 찾지 못함: %s" % PATCH)
        print("         컨테이너(/app)에는 data/reports 가 마운트되지 않는다 → 호스트에서 실행하거나")
        print("         docker cp <repo>/data/reports/%s stock_xgboost_ml:/app/app/reports/ 후 재실행."
              % PATCH_NAME)
        return 2

    orig = load_module(UNIVERSE_SRC, "l5c_orig_universe")
    try:
        patched_path, tmp = apply_patch_to_temp()
    except Exception as e:  # noqa: BLE001
        print("  [FAIL] 패치 적용 실패: %s" % e)
        return 1
    patched = load_module(patched_path, "l5c_patched_universe")
    print("  (패치본 로드: %s)" % patched_path)

    # ① 누수 재현 — 현행은 asof 를 바꿔도 같은 집합
    o1, o2 = run(orig, PITStorage(), D1), run(orig, PITStorage(), D2)
    check("① 현행 누수 재현: 서로 다른 asof 두 날짜가 동일 유니버스(853 상수 집합의 축소판)",
          o1 == o2 == {"A", "B", "C", "D"}, "%s == %s" % (s(o1), s(o2)))

    # ② 패치본 — asof 에 따라 달라지고, 현행의 부분집합
    p1, p2 = run(patched, PITStorage(), D1), run(patched, PITStorage(), D2)
    check("② 패치본: asof 에 따라 유니버스가 달라진다(과거 ≠ 최근)",
          p1 != p2, "%s vs %s" % (s(p1), s(p2)))
    check("② 패치본: 현행의 부분집합(누수로 추가되던 종목이 과거에서 제외)",
          p1 <= o1 and p2 <= o2, "%s⊆%s · %s⊆%s" % (s(p1), s(o1), s(p2), s(o2)))
    check("② 패치본: 과거 시점(asof=%s)에서 '미래 유동성' B 제외" % D1, "B" not in p1, s(p1))
    check("② 패치본: 과거 시점(asof=%s)에서 '미래 시총' C 제외" % D1, "C" not in p1, s(p1))
    check("② 패치본: 최근 시점(asof=%s)에서는 B 복귀(PIT 비대칭 확인)" % D2, "B" in p2, s(p2))
    check("② 패치본: 누수로 추가되던 집합 = {B, C}(현행−패치본)",
          (o1 - p1) == {"B", "C"} and (o2 - p2) == {"C"}, "Δ1=%s Δ2=%s" % (s(o1 - p1), s(o2 - p2)))

    # ③ 하위호환 — 기존 test_universe.py 스텁(가격·asof 게터 없음)
    lo, lp = run(orig, LegacyStub(), D1), run(patched, LegacyStub(), D1)
    check("③ 하위호환: PIT 게터 없는 스텁에서 패치본 == 현행(기존 테스트 회귀 없음)",
          lo == lp, "%s == %s" % (s(lo), s(lp)))
    check("③ 하위호환: 스텁 경로에서 asof 를 바꿔도 동일(폴백이 현행과 동일)",
          run(patched, LegacyStub(), D2) == lp, s(lp))

    # ④ 거래대금 asof 게터만 있고 가격 게터는 None — C 는 현재 시총으로 잔류
    q1 = run(patched, TvOnlyStorage(), D1)
    check("④ 가격 PIT 게터가 None 이어도 죽지 않는다(None 을 callable 로 취급)",
          q1 == {"A", "C", "D"}, s(q1))

    # ⑤ 대상 repo 파일 미변경(패치는 산출물일 뿐)
    r = subprocess.run(["git", "-C", PROJ, "diff", "--quiet", "--", UNIVERSE_REL])
    check("⑤ 대상 repo 파일은 수정되지 않았다(universe.py 무변경)", r.returncode == 0, "")

    # ⑥ 패치 클린 적용
    r2 = subprocess.run(["git", "-C", PROJ, "apply", "--check", "-p1", PATCH],
                        capture_output=True, text=True)
    check("⑥ git apply --check -p1 rc=0", r2.returncode == 0, (r2.stderr or "").strip())

    # ⑦ 기존 회귀 테스트(tests/test_universe.py)를 패치본 universe 로 직접 실행 — pytest 불필요
    total, ok, tfails = run_legacy_suite(patched)
    check("⑦ 기존 tests/test_universe.py 를 패치본으로 실행 — %d/%d PASS(회귀 없음)" % (ok, total),
          total > 0 and ok == total, "; ".join(tfails[:3]))

    shutil.rmtree(tmp, ignore_errors=True)
    print("")
    if FAILS:
        print("[L5C-PIT] %d/%d PASS — FAIL: %s" % (len(CASES) - len(FAILS), len(CASES), FAILS))
        return 1
    print("[L5C-PIT] %d/%d PASS" % (len(CASES), len(CASES)))
    print("   → 현행=누수 재현 / 패치본=PIT 정합 / 스텁 폴백=현행 동일(회귀 없음).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

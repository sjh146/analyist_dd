"""feature_survival_report — 합성 패널로 8개 판정 등급 + 멱등을 검증한다.

스펙 docs/spec_feature_survival.md 의 테스트 계약("신규 7개")에 따라:
  ① 전부 NaN → DEAD_ALL_NULL   ② 전부 0 → DEAD_CONST_ZERO   ③ 상수 3.0 → DEAD_CONST_OTHER
  ④ 10%만 값 → DEAD_SPARSE      ⑤ 날짜 내 상수 → CONST_MARKET  ⑥ 종목 고정 → CONST_STOCK
  ⑦ fwd_ret 복사 → LEAK_SUSPECT + ⑧ 정상 난수 → ALIVE (같은 테스트 — 스펙 허용)
  ⑨ 재실행 JSON 바이트 동일 (⑦⑧ 테스트에 병합 — 스펙 헤더 "신규 7개"와 ①~⑨ 항목을 맞추기 위함)

합성 패널 구성: 20종목 × 80날짜 = 1600행, 코드-메이저(종목별 날짜 오름차순 정렬 상태).
fwd 수익률은 스크립트 내부 정의(코드별 정렬 후 h번째 다음 행 pct)와 **같은 식**으로 만들어
⑦의 '피처 = fwd_ret' 가 corr 1.0 이 되게 한다.
④는 스펙 기본 --min-coverage 0.05 로는 10% 커버리지가 sparse 가 아니므로, 스펙 테스트
기대("10%만 값이 있는 피처 → DEAD_SPARSE")대로 compute_survival(min_coverage=0.2) 로
호출한다(CLI 기본값 0.05 는 스펙 그대로 유지 — 판정 임계값 사후 조정 아님).
"""
import json
import os
import sys
from datetime import date, timedelta

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import feature_survival_report as fsr  # noqa: E402

N_CODES = 20
N_DATES = 80
H = 5
SEED = 42

# 실제 달력 날짜 80일 (2026-01-32 같은 가짜 날짜 금지 — 스크립트가 span/나이 계산에 쓴다)
_DATES = [(date(2026, 1, 1) + timedelta(days=d)).isoformat() for d in range(N_DATES)]


def _price2d():
    """종목별 랜덤워크 가격 (N_CODES, N_DATES) float64 — 연속값이라 수익률 0 이 거의 없다."""
    rng = np.random.default_rng(SEED)
    logp = np.cumsum(rng.normal(0.0, 0.02, size=(N_CODES, N_DATES)), axis=1)
    return (np.exp(logp) * 10000.0).astype(np.float64)


def _fwd_ret_flat(price2d):
    """fwd_ret_{t→t+H} = price[t+H]/price[t] - 1 (코드별 블록, 스크립트와 같은 식)."""
    f = np.full_like(price2d, np.nan)
    f[:, :-H] = price2d[:, H:] / price2d[:, :-H] - 1.0
    return f.reshape(-1)


def _panel(features):
    """features: {name: (1600,) 배열} → (X float32, names, dates, codes, price float64)."""
    codes = np.repeat(np.array(["%06d" % c for c in range(N_CODES)]), N_DATES)
    dates = np.tile(np.array(_DATES), N_CODES)
    price = _price2d().reshape(-1)
    names = list(features)
    X = np.column_stack([features[n] for n in names]).astype(np.float32)
    return X, np.array(names), dates, codes, price


def _entry(res, name):
    for f in res["features"]:
        if f["feature"] == name:
            return f
    raise AssertionError("피처 없음: %s" % name)


def test_all_nan_is_dead_all_null():
    v = np.full(N_CODES * N_DATES, np.nan, dtype=np.float64)
    X, names, dates, codes, price = _panel({"f_null": v})
    res = fsr.compute_survival(X, names, dates, codes, price, h=H)
    f = _entry(res, "f_null")
    assert f["class"] == "DEAD_ALL_NULL", f["reasons"]
    assert f["stats"]["null_ratio"] == 1.0
    assert f["leak"]["n"] == 0 and f["leak"]["L1"] is None  # 표본부족 n/a


def test_all_zero_is_dead_const_zero():
    X, names, dates, codes, price = _panel({"f_zero": np.zeros(N_CODES * N_DATES)})
    res = fsr.compute_survival(X, names, dates, codes, price, h=H)
    f = _entry(res, "f_zero")
    assert f["class"] == "DEAD_CONST_ZERO", f["reasons"]  # nunique==1 보다 우선
    assert f["stats"]["nnz_ratio_honest"] == 0.0


def test_const_value_is_dead_const_other():
    X, names, dates, codes, price = _panel({"f_three": np.full(N_CODES * N_DATES, 3.0)})
    res = fsr.compute_survival(X, names, dates, codes, price, h=H)
    f = _entry(res, "f_three")
    assert f["class"] == "DEAD_CONST_OTHER", f["reasons"]
    assert f["stats"]["nunique"] == 1


def test_ten_pct_values_is_dead_sparse():
    # 10% 날짜(처음 8일)에만 랜덤값, 나머지 NaN. 값이 제각각이어야 nunique==1(상수)을 피한다.
    rng = np.random.default_rng(1)
    v = np.full((N_CODES, N_DATES), np.nan, dtype=np.float64)
    v[:, :8] = rng.normal(0.0, 1.0, size=(N_CODES, 8))
    X, names, dates, codes, price = _panel({"f_sparse": v.reshape(-1)})
    # 스펙 테스트 기대(10% → DEAD_SPARSE)는 기본 min-coverage 0.05 로는 성립하지 않으므로
    # 호출 파라미터로 0.2 를 준다 — CLI 기본값 0.05 는 스펙 그대로.
    res = fsr.compute_survival(X, names, dates, codes, price, h=H, min_coverage=0.2)
    f = _entry(res, "f_sparse")
    assert f["class"] == "DEAD_SPARSE", f["reasons"]
    assert f["stats"]["coverage_days"] == 0.1
    assert any("coverage_days" in r for r in f["reasons"])


def test_market_wide_const_is_const_market():
    # 날짜 안에서는 전 종목 동일값, 날짜 간에는 변동 → 횡단면 정보 없음.
    v = np.tile((np.arange(N_DATES) + 1.0) / N_DATES, N_CODES)
    X, names, dates, codes, price = _panel({"f_market": v})
    res = fsr.compute_survival(X, names, dates, codes, price, h=H)
    f = _entry(res, "f_market")
    assert f["class"] == "CONST_MARKET", f["reasons"]
    assert f["stats"]["xsec_constant_ratio"] == 1.0
    assert f["stats"]["time_std"] > 0.0  # 시계열 정보는 있음 → CONST_STOCK 이 아님


def test_stock_fixed_is_const_stock():
    # 종목별 고정값, 날짜 변동 없음 → 일평균이 날짜마다 같아 time_std == 0.
    v = np.repeat(np.arange(N_CODES) + 1.0, N_DATES)
    X, names, dates, codes, price = _panel({"f_stock": v})
    res = fsr.compute_survival(X, names, dates, codes, price, h=H)
    f = _entry(res, "f_stock")
    assert f["class"] == "CONST_STOCK", f["reasons"]
    assert f["stats"]["time_std"] == 0.0
    assert f["stats"]["xsec_constant_ratio"] == 0.0  # 횡단면 정보는 있음


def test_leak_alive_and_idempotent(tmp_path):
    """⑦ fwd_ret 복사 → LEAK_SUSPECT · ⑧ 난수 → ALIVE · ⑨ 재실행 JSON 바이트 동일."""
    price2d = _price2d()
    fwd = _fwd_ret_flat(price2d)
    rng = np.random.default_rng(7)
    rand = rng.normal(0.0, 1.0, size=N_CODES * N_DATES)
    X, names, dates, codes, price = _panel({"f_fwd": fwd, "f_rand": rand})
    res = fsr.compute_survival(X, names, dates, codes, price, h=H)

    f = _entry(res, "f_fwd")
    assert f["class"] == "LEAK_SUSPECT", f["reasons"]
    assert f["leak"]["L1"] is True          # corr(f, fwd_ret) == 1.0 >= 0.90
    assert f["leak"]["n"] >= fsr.LEAK_MIN_N

    g = _entry(res, "f_rand")
    assert g["class"] == "ALIVE", g["reasons"]
    assert g["leak"]["L1"] is False and g["leak"]["L2"] is False

    # ⑨ 멱등: 같은 패널·같은 출력 경로로 두 번 실행 → JSON 바이트 동일(실행 시각 미포함 설계).
    # (스펙 검증의 md5 2회와 같은 방식 — 출력 경로가 다르면 claim.persisted_files 가 달라
    #  정당하게 바이트가 다르므로, 같은 경로에 덮어써 비교한다.)
    npz = tmp_path / "panel.npz"
    np.savez(npz, X=X, feature_names=names, dates=dates, codes=codes, price=price)
    j = tmp_path / "out.json"
    m = tmp_path / "out.md"
    rc = fsr.main(["--panel", str(npz), "--json-out", str(j), "--md-out", str(m), "--quiet"])
    assert rc == 0
    first = j.read_bytes()
    rc = fsr.main(["--panel", str(npz), "--json-out", str(j), "--md-out", str(m), "--quiet"])
    assert rc == 0
    assert j.read_bytes() == first
    d = json.loads(first.decode("utf-8"))
    assert len(d["features"]) == 2
    assert "generated_at" not in d

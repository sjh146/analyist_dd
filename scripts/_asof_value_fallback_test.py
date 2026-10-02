"""검증: factor_features 의 value_per/value_pbr/quality_roe **결측 대체 계산** 회귀 테스트.

계약(2026-10-02 CG65 후속):
  1) 저장값(per/pbr/roe)이 있으면 **그대로** 쓴다(계산값으로 덮지 않는다).
  2) 저장값이 0/결측이면 as-of 시총 ÷ as-of 재무로 계산해 채운다.
  3) 계산 입력이 없으면 0.0(추정 금지).
  4) date=None(추론) 경로는 저장값이 있는 한 종전과 동일하다.

pytest 없음 → PASS/FAIL 자체점검.
"""
import sys

sys.path.insert(0, "/app")

from app.feature_engine.factor_features import FactorFeatures  # noqa: E402

fails = []


def check(name, got, want, tol=1e-9):
    ok = abs(float(got) - float(want)) <= tol
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: got={got} want={want}")
    if not ok:
        fails.append(name)


ff = FactorFeatures()

# --- 1) 저장값 우선 -------------------------------------------------------
latest = {"per": 7.5, "pbr": 1.2, "revenue": 100.0, "operating_profit": 10.0,
          "net_income": 100.0, "total_assets": 1000.0, "total_equity": 500.0,
          "roe": 12.0, "debt_ratio": 50.0}
annual = {"net_income": 999.0, "total_equity": 999.0}
v = ff._value_factors(market_cap=1000.0, latest=latest, prev={}, annual=annual)
check("stored per wins", v["value_per"], 7.5)
check("stored pbr wins", v["value_pbr"], 1.2)

# --- 2) 결측이면 계산 ------------------------------------------------------
latest2 = dict(latest, per=0.0, pbr=0.0)
v2 = ff._value_factors(market_cap=1000.0, latest=latest2, prev={}, annual=annual)
check("per fallback = cap/ni_ttm", v2["value_per"], 1000.0 / 999.0)
check("pbr fallback = cap/equity", v2["value_pbr"], 1000.0 / 500.0)

# annual 이 비면 latest net_income 으로 계산
v3 = ff._value_factors(market_cap=1000.0, latest=latest2, prev={}, annual={})
check("per fallback(연간 없음)= cap/latest ni", v3["value_per"], 1000.0 / 100.0)

# --- 3) 입력이 없으면 0 ----------------------------------------------------
latest4 = {"per": 0.0, "pbr": 0.0, "net_income": 0.0, "total_equity": 0.0}
v4 = ff._value_factors(market_cap=0.0, latest=latest4, prev={}, annual={})
check("no input -> per 0", v4["value_per"], 0.0)
check("no input -> pbr 0", v4["value_pbr"], 0.0)

# --- 4) quality_roe ------------------------------------------------------
q = ff._quality_factors(market_cap=1000.0, latest=latest, prev={}, pg_conn=None,
                        stock_code="000250", annual=annual)
check("stored roe wins", q["quality_roe"], 12.0)
q2 = ff._quality_factors(market_cap=1000.0, latest=dict(latest, roe=0.0), prev={},
                         pg_conn=None, stock_code="000250", annual=annual)
check("roe fallback = ni/equity*100", q2["quality_roe"], 100.0 / 500.0 * 100)
q3 = ff._quality_factors(market_cap=1000.0, latest=dict(latest, roe=0.0, total_equity=0.0),
                         prev={}, pg_conn=None, stock_code="000250", annual=annual)
check("roe fallback(자본 없음) = 0", q3["quality_roe"], 0.0)

print(f"\n{'FAIL' if fails else 'ALL PASS'} ({len(fails)} fail)")
sys.exit(1 if fails else 0)

"""
value_ratios - PER/PBR/ROE/PSR/GP/A 계산 (강환국『하면 된다! 퀀트투자』).

point-in-time 재무 스냅샷 + stocks.market_cap 으로 가치 비율을 계산해
반환만 한다. financial_statements 스키마 컬럼(per/pbr/roe)에는 기록하지
않는다 (plan T4 Must NOT). 음수/0 분모는 해당 비율 None 처리 — 방어적
처리이지 과최적화가 아니다.
"""

from typing import Dict, Optional


def _positive(value) -> Optional[float]:
    """Coerce to float if numeric and > 0, else None (defensive)."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def compute_ratios(
    snapshot,
    market_cap_map: Dict[str, Optional[float]],
    asof_date=None,
) -> Dict[str, Dict[str, Optional[float]]]:
    """Compute PER/PBR/ROE/PSR/GPA per stock from point-in-time financials.

    Args:
        snapshot: FinancialSnapshot instance (get_latest(stock_code, asof_date)).
        market_cap_map: {stock_code: market_cap} (from stocks.market_cap).
        asof_date: 'YYYY-MM-DD' or None (None = newest row).

    Returns:
        {code: {"per": ..., "pbr": ..., "roe": ..., "psr": ..., "gpa": ...}}

    Missing financial rows / market_cap or non-positive denominators yield
    None for the affected ratios (no ZeroDivisionError). GPA is conditional
    on gross_profit being present (유/무 조건부).
    """
    ratios: Dict[str, Dict[str, Optional[float]]] = {}
    for code, market_cap in market_cap_map.items():
        row = snapshot.get_latest(code, asof_date)
        if not row:
            ratios[code] = {"per": None, "pbr": None, "roe": None, "psr": None, "gpa": None}
            continue

        cap = _positive(market_cap)
        net_income = _positive(row.get("net_income"))
        equity = _positive(row.get("total_equity"))
        revenue = _positive(row.get("revenue"))
        total_assets = _positive(row.get("total_assets"))
        gross_profit = row.get("gross_profit")

        per = cap / net_income if cap and net_income else None
        pbr = cap / equity if cap and equity else None
        roe = net_income / equity if net_income and equity else None
        psr = cap / revenue if cap and revenue else None

        gpa = None
        if total_assets and gross_profit is not None:
            try:
                gpa = float(gross_profit) / total_assets
            except (TypeError, ValueError):
                gpa = None

        ratios[code] = {"per": per, "pbr": pbr, "roe": roe, "psr": psr, "gpa": gpa}
    return ratios

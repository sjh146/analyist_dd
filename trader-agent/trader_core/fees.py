"""수수료·세금 파생 로직 — 저널의 gross 손익과 브로커의 실현손익을 맞춘다 (백로그 T1).

문제 (2026-09-28 실측):
    저널은 청산 손익을 ``(exit - entry) x qty`` 로만 기록하고 ``fees`` 는 0 으로 남는다.
    그래서 모의 세션 성적표(+111원/건, 33건 누적 +3,662원)는 **비용을 빼지 않은 gross** 였다.
    실계좌에서는 이 비용이 바로 현금으로 나간다 — net 기대값을 모르면 전략 판단이 불가능하다.

증거:
    같은 날 같은 종목에서 브로커(CpTd6032 매도실현손익) -6,516원 vs 저널 -6,400원 = **116원 차**.
    왕복 7.4만원 규모에서 0.16% ≈ 매도 수수료 + 거래세 수준이다. 즉 브로커의 매도실현손익은
    **비용을 이미 반영한 net** 이고, 저널은 gross 다 — 그 차이가 곧 fees 다.

접근:
    요율을 가정하지 않고 **브로커 값을 기준**으로 삼는다(이 리포의 원칙: 가격은 브로커 값만 쓴다).
    종목 단위로 (저널 gross 손익 합) - (브로커 net 실현손익) = 그 종목의 비용.
    부호 검산(삼현 437730, 2026-09-28): 저널 -6,400 · 브로커 -6,516 → -6,400 - (-6,516) = **+116원**
    (비용은 손익을 깎으므로 gross - net 이 양수 = 비용).
    하루에 같은 종목을 여러 번 청산했다면 금액 비중으로 나눠 배분한다.

한계(정직하게):
    브로커 실현손익이 net 이라는 가정 위에 서 있다. 첫 실계좌 청산에서 차이가 0 에 가깝게 나오면
    (즉 브로커 값도 gross 라면) 이 방식은 무효다 — 그때는 요율 기반 계산으로 바꿔야 한다.
    ``check`` 플래그(음수 또는 회전금액의 1% 초과)가 뜨면 그 행은 사람이 확인한다.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

MAX_REASONABLE_FEE_PCT = 0.01  # 회전금액(매도대금) 대비 1% 를 넘으면 확인 대상

# CpTd6032(체결기준 종목별 일간 손익)는 **당일 세션의 실현손익만** 제공한다(실측 2026-10-04:
# 과거 09-30 청산 3건을 조회하니 브로커 매도 종목 0건). 그래서 수수료 계상은 **청산 당일**에 해야 하고,
# 지난 날짜는 조용히 0건으로 넘기면 "비용 0"으로 오독되므로 명시적으로 거부한다.
SAME_DAY_ONLY_REASON = (
    "브로커 손익 조회(CpTd6032)는 당일 세션만 제공한다 — 과거 날짜는 복구할 수 없다. "
    "그날 청산이 있으면 그날(장 종료 후) 실행해야 한다."
)


def same_day_guard(date_str: str, today: str) -> Optional[str]:
    """과거 날짜 요청이면 거부 사유를, 같은 날이면 None 을 돌려준다(순수 함수 — 테스트 대상)."""
    if date_str == today:
        return None
    return (
        "요청 날짜 {0} ≠ 오늘 {1}: {2}".format(date_str, today, SAME_DAY_ONLY_REASON)
    )



def _f(value: object, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def normalise_code(code: object) -> str:
    text = str(code or "").strip().upper()
    return text.lstrip("A") if text else ""


def derive_fees(
    journal_rows: Sequence[Dict[str, Any]],
    broker_items: Iterable[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """저널 청산 행과 브로커 실현손익을 맞춰 종목별 비용을 파생한다.

    ``journal_rows``: ``{id, code, qty, entry, exit_px}`` (하루치 청산 행)
    ``broker_items`` : ``{code, sell_qty, avg_sell_price, realized_pnl}`` (CpTd6032)

    반환: ``(allocations, unmatched_journal, unmatched_broker)``
      allocations      : ``[{code, fee, gross, net, pct, flag, rows:[{id, fee, gross}]}]``
      unmatched_journal: 브로커 실현손익이 없는 저널 행(그날 브로커가 보고하지 않은 청산)
      unmatched_broker : 저널 청산이 없는 브로커 종목(우리가 기록하지 않은 매도)
    """
    # 1) 저널 청산을 종목별로 모은다(gross 는 가격에서 직접 계산 — fees 를 섞지 않는다)
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in journal_rows:
        code = normalise_code(row.get("code"))
        entry = _f(row.get("entry"))
        exit_px = _f(row.get("exit_px"))
        qty = _f(row.get("qty"))
        gross = (exit_px - entry) * qty  # 전략은 롱 전용(저널 side='buy')
        grouped.setdefault(code, []).append(
            {
                "id": row.get("id"),
                "code": code,
                "qty": qty,
                "entry": entry,
                "exit_px": exit_px,
                "gross": gross,
                "turnover": exit_px * qty,  # 매도대금 — 비용률 판정 기준
            }
        )

    broker: Dict[str, Dict[str, Any]] = {}
    for item in broker_items:
        broker[normalise_code(item.get("code"))] = item

    allocations: List[Dict[str, Any]] = []
    matched_codes = set()
    for code, rows in grouped.items():
        item = broker.get(code)
        if item is None:
            continue
        matched_codes.add(code)
        net = _f(item.get("realized_pnl"))
        gross_total = sum(r["gross"] for r in rows)
        turnover = sum(r["turnover"] for r in rows)
        # 비용은 손익을 깎는다: broker_net = gross - cost  =>  cost = gross - net
        fee_total = gross_total - net
        pct = (fee_total / turnover) if turnover else 0.0
        flag = "ok"
        if fee_total < 0:
            flag = "check(음수비용)"
        elif abs(pct) > MAX_REASONABLE_FEE_PCT:
            flag = "check(과대)"
        # 금액(gross) 비중으로 배분 — gross 합이 0 이면 수량 비중으로 폴백
        weights = [abs(_f(r["gross"])) for r in rows]
        if sum(weights) <= 0:
            weights = [_f(r["qty"]) for r in rows]
        total_w = sum(weights) or 1.0
        per_row = []
        allocated = 0.0
        for index, r in enumerate(rows):
            share = fee_total * (weights[index] / total_w)
            if index == len(rows) - 1:  # 반올림 잔차는 마지막 행이 흡수한다
                share = fee_total - allocated
            allocated += share
            per_row.append({"id": r["id"], "code": code, "fee": share, "gross": r["gross"]})
        allocations.append(
            {
                "code": code,
                "fee": fee_total,
                "gross": gross_total,
                "net": net,
                "pct": pct,
                "turnover": turnover,
                "flag": flag,
                "rows": per_row,
            }
        )

    unmatched_journal = [
        {"id": r["id"], "code": code, "gross": r["gross"]}
        for code, rows in grouped.items()
        if code not in matched_codes
        for r in rows
    ]
    unmatched_broker = [
        {"code": code, "realized_pnl": _f(item.get("realized_pnl")), "sell_qty": item.get("sell_qty")}
        for code, item in broker.items()
        if code not in matched_codes
    ]
    return allocations, unmatched_journal, unmatched_broker


def journal_row_from_trade(trade: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """JournalTrade 스타일 dict -> derive_fees 입력 행(닫힌 행만)."""
    if trade.get("exit_ts") in (None, "") or trade.get("exit_price") in (None, ""):
        return None
    return {
        "id": trade.get("id"),
        "code": trade.get("code"),
        "qty": trade.get("qty"),
        "entry": trade.get("price"),
        "exit_px": trade.get("exit_price"),
    }

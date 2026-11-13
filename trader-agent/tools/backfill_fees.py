#!/usr/bin/env python3
"""브로커 실현손익으로 저널의 수수료·세금을 채운다 (백로그 T1 — 실계좌 필수).

왜: 저널 청산 손익은 ``(exit - entry) x qty`` gross 이고 ``fees`` 는 0 이다. 모의에서는
비용이 안 나가서 문제가 없었지만 **실계좌에서는 그 비용이 그대로 현금으로 나간다**.
net 기대값을 모르면 "이 전략이 돈을 버는가"를 판정할 수 없다(2026-09-28 실측: 브로커 -6,516
vs 저널 -6,400 → 116원 차가 미기록 비용).

방식(요율 가정 없음): 종목별로 ``브로커 매도실현손익(net) - 저널 gross 손익 = 비용``.
브로커 값이 기준이고, 하루에 같은 종목을 여러 번 청산했으면 금액 비중으로 배분한다.
자세한 근거·한계는 ``trader_core/fees.py`` 참조.

반드시 **32비트** 인터프리터(CybosPlus COM)로 실행한다:

    C:\\Users\\jhshi\\Python311-32\\python.exe tools\\backfill_fees.py                  # 예행
    C:\\Users\\jhshi\\Python311-32\\python.exe tools\\backfill_fees.py --apply          # 저널 반영
    C:\\Users\\jhshi\\Python311-32\\python.exe tools\\backfill_fees.py --date 2026-09-29

크레온 로그인 + 브리지가 아니라 **COM 직접** 호출이라 HTS 가 떠 있어야 한다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from typing import Any, Dict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bridge.orders import fetch_daily_pnl  # noqa: E402
from bridge.plus import BridgeError  # noqa: E402
from trader_core.fees import derive_fees, journal_row_from_trade, same_day_guard  # noqa: E402
from trader_core.journal import Journal  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _safe_stdout() -> None:
    """Windows 콘솔(cp949)에서 em-dash 등이 UnicodeEncodeError 로 죽는 것을 막는다(2026-10-04 실측)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass



def _num(value: object) -> float:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def main(argv=None) -> int:
    _safe_stdout()
    parser = argparse.ArgumentParser(description="브로커 실현손익으로 저널 수수료를 채운다")
    parser.add_argument("--journal", default=os.path.join(ROOT, "journal", "trade_journal.sqlite3"))
    parser.add_argument("--date", default=dt.datetime.now().strftime("%Y-%m-%d"),
                        help="청산 날짜(YYYY-MM-DD, KST). 기본=오늘")
    parser.add_argument("--apply", action="store_true", help="저널에 실제로 기록한다")
    args = parser.parse_args(argv)

    today = dt.datetime.now().strftime("%Y-%m-%d")
    refusal = same_day_guard(args.date, today)
    if refusal:
        print(refusal)
        print("  → 청산 당일(장 종료 후) 실행해야 한다. 이 경로는 매 거래일 16:05 자동 틱으로 돈다.")
        return 2

    data: Dict[str, Any]
    try:
        data = fetch_daily_pnl(None)
    except BridgeError as exc:
        print("브로커 조회 실패: {0}".format(exc))
        return 1

    items = [i for i in (data.get("items") or []) if _num(i.get("sell_qty")) > 0]
    journal = Journal(args.journal)
    closed = [journal_row_from_trade(t) for t in journal.closed_on(args.date)]
    rows = [r for r in closed if r is not None]

    print("backfill_fees @ {0}  (journal={1}, apply={2})".format(
        args.date, os.path.relpath(args.journal, ROOT), args.apply))
    print("브로커 매도 종목 {0}건 · 저널 청산 {1}건".format(len(items), len(rows)))
    if rows and not items:
        print("  브로커가 오늘 실현손익을 보고하지 않았다 — 청산이 있었는데도 0건이면 "
              "HTS 로그인/계좌 상태를 확인하라(추측으로 채우지 않는다).")
    if not rows:
        print("저널에 그날 청산 기록이 없다 — 할 일 없음.")
        return 0

    allocations, unmatched_j, unmatched_b = derive_fees(rows, items)
    print()
    print("{:<8} {:>12} {:>12} {:>10} {:>8}  {}".format(
        "code", "저널gross", "브로커net", "비용", "비율", "flag"))
    total_fee = 0.0
    for a in allocations:
        total_fee += float(a["fee"])
        print("{:<8} {:>12,.0f} {:>12,.0f} {:>10,.0f} {:>7.3%}  {}".format(
            a["code"], float(a["gross"]), float(a["net"]), float(a["fee"]),
            float(a["pct"]), a["flag"]))
    print("{:<8} {:>12} {:>12} {:>10,.0f}".format("합계", "", "", total_fee))

    for item in unmatched_j:
        print("  [미대응-저널] {0} (id {1}) — 브로커가 그날 실현손익을 보고하지 않았다".format(
            item["code"], item["id"]))
    for item in unmatched_b:
        print("  [미대응-브로커] {0} 실현손익 {1:,.0f} — 저널 청산 기록이 없다".format(
            item["code"], float(item["realized_pnl"])))

    if not args.apply:
        print("\n[예행] --apply 를 붙이면 위 비용을 저널 fees 에 쓰고 pnl 을 net 으로 다시 계산한다.")
        return 0

    updated = 0
    for a in allocations:
        for row in a["rows"]:
            if journal.set_fees(int(row["id"]), float(row["fee"])):
                updated += 1
    print("\n반영: {0}행 fees/pnl 갱신 · 총 비용 {1:,.0f}원".format(updated, total_fee))
    if unmatched_j or unmatched_b:
        print("주의: 미대응 항목이 있다 — 위 목록을 사람이 확인하라(추측 금지).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

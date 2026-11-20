"""저널에 빠진 체결을 **브로커 실적 기준**으로 복원한다 (감사·상한 정합용).

WHY(2026-11-20 068050): 09:05 매수 주문이 브리지 타임아웃 응답을 받았지만 브로커에서는
체결됐다. 저널에 행이 없어 ① 일일 진입 카운트가 1건 적게 세어져 4번째 주문이 나갔고
② 청산 손익이 저널 누적에 남지 않는다. 이 도구는 그 구멍을 **사후에** 메운다.

원칙
- 기본은 dry-run. `--apply` 를 줘야 쓴다.
- 같은 종목·같은 날짜(KST) 매수 행이 이미 있으면 **아무것도 쓰지 않고 exit 3**.
- 기록은 `order_type="reconciled"`, `entry_reason="reconcile: adopted from broker (manual backfill)"`.
- 매도가 주어지면 매도 행까지 함께 기록해 손익이 저널에 남게 한다.

사용
  python3 tools/backfill_missing_entry.py --code 068050 --qty 74 --price 1111.1351 \
      --entry-ts "2026-11-20T09:05:50+09:00" --name 팬엔터테인먼트 \
      [--exit-price 1102 --exit-ts "2026-11-20T15:05:00+09:00"] [--apply]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trader_core.config import KST  # noqa: E402
from trader_core.journal import Journal  # noqa: E402


def _parse_ts(value: str) -> datetime:
    ts = datetime.fromisoformat(value)
    return ts if ts.tzinfo else ts.replace(tzinfo=KST)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--journal", default=str(ROOT / "journal" / "trade_journal.sqlite3"))
    ap.add_argument("--code", required=True)
    ap.add_argument("--name", default="")
    ap.add_argument("--qty", type=int, required=True)
    ap.add_argument("--price", type=float, required=True, help="체결 평균단가(브로커 avg_buy_price)")
    ap.add_argument("--entry-ts", required=True)
    ap.add_argument("--exit-price", type=float, default=None)
    ap.add_argument("--exit-ts", default=None)
    ap.add_argument("--screener", default="daytrading")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    entry_ts = _parse_ts(args.entry_ts)
    journal = Journal(args.journal)

    same_day = (
        [args.code] if args.code in journal.codes_entered_today(entry_ts) else []
    )
    if same_day:
        print(json.dumps({"ok": False, "reason": "이미 매수 행이 있다",
                          "code": args.code}, ensure_ascii=False))
        return 3

    payload = {
        "code": args.code, "qty": args.qty, "price": args.price,
        "entry_ts": entry_ts.isoformat(), "exit_price": args.exit_price,
        "apply": bool(args.apply),
    }
    if not args.apply:
        print(json.dumps({"ok": True, "dry_run": True, **payload}, ensure_ascii=False))
        return 0

    buy_id = journal.record_trade(
        side="buy", code=args.code, name=args.name, qty=args.qty, price=args.price,
        order_type="reconciled",
        entry_reason="reconcile: adopted from broker (manual backfill)",
        screener=args.screener, ts=entry_ts,
    )
    payload["buy_id"] = buy_id
    if args.exit_price is not None:
        exit_ts = _parse_ts(args.exit_ts) if args.exit_ts else entry_ts
        payload["sell_id"] = journal.record_trade(
            side="sell", code=args.code, name=args.name, qty=args.qty,
            price=args.exit_price, order_type="reconciled",
            entry_reason="reconcile: broker fill backfill (exit)",
            screener=args.screener, ts=exit_ts,
        )
    print(json.dumps({"ok": True, **payload}, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

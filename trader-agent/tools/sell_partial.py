"""One-shot partial exit for a live account (owner-approved, out of band).

Why this exists: the loop only closes a position whole (stop / take-profit), so
"reduce 케어젠 to 5 shares at the open" has no in-system path.  This tool does
exactly one approved reduction, proves the fill at the broker, and keeps the
journal truthful so the running loop does not try to sell shares that are gone.

    C:\\Users\\jhshi\\Python312-64\\python.exe tools\\sell_partial.py --code 214370 --qty 5 --yes

Safety rules (each one is a recorded abort, never a guess):
  * refuses without --yes (default is a read-only preview)
  * aborts when the bridge is disconnected or /balance does not answer
  * aborts when the broker does not hold at least --qty sellable shares
  * aborts when any working order exists for the code (someone else is selling)
  * a single-use lock file, so a retried/scheduled double run cannot sell twice
  * the order echo is NOT a fill: the verdict comes from /positions + /orders
  * never writes a price: the journal row keeps its entry price, only qty is
    corrected to what the broker actually holds (the reconcile rule-6 repair)

Writes tools/../reports/sell_partial_<code>_<date>.json with the full evidence.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
import time
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRIDGE = os.environ.get("TRADER_BRIDGE_URL", "http://127.0.0.1:8100")
JOURNAL = os.environ.get("TRADER_JOURNAL", os.path.join(REPO, "journal", "trade_journal.sqlite3"))
REPORTS = os.path.join(REPO, "reports")

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _req(path: str, body: dict = None, timeout: float = 25.0) -> dict:
    url = BRIDGE + path
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    with _OPENER.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get(path: str) -> dict:
    return _req(path)


def post(path: str, body: dict) -> dict:
    return _req(path, body)


def say(msg: str) -> None:
    # cp949 console: keep output ASCII-only so a Korean name cannot kill the run
    print("[{0}] {1}".format(dt.datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def position_for(positions: list, code: str):
    for p in positions:
        if str(p.get("code")) == str(code):
            return p
    return None


def working_orders_for(orders: list, code: str):
    return [o for o in orders if str(o.get("code")) == str(code)]


def correct_journal_qty(code: str, new_qty: int, evidence: dict) -> str:
    """Set the open row's qty to what the broker holds; keep entry price untouched."""
    if not os.path.exists(JOURNAL):
        return "journal not found: {0}".format(JOURNAL)
    con = sqlite3.connect(JOURNAL, timeout=15)
    try:
        cur = con.execute(
            "SELECT id, qty, algo_json FROM trades WHERE code=? AND exit_ts IS NULL ORDER BY id DESC LIMIT 1",
            (code,),
        )
        row = cur.fetchone()
        if row is None:
            return "no open journal row for {0}".format(code)
        tid, old_qty, algo = row
        try:
            algo_d = json.loads(algo) if algo else {}
        except Exception:  # noqa: BLE001
            algo_d = {}
        algo_d["partial_exit"] = evidence
        con.execute(
            "UPDATE trades SET qty=?, algo_json=? WHERE id=?",
            (new_qty, json.dumps(algo_d, ensure_ascii=False), tid),
        )
        con.commit()
        return "journal row {0}: qty {1} -> {2}".format(tid, old_qty, new_qty)
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--code", required=True)
    ap.add_argument("--qty", type=int, required=True)
    ap.add_argument("--yes", action="store_true", help="actually place the order (default: preview only)")
    ap.add_argument("--lock-dir", default=os.path.join(REPO, "reports"))
    ap.add_argument("--settle-seconds", type=int, default=180)
    args = ap.parse_args()

    os.makedirs(REPORTS, exist_ok=True)
    today = dt.datetime.now().strftime("%Y%m%d")
    evidence = {"ts_start": dt.datetime.now().isoformat(timespec="seconds"), "code": args.code,
                "qty": args.qty, "live": bool(args.yes), "steps": []}

    def abort(reason: str, rc: int = 3) -> int:
        evidence["verdict"] = "aborted"
        evidence["reason"] = reason
        say("ABORT: " + reason)
        write_evidence(args, today, evidence)
        return rc

    # ---- pre-flight -------------------------------------------------------
    try:
        health = get("/health")
    except Exception as exc:  # noqa: BLE001
        return abort("bridge unreachable: {0}".format(exc))
    evidence["steps"].append({"health": health})
    say("health connected={0}".format(health.get("connected")))
    if not health.get("connected"):
        return abort("bridge reports connected=false (Creon session / login)")

    try:
        bal = get("/balance")
        pos = get("/positions")
        orders = get("/orders")
    except Exception as exc:  # noqa: BLE001
        return abort("broker query failed: {0}".format(exc))
    if not bal.get("ok") or not pos.get("ok") or not orders.get("ok"):
        return abort("bridge answered an error payload (half-dead session?)")
    p = position_for(pos.get("positions") or [], args.code)
    evidence["steps"].append({"balance": bal.get("balance"), "position": p, "orders": orders.get("orders")})
    if p is None:
        return abort("broker holds no position in {0}".format(args.code))
    sellable = int(p.get("sellable_qty") or 0)
    say("position qty={0} sellable={1} avg={2}".format(p.get("qty"), sellable, p.get("avg_price")))
    if sellable < args.qty:
        return abort("sellable {0} < requested {1}".format(sellable, args.qty))
    busy = working_orders_for(orders.get("orders") or [], args.code)
    if busy:
        return abort("working order already live for {0}: {1}".format(args.code, busy))

    if not args.yes:
        evidence["verdict"] = "preview"
        say("PREVIEW OK - would market-sell {0} share(s) of {1}".format(args.qty, args.code))
        write_evidence(args, today, evidence)
        return 0

    # ---- single-use lock --------------------------------------------------
    lock = os.path.join(args.lock_dir, "sell_partial_{0}_{1}.lock".format(args.code, today))
    if os.path.exists(lock):
        return abort("lock file already present ({0}) - refusing a second run".format(lock))
    with open(lock, "w", encoding="utf-8") as fh:
        fh.write(dt.datetime.now().isoformat(timespec="seconds"))

    # ---- submit -----------------------------------------------------------
    body = {"side": "sell", "code": args.code, "qty": args.qty, "order_type": "market"}
    try:
        resp = post("/order", body)
    except Exception as exc:  # noqa: BLE001
        return abort("order submit failed: {0}".format(exc), rc=4)
    evidence["steps"].append({"order_request": body, "order_response": resp})
    say("order response: {0}".format(json.dumps(resp, ensure_ascii=False)[:400]))
    echo = resp.get("order") or {}

    # ---- prove the fill ---------------------------------------------------
    before_qty = int(p.get("qty") or 0)
    target = before_qty - args.qty
    deadline = time.time() + args.settle_seconds
    verdict = "unconfirmed"
    last = {}
    while time.time() < deadline:
        time.sleep(5)
        try:
            pos2 = get("/positions").get("positions") or []
            ord2 = get("/orders").get("orders") or []
            last = {"positions": pos2, "orders": ord2,
                    "at": dt.datetime.now().isoformat(timespec="seconds")}
        except Exception as exc:  # noqa: BLE001
            last = {"error": str(exc), "at": dt.datetime.now().isoformat(timespec="seconds")}
            continue
        p2 = position_for(pos2, args.code)
        held = int(p2.get("qty") or 0) if p2 else 0
        live_orders = working_orders_for(ord2, args.code)
        say("settle: held={0} (target {1}) working_orders={2}".format(held, target, len(live_orders)))
        if held <= target and not live_orders:
            verdict = "filled" if held == target else "partial"
            break
    evidence["steps"].append({"settle_final": last})
    evidence["verdict"] = verdict
    evidence["order_echo"] = echo
    say("VERDICT: " + verdict)

    # ---- keep the loop's view truthful -----------------------------------
    if verdict in ("filled", "partial"):
        try:
            held_final = int((position_for(last.get("positions") or [], args.code) or {}).get("qty") or 0)
            note = correct_journal_qty(args.code, held_final, {"ts": evidence["ts_start"], "qty_sold": args.qty,
                                                              "verdict": verdict, "order_echo": echo})
            evidence["journal"] = note
            say(note)
        except Exception as exc:  # noqa: BLE001
            evidence["journal"] = "journal update failed: {0}".format(exc)
            say(evidence["journal"])

    write_evidence(args, today, evidence)
    return 0 if verdict in ("filled", "partial") else 5


def write_evidence(args, today: str, evidence: dict) -> None:
    path = os.path.join(REPORTS, "sell_partial_{0}_{1}.json".format(args.code, today))
    evidence["ts_end"] = dt.datetime.now().isoformat(timespec="seconds")
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(evidence, fh, ensure_ascii=False, indent=2)
        say("evidence: " + path)
    except Exception as exc:  # noqa: BLE001
        say("could not write evidence: {0}".format(exc))


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""단타 스캘핑 경로 불변식 감사(읽기 전용).

시장 중 5분마다 돌아 '의도대로 매매되는가'를 수치로 남긴다. 코드는 건드리지 않는다.
산출: reports/scalp_audit_latest.json, reports/scalp_findings.jsonl(지문 중복 제거)
종료코드: 0 정상 / 1 위반 있음 / 2 판정 불가
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
LOGGER = logging.getLogger("scalp_audit")
REPO = "/home/jhshi/analyist_dd"
TRADER = r"C:\Users\jhshi\trader-agent"
BRIDGE = "http://127.0.0.1:8100"
FEED = "http://127.0.0.1:8090/screener_latest.json"
OUT_JSON = os.path.join(REPO, "reports", "scalp_audit_latest.json")
OUT_JSONL = os.path.join(REPO, "reports", "scalp_findings.jsonl")
MIN_LIQUIDITY = 5_000_000_000
STALE_ORDER_S = 90.0


def _get(url: str, timeout: float = 8.0):
    req = urllib.request.Request(url, headers={"User-Agent": "scalp-audit"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _journal_copy() -> sqlite3.Connection | None:
    src = os.path.join("/mnt/c/Users/jhshi/trader-agent/journal", "trade_journal.sqlite3")
    if not os.path.exists(src):
        return None
    tmpdir = tempfile.mkdtemp(prefix="journal_")
    dst = os.path.join(tmpdir, "j.sqlite3")
    try:
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(src + suffix):
                shutil.copy2(src + suffix, dst + suffix)
        return sqlite3.connect(dst)
    except Exception as exc:
        LOGGER.warning("journal copy failed: %s", exc)
        return None


def _open_trades(conn: sqlite3.Connection) -> list[dict]:
    cols = [r[1] for r in conn.execute("PRAGMA table_info(trades)")]
    rows = conn.execute(
        "SELECT code, screener, qty, price, stop_pct, tp_pct, hwm_price, ts "
        "FROM trades WHERE exit_price IS NULL OR exit_ts IS NULL"
    ).fetchall()
    out = []
    for row in rows:
        d = dict(zip(["code", "screener", "qty", "price", "stop_pct", "tp_pct",
                      "hwm_price", "ts"], row))
        out.append(d)
    return out


def _today_entries(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT code, screener, qty, price, ts, exit_price FROM trades "
        "WHERE date(ts) = date('now','+9 hours') ORDER BY id"
    ).fetchall()
    return [{"code": r[0], "screener": r[1], "qty": r[2], "price": r[3],
             "ts": r[4], "exit_price": r[5]} for r in rows]


def _fingerprint(kind: str, detail: str) -> str:
    return hashlib.sha256((kind + "|" + detail).encode("utf-8")).hexdigest()[:16]


def _seen(path: str) -> set:
    seen = set()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                try:
                    seen.add(json.loads(line).get("fp"))
                except ValueError as exc:
                    LOGGER.warning("%s:%s unparseable findings line skipped: %s",
                                   path, lineno, exc)
    return seen


def audit() -> dict:
    now = datetime.now(KST)
    findings: list[dict] = []
    state: dict = {"at": now.isoformat(timespec="seconds")}

    def add(kind, detail, severity="high"):
        findings.append({"kind": kind, "detail": detail, "severity": severity,
                         "fp": _fingerprint(kind, detail)})

    try:
        state["bridge"] = _get(BRIDGE + "/health")
    except Exception as exc:
        add("bridge_down", str(exc)[:120])
        state["bridge"] = {"ok": False}

    positions, orders, quotes = [], [], {}
    positions_known = False
    if state.get("bridge", {}).get("ok"):
        try:
            positions = _get(BRIDGE + "/positions").get("positions", []) or []
            positions_known = True
        except Exception as exc:
            add("positions_unreadable", str(exc)[:120])
        try:
            orders = _get(BRIDGE + "/orders").get("orders", []) or []
        except Exception as exc:
            add("orders_unreadable", str(exc)[:120])
        codes = [str(p.get("code")) for p in positions if p.get("code")]
        if codes:
            try:
                quotes = _get(BRIDGE + "/quotes?codes=" + ",".join(codes)).get("quotes", {}) or {}
            except Exception as exc:
                add("quotes_unreadable", str(exc)[:120])

    try:
        feed = _get(FEED)
        state["feed_generated_at"] = feed.get("generated_at")
        items = (feed.get("candidates", {}).get("daytrading", {}) or {}).get("items") or []
        state["daytrading_items"] = len(items)
        have_tv = [x for x in items if x.get("trading_value") is not None]
        if items and not have_tv:
            add("feed_missing_trading_value", "daytrading {0}건 전부 trading_value 없음".format(len(items)),
                severity="medium")
        thin = [x["stock_code"] for x in have_tv
                if (x.get("trading_value") or 0) < MIN_LIQUIDITY]
        if thin:
            add("thin_liquidity_candidate",
                "{0}건 < {1:,.0f}원: {2}".format(len(thin), MIN_LIQUIDITY, ",".join(thin[:8])))
    except Exception as exc:
        add("feed_unreadable", str(exc)[:120])

    conn = _journal_copy()
    journal_open, today = [], []
    if conn is None:
        add("journal_unreadable", "trade_journal.sqlite3 복사 실패")
    else:
        try:
            journal_open = _open_trades(conn)
            today = _today_entries(conn)
        finally:
            conn.close()

    broker_codes = {str(p.get("code")) for p in positions if p.get("code")}
    journal_codes = {str(r["code"]) for r in journal_open}
    if positions_known:
        for code in sorted(broker_codes - journal_codes):
            add("broker_without_journal", code)
        for code in sorted(journal_codes - broker_codes):
            add("journal_without_broker", code)
        state["reconcile"] = "checked"
    else:
        state["reconcile"] = "unverified: broker positions unreadable"
    state["positions_known"] = positions_known

    for row in journal_open:
        code = str(row["code"])
        q = quotes.get(code) or {}
        cur = q.get("current") or q.get("price")
        entry = float(row["price"] or 0)
        stop = float(row["stop_pct"] or 0)
        if not cur or not entry or stop <= 0:
            continue
        line = entry * (1.0 - stop)
        if float(cur) <= line:
            add("stop_not_fired",
                "{0} 현재 {1:,.0f} <= 손절 {2:,.0f} (진입 {3:,.0f}, {4:.2%})".format(
                    code, float(cur), line, entry, stop))

    for row in today:
        if row["screener"] != "daytrading" or row["exit_price"] is not None:
            continue
        if row["qty"] == 0:
            add("zero_qty_entry", str(row["code"]))
        if row["price"] and row["price"] <= 0:
            add("bad_entry_price", "{0} {1}".format(row["code"], row["price"]))

    for order in orders:
        code = str(order.get("code"))
        fy = int(order.get("filled_qty") or 0)
        if fy == 0:
            add("unfilled_order_open",
                "{0} 주문 {1} 미체결 (취소가능 {2})".format(code, order.get("order_no"),
                                                         order.get("cancelable_qty")),
                severity="medium")

    state["positions"] = [{"code": p.get("code"), "qty": p.get("qty"),
                           "eval_pnl": p.get("eval_pnl")} for p in positions]
    state["open_orders"] = len(orders)
    state["journal_open"] = len(journal_open)
    state["findings"] = findings
    state["verdict"] = "OK" if not findings else "ALERT:" + ",".join(
        sorted({f["kind"] for f in findings}))
    return state


def main() -> int:
    state = audit()
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
    seen = _seen(OUT_JSONL)
    new_rows = [f for f in state["findings"] if f["fp"] not in seen]
    if new_rows:
        with open(OUT_JSONL, "a", encoding="utf-8") as fh:
            for row in new_rows:
                row["at"] = state["at"]
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(state["at"], state["verdict"],
          "| pos", len(state.get("positions", [])), "| new findings", len(new_rows))
    if not state.get("bridge", {}).get("ok"):
        return 2
    return 1 if state["findings"] else 0


if __name__ == "__main__":
    sys.exit(main())

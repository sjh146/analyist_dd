"""SQLite trade/decision journal (stdlib ``sqlite3``, WAL).

Schema version 1 via ``PRAGMA user_version`` (idempotent migration).  Two
tables per PHASE3_BRIEF.md:

* ``decisions`` — every engine gate verdict (plan or block) for the Kelly
  feedback loop.
* ``trades`` — executed fills; a position entry is a row that is later
  updated (not re-inserted) by :meth:`close_trade` with the realised PnL.

All timestamps are stored as timezone-aware ISO-8601 strings (KST).  The path
is configurable and ``:memory:`` is supported for tests.  No third-party
modules are used.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .config import KST

_SCHEMA_VERSION = 4

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    screener TEXT NOT NULL,
    code TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    score REAL,
    gates_json TEXT NOT NULL DEFAULT '[]',
    plan_json TEXT,
    reason TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    side TEXT NOT NULL,
    code TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    qty INTEGER NOT NULL,
    price REAL NOT NULL,
    order_type TEXT NOT NULL DEFAULT 'limit',
    order_resp_json TEXT NOT NULL DEFAULT '{}',
    entry_reason TEXT NOT NULL DEFAULT '',
    screener TEXT NOT NULL DEFAULT '',
    hwm_price REAL,
    algo_json TEXT NOT NULL DEFAULT '{}',
    stop_pct REAL,
    tp_pct REAL,
    kelly_f REAL,
    exit_ts TEXT,
    exit_price REAL,
    exit_reason TEXT,
    pnl REAL,
    fees REAL NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_trades_code ON trades (code, exit_ts);
CREATE INDEX IF NOT EXISTS idx_decisions_ts ON decisions (ts);
"""


def _norm_code(code: Any) -> str:
    """Normalize a stock code the way ``engine._norm`` does.

    Keeps journal lookups comparable with feed/plan codes (strips whitespace,
    upper-cases, drops a leading ``A`` marker).
    """
    if code is None:
        return ""
    text = str(code).strip().upper()
    return text.lstrip("A") if text else ""


def _iso(now: datetime) -> str:
    if now.tzinfo is None:
        now = now.replace(tzinfo=KST)
    return now.isoformat()


class Journal:
    """SQLite-backed journal of decisions and trades."""

    def __init__(self, path: str = ":memory:", timeout: float = 10.0) -> None:
        """Open the journal database at *path* (``:memory:`` for tests)."""
        self.path = path
        in_memory = path == ":memory:"
        if not in_memory:
            parent = os.path.dirname(os.path.abspath(path))
            os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(path, timeout=timeout)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if not in_memory:
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    # ------------------------------------------------------------------ #
    def _migrate(self) -> None:
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version < _SCHEMA_VERSION:
            with self._conn:
                self._conn.executescript(_SCHEMA)
                self._conn.execute(
                    "PRAGMA user_version = {0}".format(_SCHEMA_VERSION)
                )
        self._ensure_columns()

    def _ensure_columns(self) -> None:
        """Add columns introduced after v1 to an existing database.

        ``CREATE TABLE IF NOT EXISTS`` does not touch a table that already
        exists, so every post-v1 column needs an explicit, idempotent ALTER.
        """
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(trades)").fetchall()
        }
        additions = (
            ("screener", "ALTER TABLE trades ADD COLUMN screener TEXT NOT NULL DEFAULT ''"),
            ("hwm_price", "ALTER TABLE trades ADD COLUMN hwm_price REAL"),
            ("algo_json", "ALTER TABLE trades ADD COLUMN algo_json TEXT NOT NULL DEFAULT '{}'"),
        )
        for name, statement in additions:
            if name not in columns:
                with self._conn:
                    self._conn.execute(statement)

    def close(self) -> None:
        """Close the underlying connection."""
        self._conn.close()

    def __enter__(self) -> "Journal":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # decisions
    # ------------------------------------------------------------------ #
    def record_decision(
        self,
        screener: str,
        code: str,
        name: str = "",
        score: Optional[float] = None,
        gates_json: str = "[]",
        plan_json: Optional[str] = None,
        reason: str = "",
        ts: Optional[datetime] = None,
    ) -> int:
        """Insert one decision row; returns the new row id."""
        cur = self._conn.execute(
            "INSERT INTO decisions (ts, screener, code, name, score,"
            " gates_json, plan_json, reason) VALUES (?,?,?,?,?,?,?,?)",
            (
                _iso(ts or datetime.now(KST)),
                screener,
                code,
                name,
                score,
                gates_json,
                plan_json,
                reason,
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def recent_decisions(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return the most recent *limit* decisions (newest first)."""
        rows = self._conn.execute(
            "SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------ #
    # trades
    # ------------------------------------------------------------------ #
    def record_trade(
        self,
        side: str,
        code: str,
        qty: int,
        price: float,
        order_type: str = "limit",
        order_resp_json: str = "{}",
        entry_reason: str = "",
        name: str = "",
        screener: str = "",
        stop_pct: Optional[float] = None,
        tp_pct: Optional[float] = None,
        kelly_f: Optional[float] = None,
        algo_json: str = "{}",
        ts: Optional[datetime] = None,
    ) -> int:
        """Insert an executed trade (position entry); returns the row id.

        ``screener`` records *which strategy list* the position belongs to (종가
        vs 스윙 vs self-generated ranking).  The loop needs it to know whether a
        position is sold at the next open or held under stop/take-profit.
        ``algo_json`` is the full algorithm attribution for this fill (see
        :mod:`trader_core.algo`): screener, scoring mode, filters, the
        candidate's metrics, the sizing path and the stop model.
        """
        cur = self._conn.execute(
            "INSERT INTO trades (ts, side, code, name, qty, price, order_type,"
            " order_resp_json, entry_reason, screener, algo_json, stop_pct, tp_pct,"
            " kelly_f) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                _iso(ts or datetime.now(KST)),
                side,
                code,
                name,
                int(qty),
                float(price),
                order_type,
                order_resp_json,
                entry_reason,
                screener,
                algo_json,
                stop_pct,
                tp_pct,
                kelly_f,
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def close_trade(
        self,
        trade_id: int,
        exit_price: float,
        exit_reason: str = "",
        fees: float = 0.0,
        exit_ts: Optional[datetime] = None,
        algo_patch: Optional[str] = None,
    ) -> bool:
        """Mark *trade_id* closed and compute realised PnL.

        PnL = (exit - entry) * qty for a long (buy) entry, inverted for a
        short (sell) entry, minus *fees*.  Returns False when the row is
        unknown or already closed.
        """
        row = self._conn.execute(
            "SELECT side, qty, price, exit_ts FROM trades WHERE id = ?",
            (int(trade_id),),
        ).fetchone()
        if row is None or row["exit_ts"] is not None:
            return False
        qty = int(row["qty"])
        entry = float(row["price"])
        exit_px = float(exit_price)
        if str(row["side"]).lower() == "sell":
            pnl = (entry - exit_px) * qty - float(fees)
        else:
            pnl = (exit_px - entry) * qty - float(fees)
        if algo_patch:
            # keep the entry attribution and add how/why it was closed
            self._conn.execute(
                "UPDATE trades SET algo_json = ? WHERE id = ?",
                (algo_patch, int(trade_id)),
            )
        self._conn.execute(
            "UPDATE trades SET exit_ts = ?, exit_price = ?, exit_reason = ?,"
            " pnl = ?, fees = ? WHERE id = ?",
            (
                _iso(exit_ts or datetime.now(KST)),
                exit_px,
                exit_reason,
                pnl,
                float(fees),
                int(trade_id),
            ),
        )
        self._conn.commit()
        return True

    def correct_exit(
        self,
        trade_id: int,
        exit_price: float,
        fees: Optional[float] = None,
        exit_reason: Optional[str] = None,
    ) -> bool:
        """Rewrite a closed trade's exit price and recompute its PnL.

        Used by reconciliation when an exit was journaled at a bogus price (a
        market-order echo of ``0`` booked a whole position as a loss).  The
        real price must come from the broker -- this method never guesses.
        Returns False for unknown or still-open rows.
        """
        row = self._conn.execute(
            "SELECT side, qty, price, exit_ts, fees FROM trades WHERE id = ?",
            (int(trade_id),),
        ).fetchone()
        if row is None or row["exit_ts"] is None:
            return False
        qty = int(row["qty"])
        entry = float(row["price"])
        exit_px = float(exit_price)
        fee = float(row["fees"] or 0.0) if fees is None else float(fees)
        if str(row["side"]).lower() == "sell":
            pnl = (entry - exit_px) * qty - fee
        else:
            pnl = (exit_px - entry) * qty - fee
        if exit_reason is None:
            self._conn.execute(
                "UPDATE trades SET exit_price = ?, pnl = ?, fees = ? WHERE id = ?",
                (exit_px, pnl, fee, int(trade_id)),
            )
        else:
            self._conn.execute(
                "UPDATE trades SET exit_price = ?, pnl = ?, fees = ?,"
                " exit_reason = ? WHERE id = ?",
                (exit_px, pnl, fee, exit_reason, int(trade_id)),
            )
        self._conn.commit()
        return True

    def reopen_trade(self, trade_id: int) -> bool:
        """Undo a ``close_trade`` (used to repair a wrong reconciliation).

        Returns False when the row is unknown or still open.  Every exit field
        is cleared so the row behaves exactly like an open position again.
        """
        row = self._conn.execute(
            "SELECT exit_ts FROM trades WHERE id = ?", (int(trade_id),)
        ).fetchone()
        if row is None or row["exit_ts"] is None:
            return False
        self._conn.execute(
            "UPDATE trades SET exit_ts = NULL, exit_price = NULL,"
            " exit_reason = NULL, pnl = NULL, fees = 0 WHERE id = ?",
            (int(trade_id),),
        )
        self._conn.commit()
        return True

    def update_hwm(self, trade_id: int, price: float) -> bool:
        """Raise the high-water mark of an open trade (trailing stop memory).

        Only ever moves *up*; returns True when the stored value changed.
        """
        try:
            value = float(price)
        except (TypeError, ValueError):
            return False
        if value <= 0:
            return False
        row = self._conn.execute(
            "SELECT hwm_price FROM trades WHERE id = ? AND exit_ts IS NULL",
            (int(trade_id),),
        ).fetchone()
        if row is None:
            return False
        current = row["hwm_price"]
        if current is not None and float(current) >= value:
            return False
        with self._conn:
            self._conn.execute(
                "UPDATE trades SET hwm_price = ? WHERE id = ?", (value, int(trade_id))
            )
        return True

    def adjust_entry(
        self,
        trade_id: int,
        qty: Optional[int] = None,
        price: Optional[float] = None,
        note: str = "",
    ) -> bool:
        """Correct the entry qty/price of an **open** trade from broker facts.

        Partial fills are why this exists: an order response is not a fill
        confirmation, so the journal can claim more shares than the broker
        actually holds.  Closed trades are never rewritten (history stays
        immutable); returns True when a row was updated.
        """
        row = self._conn.execute(
            "SELECT * FROM trades WHERE id = ?", (int(trade_id),)
        ).fetchone()
        if row is None or row["exit_ts"] is not None:
            return False
        new_qty = int(qty) if qty is not None else int(row["qty"])
        new_price = float(price) if price is not None else float(row["price"])
        if new_qty <= 0 or new_price <= 0:
            return False
        reason = row["entry_reason"] or ""
        if note:
            reason = (reason + " | " + note).strip(" |")
        with self._conn:
            self._conn.execute(
                "UPDATE trades SET qty = ?, price = ?, entry_reason = ? WHERE id = ?",
                (new_qty, new_price, reason, int(trade_id)),
            )
        return True

    def open_trade_for(self, code: str) -> Optional[Dict[str, Any]]:
        """Return the latest unclosed trade row for *code* (or None)."""
        row = self._conn.execute(
            "SELECT * FROM trades WHERE code = ? AND exit_ts IS NULL"
            " ORDER BY id DESC LIMIT 1",
            (code,),
        ).fetchone()
        return dict(row) if row is not None else None

    def open_trades(self, screener: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return every unclosed trade row (optionally for one *screener*).

        ``screener`` lets the loop manage strategies separately -- e.g. sell
        only the 종가 positions at the next open while swing positions stay put.
        """
        if screener is None:
            rows = self._conn.execute(
                "SELECT * FROM trades WHERE exit_ts IS NULL ORDER BY id"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM trades WHERE exit_ts IS NULL AND screener = ?"
                " ORDER BY id",
                (str(screener),),
            ).fetchall()
        return [dict(row) for row in rows]

    def trade_count_today(self, now: Optional[datetime] = None) -> int:
        """Count trade rows whose entry timestamp falls on the KST day of *now*."""
        now = now or datetime.now(KST)
        if now.tzinfo is None:
            now = now.replace(tzinfo=KST)
        day_start = now.astimezone(KST).replace(hour=0, minute=0, second=0, microsecond=0)
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM trades WHERE ts >= ? AND ts < ?",
            (_iso(day_start), _iso(day_start + timedelta(days=1))),
        ).fetchone()
        return int(row["n"]) if row else 0

    def codes_entered_today(
        self, now: Optional[datetime] = None, side: str = "buy"
    ) -> List[str]:
        """Return the codes that already got an entry today (KST).

        Used as the anti-churn guard: with a large daily entry cap the same
        candidate list would otherwise be re-bought on every cycle.
        """
        now = now or datetime.now(KST)
        if now.tzinfo is None:
            now = now.replace(tzinfo=KST)
        day_start = now.astimezone(KST).replace(hour=0, minute=0, second=0, microsecond=0)
        rows = self._conn.execute(
            "SELECT DISTINCT code FROM trades WHERE side = ? AND ts >= ? AND ts < ?",
            (
                str(side),
                _iso(day_start),
                _iso(day_start + timedelta(days=1)),
            ),
        ).fetchall()
        return [_norm_code(row["code"]) for row in rows]

    def closed_trades(
        self, period_days: int = 30, now: Optional[datetime] = None
    ) -> List[Dict[str, Any]]:
        """Return closed trades in the rolling window (oldest first).

        Rows carry the entry price/qty and the realised ``pnl`` so a review
        step can compute percentage returns (``pnl / (price*qty)``), which is
        what the Kelly priors are expressed in.
        """
        now = now or datetime.now(KST)
        if now.tzinfo is None:
            now = now.replace(tzinfo=KST)
        since = now - timedelta(days=int(period_days))
        rows = self._conn.execute(
            "SELECT id, ts, side, code, name, qty, price, exit_ts, exit_price,"
            " exit_reason, pnl, fees, entry_reason, screener, algo_json, stop_pct, tp_pct,"
            " kelly_f FROM trades WHERE exit_ts IS NOT NULL AND exit_ts >= ?"
            " ORDER BY exit_ts",
            (_iso(since),),
        ).fetchall()
        return [dict(row) for row in rows]

    def summary(self, period_days: int = 30,
                now: Optional[datetime] = None) -> Dict[str, Any]:
        """Aggregate closed-trade performance over *period_days* (rolling).
        A win is any closed trade with ``pnl > 0``.  Returns
        ``{trades, wins, losses, win_rate, avg_win, avg_loss, total_pnl}``
        where ``avg_win``/``avg_loss`` are the mean KRW PnL magnitudes.
        """
        now = now or datetime.now(KST)
        if now.tzinfo is None:
            now = now.replace(tzinfo=KST)
        since = now - timedelta(days=int(period_days))
        rows = self._conn.execute(
            "SELECT pnl FROM trades WHERE exit_ts IS NOT NULL AND exit_ts >= ?",
            (_iso(since),),
        ).fetchall()
        closed = [float(r["pnl"]) for r in rows]
        wins = [pnl for pnl in closed if pnl > 0]
        losses = [pnl for pnl in closed if pnl <= 0]
        total = sum(closed)
        return {
            "trades": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": (len(wins) / len(closed)) if closed else 0.0,
            "avg_win": (sum(wins) / len(wins)) if wins else 0.0,
            "avg_loss": (abs(sum(losses)) / len(losses)) if losses else 0.0,
            "total_pnl": total,
        }

    def to_json(self, obj: Any) -> str:
        """Serialize a value to a JSON string (helper for plan/order payloads)."""
        return json.dumps(obj, ensure_ascii=False, default=str)

    # ------------------------------------------------------------------ #
    # 수수료·세금 반영 (백로그 T1)
    # ------------------------------------------------------------------ #
    def closed_on(self, day: str) -> List[Dict[str, Any]]:
        """*day* (``YYYY-MM-DD``, KST 기준 접두 일치) 에 청산된 행을 반환한다.

        브로커의 당일 실현손익(CpTd6032)과 맞추기 위한 조회다.  exit_ts 는 ISO
        문자열(+09:00)이라 날짜 접두 매칭이 정확하다.

        ``ts``(진입 시각)·``screener``·``exit_reason``·``algo_json`` 도 함께 돌려준다 —
        환류 내보내기(``tools/export_fills.py``)가 진입시각/전략 귀속/모델 확률을
        필요로 하고, 여기서 빠지면 그 필드가 빈 값으로 조용히 나간다(2026-09-29 실측).
        """
        rows = self._conn.execute(
            "SELECT id, ts, side, code, name, qty, price, exit_ts, exit_price, pnl,"
            " fees, screener, exit_reason, algo_json"
            " FROM trades WHERE exit_ts IS NOT NULL AND exit_ts LIKE ?"
            " ORDER BY exit_ts",
            ("{0}%".format(str(day)[:10]),),
        ).fetchall()
        return [dict(row) for row in rows]

    def set_fees(self, trade_id: int, fees: float) -> bool:
        """*trade_id* 의 ``fees`` 를 기록하고 ``pnl`` 을 net 으로 다시 계산한다.

        ``pnl`` 을 "현재 pnl - fees" 로 깎지 않고 **가격에서 다시 계산**한다 —
        그래야 같은 행에 두 번 호출해도 값이 어긋나지 않는다(멱등).  청산가는
        브로커 값이므로 추측이 들어가지 않는다.  이미 청산된 행만 대상으로 하고,
        열린 행이나 없는 id 는 ``False``.
        """
        row = self._conn.execute(
            "SELECT side, qty, price, exit_ts, exit_price FROM trades WHERE id = ?",
            (int(trade_id),),
        ).fetchone()
        if row is None or row["exit_ts"] is None or row["exit_price"] is None:
            return False
        qty = int(row["qty"])
        entry = float(row["price"])
        exit_px = float(row["exit_price"])
        fee = float(fees)
        if str(row["side"]).lower() == "sell":
            pnl = (entry - exit_px) * qty - fee
        else:
            pnl = (exit_px - entry) * qty - fee
        self._conn.execute(
            "UPDATE trades SET fees = ?, pnl = ? WHERE id = ?",
            (fee, pnl, int(trade_id)),
        )
        self._conn.commit()
        return True

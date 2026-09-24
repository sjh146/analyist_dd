#!/usr/bin/env python3
"""As-of (date-aligned) verification harness for app.feature_engine.macro_features.

Run inside the ML container:

    docker exec -w /app stock_xgboost_ml python /app/scripts/verify_macro_asof_features.py

Proves that macro features are now computed per (stock, date) as-of the requested
date instead of returning one constant "latest" snapshot for every training row.

Exit code 0 => all checks passed.
"""
import os
import sys
import time
from datetime import datetime

import numpy as np
import psycopg2

sys.path.insert(0, "/app")
from app.feature_engine.macro_features import MacroFeatures  # noqa: E402

DATES = ["2026-04-15", "2026-07-15", "2026-09-18"]
KEYS = ["fx_usd_krw", "fx_change_1m", "fx_change_3m",
        "oil_wti", "oil_change_1m", "oil_change_3m"]
ALL_KEYS = KEYS + ["interest_rate", "interest_rate_change_1m",
                   "interest_rate_change_3m", "cpi_yoy", "ppi_yoy",
                   "yield_spread", "credit_spread"]


def connect():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "stock_postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432) or 5432),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD"),
    )


def main() -> int:
    conn = connect()
    cur = conn.cursor()
    failures = []
    mf = MacroFeatures()

    # 1) BEFORE: legacy path (date=None) - one constant row for every date.
    legacy = mf.get_macro_from_db(conn)
    print("### [1] legacy path (date=None) - unchanged pre-fix behaviour")
    for k in KEYS:
        print(f"    {k:20s} = {legacy[k]:10.4f}   (identical for every training row)")

    # 2) AFTER: as-of per date.
    print("\n### [2] as-of path - values per requested date")
    mf.invalidate_cache()
    t0 = time.perf_counter()
    mf.get_macro_from_db(conn, DATES[0])
    cold_ms = (time.perf_counter() - t0) * 1000
    rows, warm = {}, []
    for d in DATES:
        t0 = time.perf_counter()
        rows[d] = mf.get_macro_from_db(conn, d)
        warm.append((time.perf_counter() - t0) * 1000)
    print("    " + f"{'feature':20s}" + "".join(f"{d:>14s}" for d in DATES) + f"{'std':>10s}")
    for k in ALL_KEYS:
        vals = [rows[d][k] for d in DATES]
        std = float(np.std(vals))
        print(f"    {k:20s}" + "".join(f"{v:14.4f}" for v in vals) + f"{std:10.4f}")
        if k in KEYS:
            if std == 0.0:
                failures.append(f"{k} is constant across dates")
            if all(v == legacy[k] for v in vals):
                failures.append(f"{k} unchanged vs legacy - not as-of")
    print(f"    cold={cold_ms:.1f}ms warm={sum(warm)/len(warm):.2f}ms")

    # 3) Leakage: reader value == latest DB observation <= date.
    print("\n### [3] leakage sweep (reader == SQL latest obs <= date)")
    sweep = ["2023-10-02", "2024-01-15", "2024-06-17", "2024-11-11", "2025-02-10",
             "2025-05-19", "2025-08-11", "2025-11-17", "2026-01-12", "2026-04-15",
             "2026-05-20", "2026-07-15", "2026-08-19", "2026-09-18", "2027-01-15"]
    bad = 0
    for d in sweep:
        res = mf.get_macro_from_db(conn, d)
        for k, ind in [("fx_usd_krw", "USD/KRW 환율"), ("oil_wti", "WTI 유가")]:
            cur.execute("SELECT date, value FROM macro_indicators "
                        "WHERE indicator_name=%s AND date<=%s ORDER BY date DESC LIMIT 1",
                        (ind, d))
            db_date, db_val = cur.fetchone()
            if abs(float(db_val) - res[k]) >= 1e-6:
                bad += 1
                failures.append(f"{d} {k}: reader={res[k]} db={db_val} ({db_date})")
    print(f"    checked {len(sweep)*2}/{len(sweep)*2} date-indicator pairs, mismatches={bad}")

    # 4) Panel sweep: features must actually vary across the training panel.
    cur.execute("SELECT DISTINCT trade_date FROM market_data "
                "WHERE trade_date >= '2025-10-01' ORDER BY trade_date")
    panel = [r[0].isoformat() for r in cur.fetchall()]
    t0 = time.perf_counter()
    panel_feats = [mf.get_macro_from_db(conn, d) for d in panel]
    panel_ms = (time.perf_counter() - t0) * 1000
    print(f"\n### [4] panel sweep over {len(panel)} dates ({panel[0]}..{panel[-1]})"
          f" total={panel_ms:.0f}ms per-row={panel_ms/len(panel):.3f}ms")
    for k in KEYS:
        a = np.array([f[k] for f in panel_feats])
        n_uniq = len(np.unique(np.round(a, 6)))
        print(f"    {k:20s} min={a.min():9.3f} max={a.max():9.3f} "
              f"std={a.std():8.4f} distinct={n_uniq}")
        if n_uniq < 2:
            failures.append(f"panel: {k} has a single value across {len(panel)} dates")

    # 5) TTL / invalidation.
    short = MacroFeatures(cache_ttl=0.05)
    short.get_macro_from_db(conn, "2026-04-15")
    t0 = time.perf_counter(); short.get_macro_from_db(conn, "2026-07-15")
    ttl_warm = (time.perf_counter() - t0) * 1000
    time.sleep(0.2)
    t0 = time.perf_counter(); short.get_macro_from_db(conn, "2026-07-15")
    ttl_expired = (time.perf_counter() - t0) * 1000
    print(f"\n### [5] TTL: warm={ttl_warm:.2f}ms, reload-after-expiry={ttl_expired:.2f}ms "
          f"(ttl=0.05s), invalidate_cache()={callable(mf.invalidate_cache)}")
    if ttl_expired <= ttl_warm:
        failures.append("TTL expiry did not trigger a reload")

    # 6) Feature-key contract.
    got = set(rows[DATES[0]].keys())
    print(f"\n### [6] key contract: n={len(got)} exact_match_13={got == set(ALL_KEYS)}")
    if got != set(ALL_KEYS):
        failures.append(f"key contract broken: {sorted(got)}")

    print("\n" + ("ALL_CHECKS_OK" if not failures else "CHECKS_FAILED: " + "; ".join(failures)))
    return 0 if not failures else 1


if __name__ == "__main__":
    print(f"# macro as-of verification @ {datetime.now().isoformat(timespec='seconds')}")
    sys.exit(main())

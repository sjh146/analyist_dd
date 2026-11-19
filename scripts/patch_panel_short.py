#!/usr/bin/env python3
"""공매도 비중 피처(short_selling_ratio)를 패널에 컬럼 패치.

WHY(2026-11-19): 패널에서 `short_selling_ratio` 가 DEAD_SPARSE(nnz 3.4%)인 이유는
원천 `krx_short_selling` 이 **308종목·패널 26/200** 만 덮고 있어서다. KIS 백필로 전 종목을
채운 뒤, 전체 재빌드(3시간+) 없이 **해당 컬럼만** as-of 로 다시 계산한다.

값 정의(빌더 계약과 동일): short_volume / total_volume (둘 다 >0), 없으면 short_ratio/100,
둘 다 없으면 **NaN**(0 채움 금지 — "실제 0"과 구분해야 한다).

as-of: 각 (code, date) 에 대해 `trade_date <= date - lag` 인 가장 최근 공매도 행.
lag 기본 1거래일(KRX 공매도는 D+1 공표 → 당일 값 사용은 룩어헤드 위험).

자기검증: 저장 후 ①행수 ②feature_names 순서 ③비대상 컬럼 바이트 동일(equal_nan).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

import numpy as np

REQUIRED_KEYS = ("X", "feature_names", "dates", "codes", "price")


def log(msg):
    print("[patch_short] %s" % msg, flush=True)


def _pg():
    import psycopg2

    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT", "5434")),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def load_short(db, codes, max_date):
    """(code, date_list_sorted) → {code: [(date, value), ...]} (오름차순)."""
    cur = db.cursor()
    cur.execute(
        """
        SELECT stock_code, trade_date::text, short_volume, total_volume, short_ratio
        FROM krx_short_selling
        WHERE trade_date <= %s AND stock_code = ANY(%s)
        ORDER BY stock_code, trade_date
        """,
        (max_date, list(codes)),
    )
    out = {}
    for code, d, sv, tv, sr in cur.fetchall():
        v = None
        if sv is not None and tv and float(tv) > 0:
            v = float(sv) / float(tv)
        elif sr is not None:
            v = float(sr) / 100.0
        if v is None:
            continue
        out.setdefault(str(code), []).append((str(d), v))
    cur.close()
    return out


def asof_values(codes, dates, series, lag_trading_days=1, max_stale_days=10):
    """패널 (code,date) 각 행에 대해 직전 lag 거래일까지의 최신 값(없으면 NaN).

    ``max_stale_days``: 최신 원천 행이 그 날짜보다 이보다 오래되면 **NaN**(오래된 값을
    그대로 끌고 오면 "그 날의 공매도 비중"이 아니라 몇 달 전 값이 된다 — 룩어헤드는 아니지만
    시점 왜곡이다). 0 이면 stale 허용 안 함.
    """
    import datetime as _dt

    vals = np.full(len(codes), np.nan, dtype=np.float32)
    ptr = {}
    for i in range(len(codes)):
        code = str(codes[i])
        d = str(dates[i])
        rows = series.get(code)
        if not rows:
            continue
        # date < d 인 행만 후보 (당일 제외)
        idx = ptr.get(code, 0)
        best = None
        while idx < len(rows) and rows[idx][0] < d:
            best = rows[idx]
            idx += 1
        ptr[code] = idx
        if best is not None:
            best_date, best_val = best
            if max_stale_days and max_stale_days > 0:
                try:
                    gap = (_dt.date.fromisoformat(d) - _dt.date.fromisoformat(best_date)).days
                except ValueError:
                    gap = None
                if gap is not None and gap > max_stale_days:
                    continue
            vals[i] = best_val
    return vals


def main(argv=None):
    ap = argparse.ArgumentParser(description="공매도 비중 패널 컬럼 패치")
    ap.add_argument("--panel", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--column", default="short_selling_ratio")
    ap.add_argument("--report", default="data/reports/panel_short_patch.json")
    ap.add_argument("--lag-trading-days", type=int, default=1)
    ap.add_argument("--max-stale-days", type=int, default=10,
                    help="원천 최신 행이 이보다 오래되면 NaN(기본 10일). 0=제한 없음")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if os.path.abspath(args.panel) == os.path.abspath(args.out):
        log("거부: --out 이 원본과 같다(기준선 보존)")
        return 2
    if not os.path.exists(args.panel):
        log("패널 없음: %s" % args.panel)
        return 2

    z = np.load(args.panel, allow_pickle=True)
    missing = [k for k in REQUIRED_KEYS if k not in z.files]
    if missing:
        log("패널 키 누락: %s" % missing)
        return 2
    X = z["X"].copy()
    names = [str(x) for x in z["feature_names"]]
    dates = np.array([str(d) for d in z["dates"]])
    codes = np.array([str(c) for c in z["codes"]])
    price = z["price"]

    if args.column not in names:
        log("대상 컬럼 없음: %s (feature_names 에 없음)" % args.column)
        return 2
    col = names.index(args.column)
    before = X[:, col].copy()

    db = _pg()
    series = load_short(db, sorted(set(codes)), max(dates))
    db.close()
    log("원천 로드: %d종목 (패널 %d종목 중)" % (len(series), len(set(codes))))

    vals = asof_values(codes, dates, series, args.lag_trading_days, args.max_stale_days)
    X[:, col] = vals

    def stats(v):
        finite = v[np.isfinite(v)]
        return {
            "nonzero_rows": int(np.count_nonzero(finite)),
            "nnz_ratio": float(np.count_nonzero(finite) / max(len(v), 1)),
            "null_ratio": float(1.0 - len(finite) / max(len(v), 1)),
            "mean": float(finite.mean()) if len(finite) else None,
            "std": float(finite.std()) if len(finite) else None,
        }

    rep = {
        "panel": args.panel,
        "out": args.out,
        "column": args.column,
        "rows": int(len(codes)),
        "codes_with_source": len(series),
        "lag_trading_days": args.lag_trading_days,
        "before": stats(before),
        "after": stats(vals),
    }
    log("nnz %d → %d (%.4f%% → %.4f%%), null_ratio %.4f"
        % (rep["before"]["nonzero_rows"], rep["after"]["nonzero_rows"],
           100 * rep["before"]["nnz_ratio"], 100 * rep["after"]["nnz_ratio"],
           rep["after"]["null_ratio"]))

    if args.dry_run:
        log("dry-run — 쓰기 없음")
        print(json.dumps(rep, ensure_ascii=False))
        return 0

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(args.out)),
                              prefix=".patch_short.", suffix=".npz")
    os.close(fd)
    np.savez_compressed(tmp, X=X.astype(np.float32), feature_names=np.array(names),
                        dates=dates, codes=codes, price=price)
    os.replace(tmp, args.out)

    chk = np.load(args.out, allow_pickle=False)
    same_rows = len(chk["X"]) == len(X)
    same_names = [str(x) for x in chk["feature_names"]] == names
    others = [i for i in range(len(names)) if i != col]
    non_target_same = bool(np.array_equal(chk["X"][:, others], np.load(args.panel, allow_pickle=True)["X"][:, others], equal_nan=True))
    rep["verify"] = {"rows": bool(same_rows), "names": bool(same_names),
                     "non_target_same": non_target_same}
    log("자기검증: rows=%s names=%s non_target_same=%s"
        % (same_rows, same_names, non_target_same))

    os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
    with open(args.report, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    log("저장: %s | 보고서: %s" % (args.out, args.report))
    return 0 if (same_rows and same_names and non_target_same) else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""build_path_features — 패널에 '가격 경로 형태(path shape)' 피처를 덧붙인다 (CG263 준비).

왜: 패널 213컬럼은 대부분 수준·모멘텀·변동성의 **스칼라 요약**이다. 경로의 *형태*
(고점 대비 낙폭·구간 내 위치·상승일 비율·수익률 자기상관·최대 일간등락·갭)는
213컬럼 목록에 없고, 전부 종목 단위로 계산 가능 = 서빙 경로에서 배포 가능하다
(횡단면 rank 처럼 '추론 불가' 문제가 없다 — CG164 참조).

설계 원칙:
  · as-of: trade_date 당일 종가까지 사용(장 마감 후 확정값). 라벨은 date 이후 전방수익이므로 누수 없음.
  · 워밍업 부족 구간은 0.0 으로 채운다(패널 기존 결측 대체 관례와 동일).
  · 기존 패널의 X·dates·codes·price 는 그대로 두고 컬럼만 append 한다(행 순서 불변).

실행(컨테이너):
  docker exec stock_xgboost_ml python -u /app/scripts/build_path_features.py \
      --panel /app/app/models/wf/panel_prod200.npz \
      --out /tmp/panel_prod200_path.npz --report /app/reports/overnight/cg263_path_build.json
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

NEW_FEATURES = [
    "path_dd_high_60",      # close / 60일 최고종가 - 1  (≤0, 고점 대비 낙폭)
    "path_near_high_20",    # close / 20일 최고고가 - 1  (≤0, 신고가 근접도)
    "path_range_pos_60",    # (close-min(low,60)) / (max(high,60)-min(low))  ∈[0,1] 구간 내 위치
    "path_up_ratio_20",     # 최근 20일 상승일 비율
    "path_autocorr_20",     # 일간수익 lag-1 자기상관 (20일 창)
    "path_max_absret_20",   # 최근 20일 최대 |일간수익|
    "path_gap_mean_5",      # 최근 5일 평균 갭(open/prev_close-1)
]


def _to_date_str(x):
    if hasattr(x, "strftime"):
        return x.strftime("%Y-%m-%d")
    return str(x)[:10]


def load_daily(host, port, user, pwd, db, codes, start, end):
    import psycopg2
    q = """
        SELECT stock_code, trade_date, open_price, high_price, low_price, close_price
        FROM market_data
        WHERE trade_date BETWEEN %s AND %s AND stock_code = ANY(%s)
        ORDER BY stock_code, trade_date
    """
    conn = psycopg2.connect(host=host, port=port, user=user, password=pwd, dbname=db)
    try:
        df = pd.read_sql(q, conn, params=(start, end, list(codes)))
    finally:
        conn.close()
    for c in ("open_price", "high_price", "low_price", "close_price"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df


def compute(df):
    """종목별 시계열 → (stock_code, trade_date) 인덱스의 path 피처."""
    out = []
    for code, g in df.groupby("stock_code", sort=False):
        g = g.sort_values("trade_date")
        close, high, low, openp = g["close_price"], g["high_price"], g["low_price"], g["open_price"]
        ret = close.pct_change()
        f = pd.DataFrame(index=g.index)
        f["stock_code"] = code
        f["trade_date"] = g["trade_date"].values
        f["path_dd_high_60"] = close / close.rolling(60, min_periods=20).max() - 1.0
        f["path_near_high_20"] = close / high.rolling(20, min_periods=20).max() - 1.0
        rng = (high.rolling(60, min_periods=20).max() - low.rolling(60, min_periods=20).min())
        f["path_range_pos_60"] = np.where(rng > 0, (close - low.rolling(60, min_periods=20).min()) / rng, np.nan)
        f["path_up_ratio_20"] = (ret > 0).astype(float).rolling(20, min_periods=20).mean()
        # lag-1 자기상관 (20일 창)
        f["path_autocorr_20"] = ret.rolling(20, min_periods=20).corr(ret.shift(1))
        f["path_max_absret_20"] = ret.abs().rolling(20, min_periods=20).max()
        gap = openp / close.shift(1) - 1.0
        f["path_gap_mean_5"] = gap.rolling(5, min_periods=3).mean()
        out.append(f)
    res = pd.concat(out, ignore_index=True)
    res["trade_date"] = pd.to_datetime(res["trade_date"])
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", default=None)
    ap.add_argument("--warmup-days", type=int, default=90)
    ap.add_argument("--fill", type=float, default=0.0, help="워밍업/결측 대체값")
    a = ap.parse_args()

    d = np.load(a.panel, allow_pickle=True)
    X = np.asarray(d["X"], dtype=np.float32)
    names = [str(x) for x in d["feature_names"]]
    dates_raw = d["dates"]
    codes_raw = d["codes"]
    price = d["price"] if "price" in d else None

    dates = np.array([_to_date_str(x) for x in dates_raw])
    codes = np.array([str(c) for c in codes_raw])
    uniq_codes = sorted(set(codes.tolist()))
    dmin, dmax = min(dates.tolist()), max(dates.tolist())
    start = (pd.Timestamp(dmin) - pd.Timedelta(days=a.warmup_days)).strftime("%Y-%m-%d")

    env = os.environ
    daily = load_daily(env.get("POSTGRES_HOST", "postgres"), int(env.get("POSTGRES_PORT", "5432")),
                       env.get("POSTGRES_USER", "stock_user"), env.get("POSTGRES_PASSWORD", ""),
                       env.get("POSTGRES_DB", "stock_trading"), uniq_codes, start, dmax)
    feats = compute(daily)
    feats["trade_date"] = feats["trade_date"].dt.strftime("%Y-%m-%d")

    key = pd.MultiIndex.from_arrays([dates, codes])
    feats["_k"] = list(zip(feats["trade_date"], feats["stock_code"]))
    lookup = feats.drop_duplicates("_k").set_index("_k")

    new_cols = []
    coverage = {}
    for name in NEW_FEATURES:
        s = lookup[name].reindex(key)
        v = s.to_numpy(dtype=np.float64)
        coverage[name] = float(np.mean(np.isfinite(v)))
        v = np.where(np.isfinite(v), v, a.fill).astype(np.float32)
        new_cols.append(v)

    X2 = np.hstack([X, np.vstack(new_cols).T.astype(np.float32)]) if new_cols else X
    np.savez_compressed(a.out, X=X2, feature_names=np.array(names + NEW_FEATURES),
                        dates=dates_raw, codes=codes_raw,
                        **({"price": price} if price is not None else {}))

    rep = {
        "panel": a.panel, "out": a.out, "rows": int(X2.shape[0]),
        "cols_before": int(X.shape[1]), "cols_after": int(X2.shape[1]),
        "date_range": [dmin, dmax], "codes": len(uniq_codes),
        "daily_rows": int(len(daily)), "coverage": coverage,
        "fill": a.fill,
    }
    # 단일피처 AUC 는 panel_feature_screen.py 로 별도 측정(여기서는 커버리지만)
    print(json.dumps(rep, ensure_ascii=False))
    if a.report:
        os.makedirs(os.path.dirname(a.report), exist_ok=True)
        with open(a.report, "w") as fh:
            json.dump(rep, fh, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""패널에 '거래대금 수준' 피처를 as-of 추가한다 (CG269 준비물).

왜(2026-11-14, 계기 감도 검정 CG268 이후):
  · CG255 실측: 청정 패널 panel_prod200(213피처)에는 **거래대금·시총 수준 피처가 없다**
    (유동성은 volatility·atr 계열 변동성만 존재).
  · CG250·CG251·CG254·CG259 실측: 저거래대금 상위 Δ = +1.20%p/세션(t 3.38) 이고
    비용 모형(왕복 0.71%p)에서도 +0.47%p, 슬리피지 0.5%p 는 15:01~15:30 실측 고저폭으로 확인됐다.
    즉 **돈 지표로 검증된 신호인데 모델 입력에는 없는** 유일한 정보 클래스다.
  · CG268(계기 감도): 합성 단일피처 단일변량 AUC 0.58 → 모델 AUC +0.0365, 0.66 → +0.1263
    (거의 1:1 기울기). 즉 "단일변량 AUC ≈0.565 · 기존 피처와 상관<0.9 인 신규 피처 1개"면
    사전문턱 +0.02 를 넘길 수 있다. 거래대금 수준은 이 조건의 후보다.

시점정합: 날짜 d 의 값은 d 까지의 거래대금만 쓴다(당일 종가 시점에 알 수 있음). lookahead 없음.
산출: X 에 3열 추가 — liq_amt_5d_log · liq_amt_20d_log · liq_amt_trend_20_60
결측(그 종목·일의 이력 부족)은 패널 규약과 동일하게 0.0.

사용:
  docker exec stock_xgboost_ml python /app/scripts/patch_panel_liquidity.py \
      --in /app/app/models/wf/panel_prod200.npz --out /app/app/models/wf/panel_prod200_liq.npz
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd
import psycopg2

NEW_COLS = ["liq_amt_5d_log", "liq_amt_20d_log", "liq_amt_trend_20_60"]


def _pg_connect():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def _auc(y, s):
    y = np.asarray(y, float)
    s = np.asarray(s, float)
    m = np.isfinite(y) & np.isfinite(s)
    y, s = y[m], s[m]
    if len(y) == 0 or len(np.unique(y)) < 2:
        return float("nan")
    r = pd.Series(s).rank().values
    n1 = float((y > 0.5).sum())
    n0 = float(len(y) - n1)
    return float((r[y > 0.5].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", dest="dst", required=True)
    ap.add_argument("--report", default="")
    args = ap.parse_args()

    z = np.load(args.src, allow_pickle=True)
    X = z["X"].astype(np.float32, copy=True)
    names = [str(n) for n in z["feature_names"]]
    dates = np.array([str(d)[:10] for d in z["dates"]])
    codes = np.array([str(c) for c in z["codes"]])
    price = np.asarray(z["price"], dtype=float)

    add = [c for c in NEW_COLS if c not in names]
    if not add:
        print("STOP: 이미 컬럼이 존재한다")
        return 1

    uniq = sorted(set(codes))
    dmin, dmax = min(dates.tolist()), max(dates.tolist())
    conn = _pg_connect()
    q = ("SELECT stock_code, trade_date::text, trading_value FROM stock_prices "
         "WHERE stock_code = ANY(%s) AND trade_date BETWEEN "
         "(CAST(%s AS date) - 200) AND CAST(%s AS date)")
    df = pd.read_sql(q, conn, params=(uniq, dmin, dmax))
    conn.close()
    if df.empty:
        print("STOP: stock_prices 조회 결과 0행")
        return 1
    df["trade_date"] = df["trade_date"].str[:10]
    df["trading_value"] = pd.to_numeric(df["trading_value"], errors="coerce").fillna(0.0)
    df = df.sort_values(["stock_code", "trade_date"])

    g = df.groupby("stock_code", sort=False)["trading_value"]
    df["ma5"] = g.transform(lambda s: s.rolling(5, min_periods=3).mean())
    df["ma20"] = g.transform(lambda s: s.rolling(20, min_periods=10).mean())
    df["ma60"] = g.transform(lambda s: s.rolling(60, min_periods=30).mean())
    df["liq_amt_5d_log"] = np.log1p(df["ma5"].clip(lower=0.0))
    df["liq_amt_20d_log"] = np.log1p(df["ma20"].clip(lower=0.0))
    df["liq_amt_trend_20_60"] = np.log1p(df["ma20"].clip(lower=0.0)) - np.log1p(
        df["ma60"].clip(lower=0.0))

    grid = {(r.stock_code, r.trade_date): (r.liq_amt_5d_log, r.liq_amt_20d_log,
                                           r.liq_amt_trend_20_60)
            for r in df.itertuples()}
    n_new = np.zeros((len(dates), len(add)), dtype=np.float32)
    hit = 0
    for i, key in enumerate(zip(codes, dates)):
        v = grid.get((key[0], key[1]))
        if v is None:
            continue
        hit += 1
        for j, c in enumerate(add):
            val = v[NEW_COLS.index(c)]
            n_new[i, j] = 0.0 if not np.isfinite(val) else float(val)

    Xn = np.concatenate([X, n_new], axis=1)
    fn = np.array(names + add)
    np.savez(args.dst, X=Xn, feature_names=fn, dates=z["dates"], codes=z["codes"],
             price=z["price"])

    # ── 품질·누수 게이트 보고 (하드규칙 ③: 단일피처 AUC > 0.75 금지 · 종목상수 금지) ──
    sys.path.insert(0, "/app")
    sys.path.insert(0, "/app/scripts")
    import wf_wave as W  # noqa: E402
    pdf = pd.DataFrame({"stock_code": codes, "date": dates, "price": price})
    y = W.make_labels(pdf, "quantile", 5, 0.30)
    rep = {"src": args.src, "dst": args.dst, "rows": int(len(dates)),
           "coverage_pct": round(100.0 * hit / max(1, len(dates)), 2), "cols": {}}
    for j, c in enumerate(add):
        v = Xn[:, names.__len__() + j].astype(float)
        uq = pd.Series(v).groupby(pd.Series(codes)).nunique()
        rep["cols"][c] = {"auc_univariate": round(_auc(y, v), 4),
                          "nonzero_pct": round(100.0 * float(np.mean(v != 0)), 2),
                          "stock_constant_ratio": round(float(np.mean(uq <= 1)), 4)}
    # 최대상관: 표본 20k 행으로 제한해 계산 비용을 고정
    rng = np.random.default_rng(0)
    sel = rng.choice(len(dates), size=min(20000, len(dates)), replace=False)
    Xs = Xn[sel, :len(names)].astype(np.float64)
    for j, c in enumerate(add):
        v = Xn[sel, len(names) + j].astype(np.float64)
        if np.std(v) == 0:
            rep["cols"][c]["max_abs_corr"] = 0.0
            continue
        cors = []
        sd = np.std(Xs, axis=0)
        ok = sd > 0
        vv = (v - v.mean()) / (v.std() + 1e-12)
        for k in np.where(ok)[0]:
            cors.append(abs(float(np.mean(vv * ((Xs[:, k] - Xs[:, k].mean()) / sd[k])))))
        top = int(np.argmax(cors))
        rep["cols"][c]["max_abs_corr"] = round(float(max(cors)), 4)
        rep["cols"][c]["max_corr_feature"] = names[top]

    for c, v in rep["cols"].items():
        print(f"[{c}] AUC={v['auc_univariate']} 비영={v['nonzero_pct']}% "
              f"종목상수비율={v['stock_constant_ratio']} 최대상관={v['max_abs_corr']} "
              f"({v.get('max_corr_feature')})")
    print(f"coverage={rep['coverage_pct']}% rows={rep['rows']} → {args.dst}")
    if args.report:
        import json
        with open(args.report, "w") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1)
        print("report:", args.report)
    return 0


if __name__ == "__main__":
    sys.exit(main())

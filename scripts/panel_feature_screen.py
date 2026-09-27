#!/usr/bin/env python3
"""panel_feature_screen — 패널 자체를 피처 단위로 채점한다(단일피처 AUC · 날짜별 IC · 종목상수/시장레벨).

왜: `_db_feature_screen.py` 는 DB 원천을 채점한다. 하지만 실제 모델 입력은 **패널**이고,
패널에는 (a) as-of 지연·결측 대체 로직, (b) 종목상수(최신 스냅샷) 컬럼, (c) 시장레벨(날짜 상수)
컬럼이 섞여 있다. 1~2시간 패널 빌드 전에 "이 패널에 실제로 신호가 있는가"를 분 단위로 확인한다.

판정 기준(누수/설계):
  · 단일피처 AUC > 0.75 → 누수 의심(즉시 반려)
  · 종목별 유니크값 1 → 종목상수(룩어헤드 의심군)
  · 관측일 중 날짜내 종목간 유니크값 1 비율 ≥ 0.9 → 시장레벨(횡단면 모델에 부적합)
  · |IC| < 0.02 이고 |AUC−0.5| < 0.02 → 이 피처는 라벨에 대해 사실상 무정보

실행(컨테이너):
  docker exec stock_xgboost_ml python -u /app/scripts/panel_feature_screen.py \
      --panel /app/app/models/wf/panel_420_asofpatch.npz --horizon 5 --q 0.30 --top 30
"""
import argparse
import json
import sys

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", required=True)
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--q", type=float, default=0.30)
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    d = np.load(a.panel, allow_pickle=True)
    names = [str(x) for x in d["feature_names"]]
    X = np.asarray(d["X"], dtype=np.float64)
    codes = np.asarray([str(c) for c in d["codes"]])
    dates = np.asarray([str(x) for x in d["dates"]])
    price = np.asarray(d["price"], dtype=np.float64)

    df = pd.DataFrame(X, columns=[f"f{j}" for j in range(X.shape[1])])
    df["_c"], df["_d"], df["_p"] = codes, dates, price

    # 라벨: make_labels 와 동일 규칙(날짜내 분위 상/하위 q).
    ret = df.groupby("_c", sort=False)["_p"].transform(
        lambda s: s.shift(-a.horizon) / s - 1.0)
    hi = ret.groupby(df["_d"]).transform(lambda s: s.quantile(1 - a.q))
    lo = ret.groupby(df["_d"]).transform(lambda s: s.quantile(a.q))
    y = pd.Series(np.nan, index=df.index, dtype=float)
    y[ret > hi] = 1.0
    y[ret < lo] = 0.0
    dd = [str(x) for x in dates]
    print(f"패널 {X.shape[0]}행 × {X.shape[1]}피처 · {len(set(codes))}종목 · "
          f"관측일 {len(set(dd))}일 {min(dd)}~{max(dd)} · 라벨 유효 {int(y.notna().sum())}행")

    keep = y.notna().values
    yv = y.values[keep].astype(int)
    Xk = X[keep]
    dk = dates[keep]
    retk = ret.values[keep]
    ck = codes[keep]

    rows = []
    for i, nm in enumerate(names):
        col = Xk[:, i]
        m = ~np.isnan(col)
        n_obs = int(m.sum())
        rec = {"feature": nm, "n_obs": n_obs, "fill": round(n_obs / len(col), 4)}
        if n_obs > 200 and len(set(yv[m])) > 1:
            try:
                auc = float(roc_auc_score(yv[m], col[m]))
            except ValueError:
                auc = np.nan
            rec["auc"] = round(auc, 4)
            rec["auc_abs_edge"] = round(abs(auc - 0.5), 4)
        else:
            rec["auc"] = None
            rec["auc_abs_edge"] = None
        # 날짜별 IC (Pearson): 피처 vs 선행 h일 수익률
        ics = []
        for dt in np.unique(dk):
            sel = (dk == dt) & m
            if sel.sum() < 8:
                continue
            xv, rv = col[sel], retk[sel]
            if np.std(xv) == 0 or np.std(rv) == 0:
                continue
            ics.append(float(np.corrcoef(xv, rv)[0, 1]))
        rec["ic_mean"] = round(float(np.mean(ics)), 4) if ics else None
        rec["ic_days"] = len(ics)
        rec["ic_t"] = (round(float(np.mean(ics) / (np.std(ics, ddof=1) / np.sqrt(len(ics)))), 2)
                       if len(ics) >= 10 and np.std(ics, ddof=1) > 0 else None)
        # 종목상수 / 시장레벨
        nun = pd.DataFrame({"c": ck, "v": col}).groupby("c")["v"].nunique(dropna=True)
        rec["stock_constant"] = bool(nun.max() <= 1)
        g = pd.DataFrame({"d": dk, "v": col}).groupby("d")["v"]
        n_lvl = g.nunique(dropna=True)
        cnt = g.count()
        judged = cnt >= 2
        rec["market_level"] = bool(judged.sum() > 0 and
                                   (n_lvl[judged] <= 1).mean() >= 0.9)
        rows.append(rec)

    rows.sort(key=lambda r: -(r["auc_abs_edge"] or 0))
    print(f"\n=== 단일피처 AUC 상위 {a.top} (|AUC−0.5| 기준) ===")
    print(f"{'feature':34s} {'AUC':>7s} {'|Δ0.5|':>7s} {'IC':>8s} {'t':>6s} "
          f"{'fill':>6s} {'상수':>4s} {'시장':>4s}")
    for r in rows[:a.top]:
        print(f"{r['feature'][:34]:34s} {str(r['auc']):>7s} {str(r['auc_abs_edge']):>7s} "
              f"{str(r['ic_mean']):>8s} {str(r['ic_t']):>6s} {r['fill']:>6.3f} "
              f"{'Y' if r['stock_constant'] else '.':>4s} "
              f"{'Y' if r['market_level'] else '.':>4s}")

    leak = [r["feature"] for r in rows if (r["auc_abs_edge"] or 0) > 0.25]
    const = [r["feature"] for r in rows if r["stock_constant"]]
    mkt = [r["feature"] for r in rows if r["market_level"]]
    dead = [r["feature"] for r in rows if (r["auc_abs_edge"] or 0) < 0.02
            and (r["ic_mean"] is None or abs(r["ic_mean"]) < 0.02)]
    print(f"\n요약: 누수 의심(|AUC−0.5|>0.25) {len(leak)}개 {leak[:5]} · "
          f"종목상수 {len(const)}개 · 시장레벨 {len(mkt)}개 · 무정보 {len(dead)}/{len(rows)}개")
    best = [r for r in rows[:5]]
    print("최고 5개: " + ", ".join(f"{r['feature']}={r['auc']}" for r in best))

    if a.out:
        with open(a.out, "w") as f:
            json.dump({"panel": a.panel, "horizon": a.horizon, "q": a.q,
                       "rows": rows, "leak": leak, "const": const, "market": mkt,
                       "dead": dead}, f, ensure_ascii=False, indent=1)
        print("저장:", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

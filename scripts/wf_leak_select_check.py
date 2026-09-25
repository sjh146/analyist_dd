#!/usr/bin/env python3
"""wf_leak_select_check — 횡단면 모델에 '시장레벨/종목상수' 피처가 실제로 들어가는지 **실측**한다.

왜 필요한가 (누수 게이트 #3)
  스킬 계약: ① 단일 피처 AUC > 0.75 ② 종목 상수 피처가 선별을 지배 ③ as-of 위반
  ④ dead/시장레벨 피처를 횡단면 모델에 그대로 투입 — 이 넷 중 하나라도 걸리면 중단·반려.
  리서처 R16 실측: 피처 풀 199개 중 시장레벨 26개(예: adr·breadth·total_trading_value, xsec=1.000).
  그런데 `wf_wave.subset(select='top30')` 은 **단변량 edge 상위 30개**를 그대로 쓴다 —
  시장레벨 컬럼이 edge 상위에 오면 횡단면 모델에 그대로 투입된다. 이 스크립트는 그것을
  패널 파일에서 직접 세어 판정한다(추정 금지).

방법
  - 패널(npz)을 읽어 피처별로
      · xsec_const_rate = (날짜별 종목간 유니크값 ≤ 1 인 날짜 비율)  → 1.0 이면 시장레벨
      · stock_uniq_median = 종목별 유니크값 개수의 중앙값          → 1 이면 종목상수
    를 계산한다.
  - `wf_wave` 와 **동일한** 폴드 구성(확장창·경계 h일 purge)·라벨(분위 q·호라이즌 h)·
    단변량 edge(동률 평균순위 Mann-Whitney U)·선별(top-k)을 재현해, 폴드별 top-k 안에
    시장레벨/종목상수 컬럼이 몇 개 들어가는지 센다.
  - ⚠ 이 스크립트는 **측정만** 한다. 선별 로직은 고치지 않는다(고치면 기준선이 바뀐다).

실행(호스트, numpy/pandas 필요): /usr/bin/python3 scripts/wf_leak_select_check.py \
    --panel services/xgboost-ml/app/models/wf/panel_420_asofpatch.npz --horizon 5 --q 0.3 --top 30
출력: JSON(dict) — stdout. 요약은 stderr.
"""
import argparse
import json
import sys

import numpy as np
import pandas as pd


def auc_signed(col, y):
    """단일 피처 AUC(부호 있음). edge_of 는 |AUC-0.5| 이므로 게이트(#1: 단일 피처 AUC>0.75)
    판정에는 부호 있는 값이 필요하다(방향이 뒤집혀도 0.75 초과는 위험)."""
    m = ~np.isnan(col)
    x, yy = col[m], y[m]
    if len(x) < 50 or yy.min() == yy.max():
        return None
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(len(x), dtype=float)
    ranks[order] = np.arange(1, len(x) + 1, dtype=float)
    xs = x[order]
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[j + 1] == xs[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + 1 + j + 1) / 2.0
        i = j + 1
    n1, n0 = int(yy.sum()), len(yy) - int(yy.sum())
    if n1 == 0 or n0 == 0:
        return None
    return float((ranks[yy == 1].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0))


def edge_of(col, y):
    """wf_wave.edge_of 와 동일 구현(동률은 평균순위)."""
    m = ~np.isnan(col)
    x, yy = col[m], y[m]
    if len(x) < 50 or len(np.unique(x)) < 2 or yy.min() == yy.max():
        return 0.0
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(len(x), dtype=float)
    ranks[order] = np.arange(1, len(x) + 1, dtype=float)
    xs = x[order]
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[j + 1] == xs[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + 1 + j + 1) / 2.0
        i = j + 1
    n1, n0 = int(yy.sum()), len(yy) - int(yy.sum())
    if n1 == 0 or n0 == 0:
        return 0.0
    return abs((ranks[yy == 1].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0) - 0.5)


def make_labels(df, horizon, q):
    """wf_wave.make_labels(kind='quantile') 와 동일."""
    ret = df.groupby("stock_code", sort=False)["price"].transform(
        lambda s: s.shift(-horizon) / s - 1.0)
    day = df["date"]
    hi = ret.groupby(day).transform(lambda s: s.quantile(1 - q))
    lo = ret.groupby(day).transform(lambda s: s.quantile(q))
    y = pd.Series(np.nan, index=df.index, dtype=float)
    y[ret > hi] = 1.0
    y[ret < lo] = 0.0
    return y.values


def classify(X, names, codes, dates):
    """피처별 횡단면/종목 내 변동성 분류.

    ⚠ 결측을 '시장레벨'로 오분류하지 않도록 **관측이 있는 날짜만** 센다:
    어떤 날짜에 비결측 종목이 2개 미만이면 그 날짜는 판정 대상이 아니다(모른다).
    (실측 함정: institution_ownership_pct 처럼 결측 100% 인 컬럼은 nunique<=1 이
     '값이 모두 같다'가 아니라 '값이 없다'인데, 이 구분 없이는 시장레벨로 잡힌다.)
    """
    out = {}
    dfx = pd.DataFrame(X, columns=names)
    dfx["_d"] = dates
    dfx["_c"] = codes
    for n in names:
        col = dfx[n].values.astype(float)
        valid = ~np.isnan(col)
        cov = float(np.mean(valid & (np.nan_to_num(col) != 0)))
        grp_d = dfx.groupby("_d")[n]
        nval = grp_d.count().values                 # 날짜별 비결측 개수
        nuq = grp_d.nunique(dropna=True).values
        m = nval >= 2                               # 판정 가능한 날짜만
        xsec = float(np.mean(nuq[m] <= 1)) if m.any() else None
        grp_c = dfx.groupby("_c")[n]
        nvalc = grp_c.count().values
        nuqc = grp_c.nunique(dropna=True).values
        mc = nvalc >= 5                             # 종목별로 5회 이상 관측된 종목만
        s_med = float(np.median(nuqc[mc])) if mc.any() else None
        out[n] = {
            "coverage_nonzero": cov,
            "xsec_const_rate": xsec,          # None = 판정 불가(관측 부족)
            "stock_uniq_median": s_med,
            "n_dates_judged": int(m.sum()),
            "n_stocks_judged": int(mc.sum()),
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", required=True)
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--q", type=float, default=0.3)
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--json-out", default=None)
    a = ap.parse_args()

    z = np.load(a.panel, allow_pickle=True)
    # ⚠ 중복 라벨 제거(실측: 210개 중 14개 중복 → `df[list]` 가 238열로 부풀고 순서가 바뀌어
    #    '이름↔열' 매핑이 깨진다. 그 상태의 선별 결과는 엉뚱한 컬럼을 이름으로 보고한다).
    _seen, names = {}, []
    for _n in [str(x) for x in z["feature_names"]]:
        if _n in _seen:
            _seen[_n] += 1
            names.append(f"{_n}__dup{_seen[_n]}")
        else:
            _seen[_n] = 0
            names.append(_n)
    X = z["X"].astype(np.float64)
    if X.shape[1] != len(names):
        raise SystemExit(f"패널 불일치: X열 {X.shape[1]} vs 이름 {len(names)}")
    dates = np.array([str(d) for d in z["dates"]])
    codes = np.array([str(c) for c in z["codes"]])
    price = z["price"].astype(float)
    df = pd.DataFrame(X, columns=names)
    df["date"], df["stock_code"], df["price"] = dates, codes, price

    cls = classify(X, names, codes, dates)
    # 시장레벨 = 값이 실제로 존재(비영 커버리지 2% 이상)하면서 날짜별로 종목간 값이 하나뿐인 비율 >= 0.9
    market_level = {n for n, v in cls.items()
                    if v["coverage_nonzero"] >= 0.02 and (v["xsec_const_rate"] or 0) >= 0.9}
    # 종목상수 = 값이 존재하면서 종목별 유니크값 중앙값 <= 1 (종목 식별 증폭 위험)
    stock_const = {n for n, v in cls.items()
                   if v["coverage_nonzero"] >= 0.02 and (v["stock_uniq_median"] or 0) <= 1}
    # 결측 지배 컬럼(값이 사실상 없다) — 별도로 센다: '시장레벨'과 구분해야 오분류가 없다
    null_dominated = {n for n, v in cls.items() if v["coverage_nonzero"] < 0.02}

    y = make_labels(df, a.horizon, a.q)
    d = df.copy()
    d["_y"] = y
    d = d[~pd.isna(d["_y"])]
    dd = sorted(d["date"].astype(str).unique())
    n = len(dd)
    step = n // (a.folds + 1)
    base_names = [c for c in names if c in d.columns]

    folds = {}
    for i in range(1, a.folds + 1):
        cut = dd[step * i - 1]
        nxt = dd[min(n - 1, step * (i + 1) - 1)]
        purge = set(dd[max(0, step * i - a.horizon):step * i])
        tr = d[(d["date"] <= cut) & (~d["date"].isin(purge))]
        te = d[(d["date"] > cut) & (d["date"] <= nxt)]
        if min(len(tr), len(te)) < 100:
            folds[f"fold{i}"] = {"skipped": True, "train_rows": len(tr), "test_rows": len(te)}
            continue
        Xtr = np.nan_to_num(tr[base_names].values.astype(np.float32), nan=0.0)
        ytr = tr["_y"].values.astype(int)
        cols = np.std(Xtr, axis=0) > 0
        fn = [f for f, m in zip(base_names, cols) if m]
        Xtr = Xtr[:, cols]
        edges = np.array([edge_of(Xtr[:, j].astype(float), ytr) for j in range(len(fn))])
        order = np.argsort(-edges)[:a.top]
        sel = [fn[j] for j in sorted(int(x) for x in order)]
        # 누수 게이트 #1: 단일 피처 AUC > 0.75 이면 반려 대상
        auc_scores = {}
        for j, f_ in enumerate(fn):
            au = auc_signed(Xtr[:, j].astype(float), ytr)
            if au is not None:
                auc_scores[f_] = round(max(au, 1.0 - au), 4)
        gate1 = {k: v for k, v in auc_scores.items() if v > 0.75}
        sel_mkt = [s for s in sel if s in market_level]
        sel_sc = [s for s in sel if s in stock_const]
        folds[f"fold{i}"] = {
            "train_rows": int(len(ytr)), "test_rows": int(len(te)),
            "test_from": str(te["date"].min()), "test_to": str(te["date"].max()),
            "test_up_rate": float(np.mean(te["_y"].values.astype(int))),
            "n_candidates": len(fn), "n_selected": len(sel),
            "selected_market_level": sel_mkt,
            "selected_stock_constant": sel_sc,
            "selected_stats": {k: cls[k] for k in sel},
            "selected_zero_edge": [k for k in sel if cls[k]["coverage_nonzero"] < 0.02
                                   or (cls[k]["xsec_const_rate"] is None
                                       and cls[k]["stock_uniq_median"] is None)],
            "selected_market_level_share": round(len(sel_mkt) / max(1, len(sel)), 4),
            "selected_stock_constant_share": round(len(sel_sc) / max(1, len(sel)), 4),
            "top_edge": round(float(edges[order[0]]), 4) if len(order) else None,
            "single_feature_auc_gt_075": dict(sorted(gate1.items(), key=lambda kv: -kv[1])[:10]),
            "n_single_feature_auc_gt_075": len(gate1),
            "top5_single_feature_auc": dict(sorted(auc_scores.items(), key=lambda kv: -kv[1])[:5]),
            "cut_edge_selected": round(float(edges[order[-1]]), 4) if len(order) else None,
        }

    rep = {
        "panel": a.panel,
        "panel_shape": list(X.shape),
        "dates": [min(str(x) for x in dates), max(str(x) for x in dates)],
        "n_stocks": int(len(set(codes))),
        "horizon": a.horizon, "q": a.q, "top": a.top, "folds": a.folds,
        "n_features": len(names),
        "n_market_level_in_panel": len(market_level),
        "n_stock_constant_in_panel": len(stock_const),
        "n_null_dominated_in_panel": len(null_dominated),
        "null_dominated_names": sorted(null_dominated),
        "market_level_names": sorted(market_level),
        "folds_detail": folds,
        "verdict": None,
    }
    tot_sel = sum(len(f.get("selected_market_level", [])) for f in folds.values())
    tot_sc = sum(len(f.get("selected_stock_constant", [])) for f in folds.values())
    rep["selected_market_level_total"] = tot_sel
    rep["selected_stock_constant_total"] = tot_sc
    if tot_sel > 0:
        rep["verdict"] = "게이트 위반: 시장레벨 피처가 횡단면 선별에 포함됨"
    elif tot_sc > 0:
        rep["verdict"] = "주의: 종목상수 피처가 선별에 포함됨(종목 식별 증폭 위험)"
    else:
        rep["verdict"] = "통과: 폴드 선별에 시장레벨·종목상수 피처 없음"
    rep["_note"] = "선별 로직은 wf_wave.subset(select='topN') 과 동일 재현. 측정 전용(수정 금지)."

    out = json.dumps(rep, ensure_ascii=False, indent=2)
    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as f:
            f.write(out)
    print(out)
    print(f"\n[요약] 패널 {X.shape[0]}행/{len(set(codes))}종목 · 피처 {len(names)}개 "
          f"(시장레벨 {len(market_level)} / 종목상수 {len(stock_const)})", file=sys.stderr)
    for k, v in folds.items():
        if v.get("skipped"):
            print(f"  {k}: 표본 부족 건너뜀", file=sys.stderr)
            continue
        print(f"  {k}: top{a.top} 중 시장레벨 {len(v['selected_market_level'])}개 "
              f"({v['selected_market_level']}) · 종목상수 {len(v['selected_stock_constant'])}개 "
              f"· 테스트 {v['test_from']}~{v['test_to']} 상승률 {v['test_up_rate']:.3f}",
              file=sys.stderr)
    print(f"[판정] {rep['verdict']}", file=sys.stderr)


if __name__ == "__main__":
    main()

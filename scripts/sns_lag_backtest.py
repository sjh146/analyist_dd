#!/usr/bin/env python3
"""
SNS Lag Walk-Forward (time-split) Backtest
==========================================

Runs a **time-split / walk-forward** backtest for a strategy that uses SNS lag
features to predict the sign of next-day return. No ground-truth DB is required
-- data can be synthesized (``--synthetic``) so the script runs DB-free.

Workflow
--------
1. Load or synthesize a per-stock panel of SNS features + price returns.
2. Compute price-vs-SNS lag features via ``SnsLagFeatures``.
3. Walk-forward split into >= 2 folds: train on rows ``date <= T_i``, test on
   ``T_i < date`` (no time leakage -- all train rows strictly precede test).
4. Fit a model on the training fold, predict the test fold.
5. Report per-fold win_rate / mean_return / sharpe + lag direction histogram,
   and write ``reports/sns_lag_backtest.json``.

Champion contract
-----------------
When a champion ``feature_names.json`` is provided/loaded, the fold matrix is a
0-fill ``(n_rows, n_features)`` array ordered by that file's feature list,
filling each column from the feature dict when the name is present
(``phase_4_backtest.py`` pattern). This keeps matrix width consistent.

Usage
-----
    python3 scripts/sns_lag_backtest.py --synthetic --folds 3
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "services", "xgboost-ml"))

from app.feature_engine.sns_lag_features import SnsLagFeatures  # noqa: E402


def load_champion_feature_names(path: str) -> List[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            names = json.load(fh)
        return [str(n) for n in names] if isinstance(names, list) else []
    except Exception:
        return []


def synthesize_panel(n_stocks: int = 20, n_days: int = 120, seed: int = 42) -> Dict:
    """종목별 SNS 피처 + 가격 수익률 패널을 시드 고정으로 합성한다.

    일부 종목은 SNS 리드(lead=2), 일부는 가격 리드(lead=-3) 구조로 생성해
    시차 lag 피처가 실제 정보를 담도록 한다.
    """
    rng = np.random.default_rng(seed)
    start = date(2026, 1, 1)
    panels: Dict[str, List[Dict]] = {"sns": [], "price": []}
    for s in range(n_stocks):
        stock = f"{100000 + s:06d}"
        dates = [start + timedelta(days=i) for i in range(n_days)]
        ret = rng.normal(0.0002, 0.02, n_days)
        close = 100.0 * np.cumprod(1 + ret)
        lead = 2 if rng.random() < 0.5 else -3
        base = float(rng.uniform(-0.5, 0.5))
        sentiment = np.clip(base + 0.8 * np.roll(ret, -lead)
                            + 0.2 * rng.normal(0, 0.1, n_days), -1.0, 1.0)
        attention = np.abs(np.roll(ret, -lead)) * 5.0 + rng.random(n_days) * 0.3
        attention = np.clip(attention, 0.0, 1.0)
        momentum = np.clip(np.diff(np.concatenate([[0.0], attention])), -1.0, 1.0)
        author_quality = np.clip(0.5 + rng.normal(0, 0.1, n_days), 0.0, 1.0)
        for i, d in enumerate(dates):
            panels["sns"].append(
                {"stock_code": stock, "trade_date": d,
                 "sentiment_score": float(sentiment[i]),
                 "attention_score": float(attention[i]),
                 "momentum_score": float(momentum[i]),
                 "author_quality_score": float(author_quality[i])}
            )
            panels["price"].append(
                {"stock_code": stock, "trade_date": d,
                 "close": float(close[i]), "return": float(ret[i])}
            )
    return panels


def _to_date(v) -> date:
    if isinstance(v, (datetime, pd.Timestamp)):
        return v.date()
    if isinstance(v, str):
        return date.fromisoformat(str(v)[:10])
    return v


def _sign(x: float) -> float:
    return 1.0 if x > 0 else (-1.0 if x < 0 else 0.0)


def walk_forward_splits(dates: List, n_folds: int) -> List[Tuple[List[int], List[int]]]:
    """연속 정렬된 날짜 배열의 워크포워드 (train, test) 행 인덱스 목록.

    같은 날짜에 여러 종목 로우가 겹칠 수 있으므로 **고유 날짜 경계로 분리**한다.
    - 최소 2 폴드.
    - 각 폴드: ``max(train 날짜) < min(test 날짜)`` (고유 날짜 경계 분리로
      시간 누출 원천 차단 — 날짜 레벨 누출 없음).
    - 폴드는 시간순 연속, 날짜 비중첩, 전체 커버.

    Returns
    -------
    list[(list[int], list[int])] — 각 폴드의 train/test 로우 인덱스.
    """
    n_folds = max(2, int(n_folds))
    n = len(dates)
    if n < 3:
        return []
    # 날짜를 열거하고 각 로우의 날짜 순위(인덱스)를 매긴다.
    unique_dates = sorted({_to_date(d) for d in dates})
    n_u = len(unique_dates)
    if n_u < 3:
        return []
    n_folds = min(n_folds, n_u - 1)
    # 고유 날짜 경계 (0 < b < n_u) — 경계 왼쪽 전체가 train, 오른쪽이 test.
    boundaries = np.unique(np.linspace(1, n_u - 1, n_folds, dtype=int)).tolist()
    date_to_rank = {d: i for i, d in enumerate(unique_dates)}
    ranks = np.array([date_to_rank[_to_date(d)] for d in dates])

    splits: List[Tuple[List[int], List[int]]] = []
    b_prev = 0
    for i, b in enumerate(boundaries):
        b_next = boundaries[i + 1] if i + 1 < len(boundaries) else n_u
        train_idx = np.where(ranks < b)[0].tolist()
        test_idx = np.where((ranks >= b) & (ranks < b_next))[0].tolist()
        if train_idx and test_idx:
            splits.append((train_idx, test_idx))
        b_prev = b
    return splits


def build_champion_matrix(features: List[Dict], feature_names: List[str]) -> np.ndarray:
    """champion 순서의 0-fill 행렬 (phase_4 계약).

    ``features`` : (row -> {feature_name: value}) dict 목록. 존재하는 피처만
    채우고(np.nan_to_num), 없는 피처는 0 유지 → 폭 일치.
    """
    n = len(features)
    X = np.zeros((n, len(feature_names)), dtype=np.float32)
    if n == 0 or not feature_names:
        return X
    for j, feat in enumerate(feature_names):
        col = np.array([row.get(feat, 0.0) for row in features], dtype=np.float64)
        X[:, j] = np.nan_to_num(col, nan=0.0, posinf=0.0, neginf=0.0)
    return X


def _fit_logistic(Xtr: np.ndarray, ytr: np.ndarray,
                  iters: int = 300, lr: float = 0.5) -> np.ndarray:
    """sklearn 없이 numpy 로 로지스틱 회귀 가중치 학습.

    ``ytr`` 은 {-1, 0, +1} 부호. 이진 목표 = (y >= 0 → 1) 로 단순화.
    """
    n, d = Xtr.shape
    if n == 0 or d == 0:
        return np.zeros(d)
    if d > 64:  # 과적합/희소 열 보호 — 스쿼어드 가중치 사용.
        Xtr = Xtr[:, :64]
    y_bin = np.where(ytr >= 0, 1.0, 0.0)
    w = np.zeros(Xtr.shape[1])
    for _ in range(iters):
        z = Xtr @ w
        p = 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))
        grad = Xtr.T @ (p - y_bin)
        w -= lr * grad / max(n, 1)
    return w


def _fit_predict(Xtr: np.ndarray, ytr: np.ndarray, Xte: np.ndarray) -> np.ndarray:
    """라이트 웨이스트 로지스틱 예측 → 부호(±1/0)."""
    if Xtr.shape[0] == 0 or Xtr.shape[1] == 0:
        return np.zeros(Xte.shape[0])
    try:
        w = _fit_logistic(Xtr, ytr)
        return np.sign(np.tanh(Xte @ w))
    except Exception:
        return np.zeros(Xte.shape[0])


def run_backtest(panels: Dict, n_folds: int = 3, champion_path: str = "") -> Dict:
    """종목별 lag 피처를 병합하고 워크포워드 백테스트를 실행한다."""
    sns_df = pd.DataFrame(panels["sns"])
    price_df = pd.DataFrame(panels["price"])
    lag = SnsLagFeatures()
    feature_keys = lag.feature_keys

    all_rows: List[Dict] = []
    for code in sorted(sns_df["stock_code"].unique()):
        s = sns_df[sns_df["stock_code"] == code].sort_values("trade_date")
        p = price_df[price_df["stock_code"] == code].sort_values("trade_date")
        feats = lag.compute_for_stock(s, p, stock_code=code)
        merged = s.merge(p[["trade_date", "return"]], on="trade_date",
                         how="inner").sort_values("trade_date")
        for _, row in merged.iterrows():
            rec = {"stock_code": code, "trade_date": _to_date(row["trade_date"]),
                   "return": float(row.get("return", 0.0))}
            for fn in feature_keys:
                rec[fn] = feats.get(fn, 0.0)
            all_rows.append(rec)

    if not all_rows:
        return {"error": "no data", "n_folds": 0, "n_rows": 0, "n_features": 0,
                "folds": [], "aggregate": {}, "lag_direction_distribution": {}}

    all_rows.sort(key=lambda r: (r["trade_date"], r["stock_code"]))

    feature_names = load_champion_feature_names(champion_path) if champion_path else feature_keys
    X = build_champion_matrix(all_rows, feature_names)
    rets = np.array([r["return"] for r in all_rows], dtype=float)
    y = np.array([_sign(v) for v in rets], dtype=float)
    dates = [r["trade_date"] for r in all_rows]

    splits = walk_forward_splits(dates, n_folds)
    folds_out: List[Dict] = []
    lag_hist = {"sns_leads": 0, "price_leads": 0, "no_lead": 0}
    agg_w, agg_r, agg_s, agg_n = 0.0, 0.0, 0.0, 0
    for ti, (train_idx, test_idx) in enumerate(splits):
        Xtr, ytr, Xte = X[np.array(train_idx)], y[np.array(train_idx)], X[np.array(test_idx)]
        ret_te = rets[np.array(test_idx)]
        pred = _fit_predict(Xtr, ytr, Xte)
        sign_pred = np.sign(pred)
        n_test = len(test_idx)
        y_te = y[np.array(test_idx)]
        win = float(np.mean(sign_pred == y_te)) if n_test else 0.0
        mean_ret = float(np.mean(sign_pred * ret_te)) if n_test else 0.0
        sharpe = 0.0
        daily = sign_pred * ret_te
        if n_test > 1 and np.std(daily) > 0:
            sharpe = float(np.mean(daily) / np.std(daily) * np.sqrt(252))
        fold_lag = {"sns_leads": 0, "price_leads": 0, "no_lead": 0}
        for i in test_idx:
            bl = int(round(all_rows[i].get("sns_sentiment_score_best_lag", 0) or 0))
            if bl > 0:
                fold_lag["sns_leads"] += 1
            elif bl < 0:
                fold_lag["price_leads"] += 1
            else:
                fold_lag["no_lead"] += 1
        for k in fold_lag:
            lag_hist[k] += fold_lag[k]
        folds_out.append({
            "fold": ti + 1,
            "train_start": str(dates[train_idx[0]]),
            "train_end": str(dates[train_idx[-1]]),
            "test_start": str(dates[test_idx[0]]),
            "test_end": str(dates[test_idx[-1]]),
            "n_train": len(train_idx), "n_test": n_test,
            "win_rate": round(win, 4), "mean_return": round(mean_ret, 6),
            "sharpe": round(sharpe, 4), "lag_direction": fold_lag,
        })
        agg_w += win * n_test
        agg_r += mean_ret * n_test
        agg_s += sharpe * n_test
        agg_n += n_test

    if agg_n > 0:
        agg_w /= agg_n
        agg_r /= agg_n
        agg_s /= agg_n

    return {
        "n_folds": len(folds_out), "n_rows": int(len(all_rows)),
        "n_features": int(X.shape[1]), "folds": folds_out,
        "aggregate": {"win_rate": round(float(agg_w), 4),
                      "mean_return": round(float(agg_r), 6),
                      "sharpe": round(float(agg_s), 4)},
        "lag_direction_distribution": lag_hist,
    }


def _load_db_panel() -> Optional[Dict]:
    """DB 로딩은 운영 크론에서 연결 예정. 여기서는 None (fail-open)."""
    return None


def main():
    ap = argparse.ArgumentParser(description="SNS lag walk-forward backtest")
    ap.add_argument("--synthetic", action="store_true", help="합성 데이터 사용 (DB 불요)")
    ap.add_argument("--folds", type=int, default=3)
    ap.add_argument("--n-stocks", type=int, default=20)
    ap.add_argument("--n-days", type=int, default=120)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--champion", default="", help="champion feature_names.json 경로")
    ap.add_argument("--out", default=os.path.join(REPO_ROOT, "reports", "sns_lag_backtest.json"))
    args = ap.parse_args()

    panels = synthesize_panel(args.n_stocks, args.n_days, args.seed) if args.synthetic \
        else _load_db_panel()
    if panels is None:
        print("DB 패널 로드 실패/미구현 → --synthetic 사용 권장", file=sys.stderr)
        return 2

    result = run_backtest(panels, n_folds=args.folds, champion_path=args.champion)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False)
    print(f"saved: {args.out}")

    print(f"\nSNS Lag 워크포워드 백테스트 (folds={result.get('n_folds')}, rows={result.get('n_rows')}, features={result.get('n_features')})")
    print("fold | train window                 | test window                | n_test | win_rate | mean_ret   | sharpe")
    for f in result.get("folds", []):
        print(f" {f['fold']:<3} | {f['train_start']} ~ {f['train_end']} | "
              f"{f['test_start']} ~ {f['test_end']} | {f['n_test']:<6} | "
              f"{f['win_rate']:<8.4f} | {f['mean_return']:<10.6f} | {f['sharpe']:.4f}")
    agg = result.get("aggregate", {})
    print(f"\naggregate win_rate={agg.get('win_rate')} mean_return={agg.get('mean_return')} sharpe={agg.get('sharpe')}")
    print(f"lag direction: {result.get('lag_direction_distribution')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""cg283_liquidity_auc.py — AUC 를 '체결 가능 부분집합'에서 다시 채점한다.

WHY(2026-11-15 CG280 실측): 돈 지표 측정기의 체결성 필터 기본값은
`--min-value 1e9` + `--max-day-chg 25` 다. 그 결과 cg280 의 49종목 패널 덤프
11,110행 중 7,957행(71.6%)이 탈락하고 세션당 풀 중앙값이 14종목으로 줄었다.
즉 **모델이 최적화하는 지표(전 행 AUC)의 표본과, 실제로 살 수 있는 표본이
서로 다른 모집단**이다. 북극성 지표(로버스트 AUC)는 전 행에서 재는데 트레이더는
유동 부분집합만 살 수 있다면, 19사이클 무개선의 원인이 '모델'이 아니라
'지표 표본 불일치'일 수 있다 → 학습 없이 기존 덤프 재채점만으로 확인한다.

프로토콜(사전등록):
  - 같은 덤프(같은 폴드·같은 시드), 재학습 없음 → 순수 재채점.
  - 시드 평균 y_pred 로 (fold, date, code) 당 1행.
  - 라벨은 덤프의 y_true 그대로(quantile q0.30, 가운데 결측은 제외).
  - 체결가능 = trading_value >= --min-value AND day_chg_pct < --max-day-chg.
  - 성공 판정: 부분집합 폴드평균 AUC − 전체 폴드평균 AUC >= +0.02 (사전문턱) &
    폴드승률 >= 4/5. 미달이면 '표본 불일치는 AUC 레버가 아니다'로 종결.

사용:
  python3 scripts/cg283_liquidity_auc.py \
    --preds /app/reports/overnight/cg280_preds_49.jsonl \
    --tags CO_smooth_d1_h5,CO_core30_h5 \
    --json-out /app/reports/overnight/cg283_liquidity_auc.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict

import numpy as np
import pandas as pd


def db_connect():
    import psycopg2
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT", "5434")),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def auc_rank(y, s):
    """Mann-Whitney AUC (동순위 평균 rank). sklearn 없이."""
    y = np.asarray(y, dtype=float)
    s = np.asarray(s, dtype=float)
    n1 = float((y == 1).sum())
    n0 = float((y == 0).sum())
    if n1 == 0 or n0 == 0:
        return None
    order = np.argsort(s, kind="mergesort")
    ranks = np.empty(len(s), dtype=float)
    ranks[order] = np.arange(1, len(s) + 1, dtype=float)
    # 동순위 보정
    sv = s[order]
    i = 0
    while i < len(sv):
        j = i
        while j + 1 < len(sv) and sv[j + 1] == sv[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + 1 + j + 1) / 2.0
        i = j + 1
    return float((ranks[y == 1].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0))


def load_preds(path, tag):
    per = defaultdict(list)
    ytrue = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("exp") != tag:
                continue
            if r.get("y_pred") is None or r.get("date") is None or r.get("code") is None:
                continue
            key = (int(r.get("fold") or 0), str(r["date"]), str(r["code"]).zfill(6))
            per[key].append(float(r["y_pred"]))
            if r.get("y_true") is not None:
                ytrue[key] = int(r["y_true"])
    rows = []
    for key, vals in per.items():
        fold, date, code = key
        rows.append({"fold": fold, "date": date, "code": code,
                     "score": float(np.mean(vals)), "y": ytrue.get(key)})
    return rows


def liquidity(conn, codes, dmin, dmax):
    q = ("SELECT stock_code, trade_date, close_price, trading_value FROM market_data "
         "WHERE stock_code = ANY(%s::text[]) AND trade_date BETWEEN %s AND %s")
    px = pd.read_sql(q, conn, params=[list(codes), dmin, dmax])
    px["stock_code"] = px["stock_code"].astype(str).str.zfill(6)
    px = px.sort_values(["stock_code", "trade_date"])
    px["prev"] = px.groupby("stock_code")["close_price"].shift(1)
    px["day_chg"] = (px["close_price"] / px["prev"] - 1.0) * 100.0
    out = {}
    for r in px.itertuples(index=False):
        out[(str(r.trade_date), r.stock_code)] = (r.trading_value, r.day_chg)
    return out


def summarize(folds):
    vals = [v for v in folds if v is not None]
    if not vals:
        return {"n_folds": 0, "mean": None, "std": None, "folds": folds}
    return {"n_folds": len(vals), "mean": round(float(np.mean(vals)), 4),
            "std": round(float(np.std(vals, ddof=1)) if len(vals) > 1 else 0.0, 4),
            "folds": folds}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preds", required=True)
    ap.add_argument("--tags", required=True)
    ap.add_argument("--min-value", type=float, default=1e9)
    ap.add_argument("--max-day-chg", type=float, default=25.0)
    ap.add_argument("--json-out", required=True)
    a = ap.parse_args(argv)

    tags = [t.strip() for t in a.tags.split(",") if t.strip()]
    conn = db_connect()
    blocks, report = {}, {}
    for tag in tags:
        rows = load_preds(a.preds, tag)
        codes = sorted({r["code"] for r in rows})
        dmin = min(r["date"] for r in rows)
        dmax = max(r["date"] for r in rows)
        liq = liquidity(conn, codes, dmin, dmax)
        folds = sorted({r["fold"] for r in rows})
        full, sub, n_full, n_sub, pos_sub = [], [], [], [], []
        for fd in folds:
            rf = [r for r in rows if r["fold"] == fd and r["y"] in (0, 1)]
            rs = []
            for r in rf:
                tv, dc = liq.get((r["date"], r["code"]), (None, None))
                if tv is None or dc is None:
                    continue
                if pd.isna(tv) or pd.isna(dc):
                    continue
                if tv >= a.min_value and dc < a.max_day_chg:
                    rs.append(r)
            full.append(auc_rank([r["y"] for r in rf], [r["score"] for r in rf]))
            sub.append(auc_rank([r["y"] for r in rs], [r["score"] for r in rs]))
            n_full.append(len(rf))
            n_sub.append(len(rs))
            pos_sub.append(int(sum(r["y"] for r in rs)))
        sf, ss = summarize(full), summarize(sub)
        delta = (round(ss["mean"] - sf["mean"], 4)
                 if sf["mean"] is not None and ss["mean"] is not None else None)
        wins = sum(1 for x, y in zip(sub, full)
                   if x is not None and y is not None and x > y)
        ncmp = sum(1 for x, y in zip(sub, full) if x is not None and y is not None)
        verdict = "판정불가"
        if delta is not None and ncmp:
            verdict = ("신호(부분집합 AUC 우위)" if (delta >= 0.02 and wins / ncmp >= 0.8)
                       else "노이즈(표본 불일치는 AUC 레버 아님)")
        report[tag] = {
            "full_auc": sf, "fillable_auc": ss, "delta": delta,
            "fold_win": f"{wins}/{ncmp}",
            "rows_full": n_full, "rows_fillable": n_sub,
            "pos_full": [int(sum(r["y"] for r in rows
                                 if r["fold"] == fd and r["y"] in (0, 1))) for fd in folds],
            "pos_fillable": pos_sub,
            "verdict": verdict,
        }
        print(f"[{tag}] 전체 AUC {sf['mean']}±{sf['std']} {sf['folds']}")
        print(f"[{tag}] 체결가능 AUC {ss['mean']}±{ss['std']} {ss['folds']}")
        print(f"[{tag}] Δ {delta:+.4f} · 폴드승률 {wins}/{ncmp} · "
              f"행 {sum(n_full)}→{sum(n_sub)} ({100*sum(n_sub)/max(1,sum(n_full)):.1f}%) · "
              f"양성 {sum(pos_sub)}/{sum(n_sub)}")
        print(f"[{tag}] 판정: {verdict}")
    out = {"metric_name": "liquidity_subset_auc", "preds": a.preds,
           "min_value": a.min_value, "max_day_chg": a.max_day_chg,
           "threshold": "Δ(부분집합−전체) ≥ +0.02 & 폴드승률 ≥ 4/5 → 신호", "tags": report}
    os.makedirs(os.path.dirname(a.json_out) or ".", exist_ok=True)
    with open(a.json_out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"[ok] {a.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

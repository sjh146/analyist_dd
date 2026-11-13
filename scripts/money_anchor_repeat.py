#!/usr/bin/env python3
"""money_anchor_repeat.py — 돈 지표의 **앵커 반복성** 측정기 (엔지니어 소유).

WHY (실측 배경, 2026-11-13)
  CG242 는 CO_smooth_d1_h5 의 체결성 순기대를 앵커 **1개(2026-11-02)** 에서만 쟀다:
  k=3 +0.834%p/세션(풀평균 +0.501 대비 +0.333) · k=5 +0.781 인데 분할표본은 [+3.201/-2.566]
  unstable 이었다. 그런데 AUC 축 실측(CG240)은 앵커 3개에서 Δ+0.0049/+0.0069/+0.0098 로
  **앵커마다 2배 차이**가 났다 — 즉 앵커 하나의 돈 지표는 그 자체로는 반복성을 보증하지 않는다.
  이 스크립트는 같은 config 를 앵커 3개에서 각각 학습·채점(--dump-preds)한 뒤,
  각 앵커에서 top-k 순기대 − 풀평균(널 기준선)을 계산해 **부호가 반복되는지**만 본다.

판정(사전 등록)
  앵커 3개 중 Δ(top-k − 풀평균) > 0 인 앵커가 2개 이상 AND 앵커평균 Δ ≥ +0.10%p
  AND 앵커별 t 중앙값 ≥ 1.5  → "돈 신호 앵커 반복성 확인"(후속: 게이트 다중앵커화)
  그 외 → "앵커 특이(단일 앵커 돈 지표 보고 금지)" — CG242 류 단일앵커 수치를 승격 근거로 쓰지 않는다.

사용(컨테이너 안에서)
  docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 2400 \
    python -u scripts/money_anchor_repeat.py \
      --panel /app/app/models/wf/panel_200liq_fresh.npz --config CO_smooth_d1_h5 \
      --anchors 2026-11-02,2026-10-27,2026-10-20 --folds 5 --seeds 5 --k 3,5 \
      --json-out /app/reports/overnight/cg244_money_anchor.json'
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fillable_expectancy import FEE_BUY, FEE_SELL, TAX_SELL          # noqa: E402
from fillable_topk_expectancy import (                              # noqa: E402
    apply_filters, baskets, db_connect, enrich, halves, load_rows, pool_series,
    price_series, stat,
)


def run_sweep(a, anchor: str, preds: str) -> int:
    """앵커 1개 학습·채점. preds jsonl 이 이미 있으면 재사용(멱등)."""
    if os.path.exists(preds) and os.path.getsize(preds) > 0 and not a.fresh:
        print(f"[재사용] {anchor} preds={preds} ({os.path.getsize(preds)} B)", flush=True)
        return 0
    cmd = [sys.executable, "-u", "/app/scripts/wf_label_sweep.py",
           "--panel", a.panel, "--asof-date", anchor, "--folds", str(a.folds),
           "--limit", str(a.limit),
           "--seeds", str(a.seeds), "--only", a.config,
           "--dump-preds", preds,
           "--out", f"/app/reports/overnight/cg244/run_{anchor}.jsonl",
           "--summary-out", f"/app/reports/overnight/cg244/run_{anchor}.json"]
    os.makedirs("/app/reports/overnight/cg244", exist_ok=True)
    print(f"[학습] {anchor} folds={a.folds} seeds={a.seeds} → {os.path.basename(preds)}", flush=True)
    p = subprocess.run(cmd, timeout=a.train_timeout)
    return p.returncode


def anchor_money(a, preds: str) -> dict:
    rows, bad = load_rows(preds, a.config)
    if not rows:
        return {"error": f"preds 행 없음(bad={bad})"}
    codes = sorted({r["code"] for r in rows})
    dmin, dmax = min(r["date"] for r in rows), max(r["date"] for r in rows)
    d0 = (pd.Timestamp(dmin) - pd.Timedelta(days=6)).date()
    d1 = (pd.Timestamp(dmax) + pd.Timedelta(days=6 * (a.horizon + 2))).date()
    conn = db_connect()
    series = price_series(conn, codes, d0, d1)
    kept, drop = enrich(rows, series, a.horizon, a.exit)
    fa, _ = apply_filters(kept, a, True)
    if not fa:
        return {"error": "필터 후 행 없음"}
    rt_pct = (FEE_BUY + FEE_SELL + TAX_SELL) * 100.0
    ba = baskets(fa, a.ks, rt_pct)
    bpool = pool_series(fa, rt_pct)
    out = {"n_rows": len(rows), "n_fillable": len(fa), "dropped_price": drop,
           "n_sessions": len(bpool), "pool": stat(list(bpool.values())), "k": {}}
    for k in a.ks:
        s_a = ba[k]
        keys = sorted(set(s_a) & set(bpool))
        diffs = [s_a[x] - bpool[x] for x in keys]
        st = stat(diffs)
        if "mean" in st:                     # stat() 은 mean 키를 쓴다 — 소비자는 delta_mean
            st["delta_mean"] = st.pop("mean")
        out["k"][str(k)] = {
            "arm": stat(list(s_a.values())),
            "delta_vs_pool": st,
            "halves": halves({x: s_a[x] - bpool[x] for x in keys}),
        }
    # 하위 k(모델이 가장 낮게 점수한 종목) — 점수가 '음의 정보'를 갖는지 보는 반대쪽 검사.
    # 풀에서 하위 k 만 빼고 균등매수하는 정책(롱온리 구현 가능)의 기대값을 직접 계산한다.
    bb = baskets(fa, a.ks, rt_pct, reverse=True)
    by_sess = defaultdict(list)
    for r in fa:
        by_sess[(r["fold"], r["date"])].append(r)
    out["bottom_k"], out["pool_ex_bottom"] = {}, {}
    for k in a.ks:
        keys = sorted(set(bb[k]) & set(bpool))
        diffs = [bb[k][x] - bpool[x] for x in keys]
        st = stat(diffs)
        if "mean" in st:
            st["delta_mean"] = st.pop("mean")
        out["bottom_k"][str(k)] = {"arm": stat(list(bb[k].values())),
                                   "delta_vs_pool": st,
                                   "halves": halves({x: bb[k][x] - bpool[x] for x in keys})}
        ex = {}
        for key in keys:
            rs = by_sess[key]
            if len(rs) <= k:
                continue
            low = sorted(rs, key=lambda x: x["score"])[:k]
            tot = sum(x["gross_pct"] for x in rs)
            lowsum = sum(x["gross_pct"] for x in low)
            ex[key] = ((tot - lowsum) / (len(rs) - k)) - rt_pct
        ediffs = [ex[x] - bpool[x] for x in sorted(set(ex) & set(bpool))]
        est = stat(ediffs)
        if "mean" in est:
            est["delta_mean"] = est.pop("mean")
        out["pool_ex_bottom"][str(k)] = {"arm": stat(list(ex.values())),
                                         "delta_vs_pool": est,
                                         "halves": halves({x: ex[x] - bpool[x]
                                                           for x in sorted(set(ex) & set(bpool))})}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="돈 지표 앵커 반복성(체결성 top-k − 풀평균)")
    ap.add_argument("--panel", required=True)
    ap.add_argument("--config", required=True, help="wf_label_sweep 실험명(예 CO_smooth_d1_h5)")
    ap.add_argument("--anchors", required=True, help="YYYY-MM-DD,YYYY-MM-DD,...")
    ap.add_argument("--limit", type=int, default=200,
                    help="패널 meta 와 맞출 유니버스 크기(불일치 시 구동기가 측정무효로 판정)")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--k", default="3,5")
    ap.add_argument("--exit", default="close_h")
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--json-out", required=True)
    ap.add_argument("--train-timeout", type=int, default=1800)
    ap.add_argument("--fresh", action="store_true", help="기존 preds 무시하고 재학습")
    # 체결성 필터 기본값은 fillable_topk_expectancy 와 동일(1급 옵션).
    ap.add_argument("--exclude-limit-up", action="store_true", default=True)
    ap.add_argument("--max-day-chg", type=float, default=25.0)
    ap.add_argument("--min-value", type=float, default=1e9)
    ap.add_argument("--min-price", type=float, default=None)
    ap.add_argument("--max-price", type=float, default=None)
    a = ap.parse_args(argv)
    a.ks = [int(x) for x in str(a.k).split(",") if x.strip()]

    anchors = [x.strip() for x in a.anchors.split(",") if x.strip()]
    res = {"config": a.config, "panel": os.path.basename(a.panel), "anchors": anchors,
           "folds": a.folds, "seeds": a.seeds, "exit": a.exit, "horizon": a.horizon,
           "ks": a.ks,
           "fee_roundtrip_pct": round((FEE_BUY + FEE_SELL + TAX_SELL) * 100.0, 4),
           "per_anchor": {}, "errors": {}}
    for anc in anchors:
        preds = f"/app/reports/overnight/cg244/preds_{anc}_{a.config}.jsonl"
        try:
            rc = run_sweep(a, anc, preds)
            if rc != 0:
                res["errors"][anc] = f"wf_label_sweep rc={rc}"
                continue
            res["per_anchor"][anc] = anchor_money(a, preds)
        except subprocess.TimeoutExpired:
            res["errors"][anc] = f"학습 타임아웃({a.train_timeout}s)"
        except Exception as e:                                   # noqa: BLE001
            res["errors"][anc] = f"{type(e).__name__}: {e}"
        print(f"[{anc}] {json.dumps(res['per_anchor'].get(anc, res['errors'].get(anc)), ensure_ascii=False)[:400]}",
              flush=True)

    ok = {anc: v for anc, v in res["per_anchor"].items() if "k" in v}
    verdict, agg = "판정불가(앵커 실패)", {}
    if len(ok) >= 2:
        for k in a.ks:
            deltas = {anc: v["k"][str(k)]["delta_vs_pool"].get("delta_mean")
                      for anc, v in ok.items()
                      if v["k"].get(str(k), {}).get("delta_vs_pool", {}).get("n")}
            ts = {anc: v["k"][str(k)]["delta_vs_pool"].get("t")
                  for anc, v in ok.items() if v["k"].get(str(k))}
            vals = [d for d in deltas.values() if d is not None]
            tv = sorted(t for t in ts.values() if t is not None)
            pos = sum(1 for d in vals if d > 0)
            mean_d = round(sum(vals) / len(vals), 4) if vals else None
            med_t = tv[len(tv) // 2] if tv else None
            agg[str(k)] = {"delta_by_anchor": deltas, "n_anchor": len(vals),
                           "positive_anchors": pos, "delta_mean": mean_d,
                           "median_t": med_t}
        k0 = str(a.ks[0])
        g = agg.get(k0, {})
        if g.get("positive_anchors", 0) >= 2 and (g.get("delta_mean") or -9) >= 0.10 \
                and (g.get("median_t") or -9) >= 1.5:
            verdict = (f"돈 신호 앵커 반복성 확인(k={k0} Δ평균 {g['delta_mean']:+.3f}%p · "
                       f"양(+)앵커 {g['positive_anchors']}/{g['n_anchor']} · t중앙 {g['median_t']})")
        elif g.get("positive_anchors") == 0 and g.get("n_anchor", 0) >= 2:
            verdict = (f"선택 엣지 없음 — 전 앵커에서 top-k < 풀평균(k={k0} Δ평균 "
                       f"{g['delta_mean']:+.3f}%p · 양(+)앵커 0/{g['n_anchor']} · "
                       f"t중앙 {g['median_t']}) → 단일 앵커 양(+) 수치는 베타로 설명된다")
        else:
            verdict = (f"앵커 특이 — 단일 앵커 돈 지표 보고 금지(k={k0} Δ평균 "
                       f"{(g.get('delta_mean') if g.get('delta_mean') is not None else float('nan')):+.3f}%p · "
                       f"양(+)앵커 {g.get('positive_anchors')}/{g.get('n_anchor')} · "
                       f"t중앙 {g.get('median_t')})")
    res["aggregate"] = agg
    res["verdict"] = verdict

    print("\n=== 앵커별 Δ(top-k − 풀평균) ===")

    def _fmt(x, spec="+.3f"):
        return format(x, spec) if isinstance(x, (int, float)) else "n/a"

    for k in a.ks:
        for anc, v in ok.items():
            d = v["k"][str(k)]["delta_vs_pool"]
            print(f"  k={k} {anc} arm {_fmt(v['k'][str(k)]['arm'].get('mean'))}%p · "
                  f"pool {_fmt(v['pool'].get('mean'))} · Δ {_fmt(d.get('delta_mean'))} "
                  f"(t {d.get('t')}) · 분할 {(v['k'][str(k)]['halves'] or {}).get('stable')}")
    print("\n=== 반대쪽: 하위 k · 하위 k 제외 풀 (롱온리 구현 가능) ===")
    for k in a.ks:
        for anc, v in ok.items():
            b1 = v.get("bottom_k", {}).get(str(k), {})
            b2 = v.get("pool_ex_bottom", {}).get(str(k), {})
            print(f"  k={k} {anc} 하위k {_fmt(b1.get('arm', {}).get('mean'))}%p "
                  f"Δpool {_fmt(b1.get('delta_vs_pool', {}).get('delta_mean'))} "
                  f"(t {b1.get('delta_vs_pool', {}).get('t')}) | "
                  f"하위k제외 {_fmt(b2.get('arm', {}).get('mean'))}%p "
                  f"Δpool {_fmt(b2.get('delta_vs_pool', {}).get('delta_mean'))} "
                  f"(t {b2.get('delta_vs_pool', {}).get('t')} · "
                  f"{(b2.get('halves') or {}).get('stable')})")
    print(f"\n판정: {verdict}")
    if res["errors"]:
        print("오류:", json.dumps(res["errors"], ensure_ascii=False))
    os.makedirs(os.path.dirname(a.json_out) or ".", exist_ok=True)
    with open(a.json_out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print(f"[ok] {a.json_out}")
    # 앵커가 2개 미만이면 rc≠0 — 구동기가 '실행실패'로 기록하게 한다(조용한 판정불가 금지).
    return 0 if len(ok) >= 2 else 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""baseline_ranking_money.py — 비(非)ML 기준 랭킹(모멘텀·반전·저변동)을 모델과 **같은 돈 지표**로 채점.

WHY (실측 배경 — 2026-10-05)
  돈 축 실측(CG96~CG109): 배포·스윕 모델의 top-k 순기대가 세션 풀 평균(무작위 k 기대)을 넘지 못했다
  (CG96 Δ−0.451/+0.181 t<0.5 · CG103 Δ+0.462/−0.006 t<0.9 · CG108 Δ+0.409 t0.98 · CG109 Δ+0.854 t2.01
  이나 분할 unstable). 즉 "현 모델 점수에 돈 정보가 없다"가 8개 런으로 확정됐다.
  그런데 그 비교의 기준선은 ①같은 모델의 다른 arm ②세션 풀 평균뿐이었다 — **단순 팩터 랭킹**
  (모멘텀·단기반전·저변동)이 같은 유니버스·같은 세션에서 돈 초과를 내는지는 한 번도 재지 않았다
  (백로그 L4 '팩터 vs ML' 의 축소판: L4 는 부활 피처 커버리지 0 으로 막혀 있으나, 가격만 쓰는
  팩터는 커버리지 100% 로 지금 계산 가능하다).
  이 스크립트는 champion_robust_eval --dump-preds 의 **같은 행**(같은 exp·fold·date·code)에
  기준 랭킹 점수를 채워 넣고 fillable_topk_expectancy 의 계산 경로(enrich→apply_filters→baskets→
  pool_series→paired_stats)를 그대로 재사용해 Δ(top-k − 풀평균) 을 모델과 나란히 낸다.

시점정합
  점수는 **d일 종가까지의 정보만** 쓴다(진입도 d일 종가). lookback 부족·결측은 그 행을 그 랭킹에서 제외.

판정
  사전문턱(승격 표준과 동일 단위): arm>0 · Δ(pool)≥+0.1%p/세션 · t≥2 · 분할 both_positive.
  ⚠ 이 런은 **스크리닝**이다 — 단일 런·단일 기간이므로 승격 근거가 아니다. 신호가 나오면
  서로소 유니버스/기간 재현(CG109 절차)이 선행한다.

사용(컨테이너 — DB·psycopg2 가 컨테이너에 있다)
  docker exec stock_xgboost_ml sh -c 'cd /app && python scripts/baseline_ranking_money.py \
      --dump /app/reports/overnight/cg109_preds.jsonl --k 3,5,10 --horizon 5 \
      --json-out /app/reports/overnight/cg110_baseline_money.json'
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
from collections import defaultdict

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fillable_topk_expectancy as F  # noqa: E402
from fillable_expectancy import FEE_BUY, FEE_SELL, TAX_SELL  # noqa: E402


def load_dump(path):
    rows, bad = [], 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                continue
            if r.get("date") is None or r.get("code") is None:
                bad += 1
                continue
            rows.append({
                "exp": str(r.get("exp") or ""),
                "fold": int(r.get("fold") or 0),
                "date": str(r["date"]),
                "code": str(r["code"]).zfill(6),
                "score": (float(r["y_pred"]) if r.get("y_pred") is not None else None),
                "fwd_ret": (float(r["fwd_ret"]) if r.get("fwd_ret") is not None else None),
            })
    return rows, bad


def _idx(series, code, date):
    s = series.get(code)
    if not s:
        return None, None
    try:
        return s, s["d"].index(pd.Timestamp(date).date())
    except ValueError:
        return s, None


def _ret(s, i, n):
    """close[i] / close[i-n] − 1 (i·n 유효할 때만)."""
    if i is None or i - n < 0:
        return None
    a, b = F._f(s["c"][i]), F._f(s["c"][i - n])
    if not a or not b or b <= 0:
        return None
    return a / b - 1.0


def _lowvol(s, i, n=20):
    """−std(일간 로그수익, 직전 n일). 클수록 저변동."""
    if i is None or i - n < 0:
        return None
    rets = []
    for j in range(i - n + 1, i + 1):
        a, b = F._f(s["c"][j]), F._f(s["c"][j - 1])
        if not a or not b or b <= 0:
            return None
        rets.append(math.log(a / b))
    m = sum(rets) / len(rets)
    var = sum((x - m) ** 2 for x in rets) / (len(rets) - 1)
    return -math.sqrt(var)


BASELINES = {
    "model": None,                                   # dump 의 y_pred 그대로
    "mom20": lambda s, i: _ret(s, i, 20),
    "mom60": lambda s, i: _ret(s, i, 60),
    "rev5": lambda s, i: (lambda v: None if v is None else -v)(_ret(s, i, 5)),
    "lowvol20": lambda s, i: _lowvol(s, i, 20),
    "rand": lambda s, i: random.random(),           # 기대 Δ≈0 (계측기 자체검증)
}


def build_scores(rows, series, names, seed=0):
    """(exp,fold,date,code) 마다 랭킹별 점수. 모델은 dump y_pred 재사용."""
    random.seed(seed)
    out = {n: [] for n in names}
    for r in rows:
        s, i = _idx(series, r["code"], r["date"])
        for n in names:
            if n == "model":
                sc = r["score"]
            else:
                sc = BASELINES[n](s, i) if (s is not None and i is not None) else None
            out[n].append({**r, "score": sc})
    return out


def evaluate(name, rows, series, a, ks, rt_pct, bpool, exp_off):
    rows = [dict(r) for r in rows if r.get("score") is not None]
    for r in rows:
        r["fold"] = r["fold"] + exp_off.get(r["exp"], 0)
    e, drop = F.enrich(rows, series, a.horizon, a.exit)
    fe, sk = F.apply_filters(e, a, True)
    ba = F.baskets(fe, ks, rt_pct)
    res = {"n_rows": len(rows), "n_rows_priced": len(e), "dropped_price": drop,
           "filtered_out": sk, "k": {}}
    for k in ks:
        sa = ba[k]
        pv = F.paired_stats(sa, bpool)
        res["k"][str(k)] = {"arm": F.stat(list(sa.values())),
                            "halves": F.halves(sa), "paired_vs_pool": pv}
    return res, ba


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="기준 랭킹(비ML) 돈 지표 스크리닝")
    ap.add_argument("--dump", required=True, help="champion_robust_eval --dump-preds jsonl")
    ap.add_argument("--baselines", default="model,mom20,mom60,rev5,lowvol20,rand")
    ap.add_argument("--k", default="3,5,10")
    ap.add_argument("--exit", choices=F.EXITS, default="close_h")
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--exclude-limit-up", action="store_true", default=True)
    ap.add_argument("--no-exclude-limit-up", dest="exclude_limit_up", action="store_false")
    ap.add_argument("--max-day-chg", type=float, default=25.0)
    ap.add_argument("--min-value", type=float, default=1e9)
    ap.add_argument("--min-price", type=float, default=None)
    ap.add_argument("--max-price", type=float, default=None)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--rand-reps", type=int, default=1,
                    help="무작위 랭킹 대조를 N개 시드로 반복 → Δ 의 표준오차(무작위 대비 MDE) 실측")
    ap.add_argument("--standard", action="store_true",
                    help="--json-out 을 metric `fillable_topk_vs_pool` 표준 스키마로 쓴다(구동기 판정 재사용). "
                         "arm 이 되는 랭킹은 --baselines 의 **첫 항목**.")
    ap.add_argument("--split-exps", action="store_true",
                    help="exp(코드슬라이스) 블록을 별도 세션으로 분리 — 기본은 풀링(생산 프로토콜: "
                         "(fold,date) 세션 = 전 유니버스). 풀링이 CG108/CG109 와 같은 단위다.")
    ap.add_argument("--json-out", default=None)
    a = ap.parse_args(argv)
    ks = [int(x) for x in str(a.k).split(",") if x.strip()]
    names = [n.strip() for n in str(a.baselines).split(",") if n.strip()]
    rt_pct = (FEE_BUY + FEE_SELL + TAX_SELL) * 100.0

    rows, bad = load_dump(a.dump)
    if not rows:
        print(f"[FAIL] dump 비어 있음 {a.dump}", file=sys.stderr)
        return 2
    exps = sorted({r["exp"] for r in rows})
    # 기본: exp 블록을 (fold,date) 세션으로 **풀링**(생산 프로토콜과 동일 단위).
    # --split-exps: 블록을 별도 세션으로(30종목/세션) — k 의미가 달라져 참조 수치와 비교 불가.
    exp_off = {e: 1000 * n for n, e in enumerate(exps)} if a.split_exps else {}
    codes = sorted({r["code"] for r in rows})
    dmin = min(r["date"] for r in rows)
    dmax = max(r["date"] for r in rows)
    # lookback 최대 60거래일 ≈ 95일 + 청산 여유
    d0 = (pd.Timestamp(dmin) - pd.Timedelta(days=120)).date()
    d1 = (pd.Timestamp(dmax) + pd.Timedelta(days=6 * (a.horizon + 2))).date()
    print(f"dump={os.path.basename(a.dump)} rows={len(rows)} bad={bad} "
          f"codes={len(codes)} exps={len(exps)} 구간={dmin}~{dmax}")
    conn = F.db_connect()
    series = F.price_series(conn, codes, d0, d1)
    print(f"가격 시리즈: {len(series)}/{len(codes)} 종목 로드")

    scored = build_scores(rows, series, names, seed=a.seed)

    # 공통 널 기준선: 전체 행(모델 점수 보유) 기준 풀 평균 — 모든 랭킹이 **같은** 널을 쓴다.
    base_rows = [dict(r) for r in (scored["model"] if "model" in names else rows)]
    for r in base_rows:
        r["fold"] = r["fold"] + exp_off.get(r["exp"], 0)
    be, _ = F.enrich(base_rows, series, a.horizon, a.exit)
    bfe, _ = F.apply_filters(be, a, True)
    bpool = F.pool_series(bfe, rt_pct)
    bs = F.stat(list(bpool.values()))
    print(f"[널] 세션 풀 평균 {bs.get('mean'):+.3f}%p/세션 (n={bs.get('n')} · t {bs.get('t')} · "
          f"양세션 {bs.get('pos_pct')}%) · 수수료 왕복 {rt_pct:.3f}%p")

    out = {"metric_name": "baseline_ranking_money", "dump": a.dump, "exit": a.exit,
           "horizon": a.horizon, "ks": ks, "fee_roundtrip_pct": round(rt_pct, 4),
           "rows": len(rows), "codes": len(codes), "date_range": [dmin, dmax],
           "pool": {"stat": bs, "halves": F.halves(bpool)}, "baselines": {}}

    print(f"\n{'ranking':<10} {'k':>3} {'arm net%':>9} {'Δ(pool)':>9} {'t':>6} {'양세션%':>7} 분할")
    bsets = {}
    for n in names:
        r, ba = evaluate(n, scored[n], series, a, ks, rt_pct, bpool, exp_off)
        out["baselines"][n] = r
        bsets[n] = ba
        for k in ks:
            b = r["k"][str(k)]
            pv = b["paired_vs_pool"]
            hl = b["halves"] or {}
            print(f"{n:<10} {k:>3} {b['arm'].get('mean', float('nan')):>9.3f} "
                  f"{pv.get('delta_mean', float('nan')):>9.3f} {pv.get('t', float('nan')):>6.2f} "
                  f"{b['arm'].get('pos_pct', float('nan')):>7.1f} {hl.get('stable','-')} "
                  f"[{hl.get('front',{}).get('mean',float('nan')):+.2f}/"
                  f"{hl.get('back',{}).get('mean',float('nan')):+.2f}]")

    # 무작위 랭킹 대조 반복 → Δ 의 표준오차 = '무작위 대비 최소검출효과(MDE)'.
    # 판정 바닥을 실측해 두면 이후 돈 verdict 에 "이 Δ 는 검출 바닥 이하" 를 명시할 수 있다.
    if a.rand_reps > 1 and "rand" in names:
        per_k = {k: [] for k in ks}
        for rep in range(1, a.rand_reps):
            s2 = build_scores(rows, series, ["rand"], seed=a.seed + 1000 * rep)["rand"]
            r2, _ = evaluate("rand", s2, series, a, ks, rt_pct, bpool, exp_off)
            for k in ks:
                d = r2["k"][str(k)]["paired_vs_pool"].get("delta_mean")
                if d is not None:
                    per_k[k].append(d)
        mde = {str(k): {"n": len(v), **({"mean": round(sum(v) / len(v), 4),
                                         "sd": round((sum((x - sum(v) / len(v)) ** 2 for x in v)
                                                      / max(1, len(v) - 1)) ** 0.5, 4),
                                         "min": round(min(v), 4), "max": round(max(v), 4),
                                         "se": round((sum((x - sum(v) / len(v)) ** 2 for x in v)
                                                      / max(1, len(v) - 1)) ** 0.5 / len(v) ** 0.5, 4)}
                                       if v else {})} for k, v in per_k.items()}
        out["rand_mde"] = {"reps": a.rand_reps, "delta_stats_by_k": mde}
        print("\n[무작위 대조 MDE] Δ(top-k − 풀평균) 분포 (reps=%d)" % a.rand_reps)
        for k in ks:
            m = mde[str(k)]
            print(f"  k={k:>2} mean {m.get('mean', float('nan')):+.3f} · sd {m.get('sd', float('nan')):.3f} "
                  f"· min {m.get('min', float('nan')):+.3f} · max {m.get('max', float('nan')):+.3f} "
                  f"· SE {m.get('se', float('nan')):.3f}")

    if a.json_out:
        if a.standard:
            # metric `fillable_topk_vs_pool` 표준 스키마 — 구동기 판정기를 그대로 재사용한다.
            arm_name = names[0]
            ctl_name = "model" if ("model" in names and "model" != arm_name) else arm_name
            cond = {"filtered_out": {"arm": out["baselines"][arm_name]["filtered_out"]},
                    "k": {}, "baseline_pool": {
                        "desc": "세션별 풀 평균(필터 통과 종목 동일비중) − 수수료 = 무작위 k 바스켓 기대값",
                        "stat": bs, "halves": F.halves(bpool),
                        "paired_by_k": {str(k): bsets[arm_name][k] and
                                        F.paired_stats(bsets[arm_name][k], bpool) for k in ks}}}
            for k in ks:
                sa, sc = bsets[arm_name][k], bsets[ctl_name][k]
                cond["k"][str(k)] = {"arm": F.stat(list(sa.values())),
                                     "control": F.stat(list(sc.values())),
                                     "arm_halves": F.halves(sa), "control_halves": F.halves(sc),
                                     "paired": F.paired_stats(sa, sc)}
            pools = defaultdict(int)
            for r in bfe:
                pools[(r["fold"], r["date"])] += 1
            payload = {"metric_name": "fillable_topk_vs_pool", "exit": a.exit, "horizon": a.horizon,
                       "ks": ks, "arm_tag": arm_name, "control_tag": ctl_name,
                       "fee_roundtrip_pct": round(rt_pct, 4), "rows": len(rows),
                       "n_sessions_fillable": len(pools),
                       "pool_median_fillable": int(pd.Series(list(pools.values())).median()),
                       "conditions": {"fillable": cond, "unfiltered": cond},
                       # 표준 스키마 밖(파서가 무시) — 스크리닝 맥락 보존
                       "screen": {"dump": a.dump, "date_range": [dmin, dmax], "codes": len(codes),
                                  "pool_median_note": "unfiltered 와 동일 블록(스크리닝 시 필터만 적용)",
                                  "baselines": {n: {str(k): out["baselines"][n]["k"][str(k)]["paired_vs_pool"]
                                                    for k in ks} for n in names},
                                  "rand_mde": out.get("rand_mde")}}
            out = payload
        os.makedirs(os.path.dirname(a.json_out) or ".", exist_ok=True)
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"[ok] {a.json_out}  (schema={'standard' if a.standard else 'rich'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""CG496 — 머니 Δ 꼬리 의존 분해 (읽기 전용 계측기).

WHY(2026-11-26 CG495 실측): k=3/5/10 부호는 일관(Δ+2.47/+2.62/+1.97)했지만 arm 평균과 중앙값이
갈렸다(k=5 mean +1.683 vs median +0.092, k=10 mean +1.031 vs median −0.838). 즉 '동전 던지기 평균'이
소수 대박 세션에 실려 있을 수 있다. 평균 Δ 하나만 보고 '돈 엣지'라고 쓰면 CG215(꼬리 소수 세션)
같은 사고를 반복한다.

새 학습 없이 **같은 덤프**에서 ① 세션별 Δ(arm top-k − 세션 풀평균)의 상·하위 제거(leave-top-out)
② 절사평균(10%/20%)·중앙값·부호검정(양측 이항) ③ 종목 단위 Δ 귀속과 상위 종목 제외 재측정
④ 폴드별 Δ 를 계산한다. 사전 등록 문턱: 상위 3세션 제거 후 Δ>0 & t≥2 & 10% 절사평균>0 이면
'꼬리 아님', 아니면 '꼬리 의존'. 단 편측 제거만으로 단정하지 말고 대칭 절사 결과를 함께 읽어라
(양측 두꺼운 꼬리와 단일 대박은 다르다).

사용:
  docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 timeout 900 \
    python -u scripts/cg496_tail_decomp.py \
      --arm-jsonl /app/reports/overnight/cg411_dump.jsonl --arm-tag cg409_nogate \
      --control-jsonl /app/reports/overnight/cg411_null.jsonl --control-tag cg409_hashnull \
      --k 5 --stride 5 --exit close_h --horizon 5 --entry next_open \
      --json-out /app/reports/overnight/cg496_tail_decomp.json'
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fillable_topk_expectancy as F  # noqa: E402


def _t(vals):
    """평균과 t (표본표준편차 기준). n<2 면 (None, None)."""
    n = len(vals)
    if n < 2:
        return None, None
    m = sum(vals) / n
    sd = math.sqrt(sum((v - m) ** 2 for v in vals) / (n - 1))
    if sd == 0:
        return m, None
    return m, m / (sd / math.sqrt(n))


def _trimmed(vals, frac):
    """양측 frac 절사평균. 남는 표본이 2 미만이면 (None, None)."""
    v = sorted(vals)
    cut = int(len(v) * frac)
    if len(v) - 2 * cut < 2:
        return None, None
    return _t(v[cut:len(v) - cut])


def _sign_p(pos, neg):
    """양측 이항검정 p (공정동전). 부호 일관성만 본다."""
    n = pos + neg
    if n == 0:
        return None
    k = min(pos, neg)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    return round(min(1.0, 2 * tail), 4)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm-jsonl", required=True)
    ap.add_argument("--arm-tag", default=None)
    ap.add_argument("--control-jsonl", required=True)
    ap.add_argument("--control-tag", default=None)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--stride", type=int, default=5)
    ap.add_argument("--exit", default="close_h")
    ap.add_argument("--entry", default="next_open")
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--json-out", default=None)
    a = ap.parse_args(argv)

    rt_pct = (F.FEE_BUY + F.FEE_SELL + F.TAX_SELL) * 100.0
    arm, bad_a = F.load_rows(a.arm_jsonl, a.arm_tag)
    ctl, bad_c = F.load_rows(a.control_jsonl, a.control_tag)
    if not arm or not ctl:
        print(f"[FAIL] 행 없음 arm={len(arm)} control={len(ctl)}", file=sys.stderr)
        return 2
    codes = sorted({r["code"] for r in arm} | {r["code"] for r in ctl})
    dmin = min(r["date"] for r in arm + ctl)
    dmax = max(r["date"] for r in arm + ctl)
    d0 = (F.pd.Timestamp(dmin) - F.pd.Timedelta(days=8)).date()
    d1 = (F.pd.Timestamp(dmax) + F.pd.Timedelta(days=8 * (a.horizon + 2))).date()
    conn = F.db_connect()
    series = F.price_series(conn, codes, d0, d1)
    conn.close()

    arm_e, drop_a = F.enrich(arm, series, a.horizon, a.exit, a.entry)
    ctl_e, drop_c = F.enrich(ctl, series, a.horizon, a.exit, a.entry)

    filt = argparse.Namespace(exclude_limit_up=True, max_day_chg=25.0,
                              min_value=1e9, min_price=None, max_price=None)
    fa, out_a = F.apply_filters(arm_e, filt, on=True)
    fc, out_c = F.apply_filters(ctl_e, filt, on=True)

    keep = F.stride_keys(fa, a.stride)
    basket = F.baskets(fa, [a.k], rt_pct)[a.k]
    pool = F.pool_series(fa, rt_pct)
    cbasket = F.baskets(fc, [a.k], rt_pct)[a.k]

    keys = [k for k in basket if k in pool and (keep is None or k in keep)]
    keys.sort(key=lambda x: (x[0], x[1]))
    deltas = [basket[k] - pool[k] for k in keys]
    dctl = [basket[k] - cbasket[k] for k in keys if k in cbasket]

    mean, t = _t(deltas)
    res = {
        "metric_name": "cg496_tail_decomp",
        "spec": {"k": a.k, "stride": a.stride, "exit": a.exit, "entry": a.entry,
                 "horizon": a.horizon, "fee_roundtrip_pct": round(rt_pct, 4),
                 "arm_tag": a.arm_tag, "control_tag": a.control_tag},
        "rows": {"arm": len(fa), "control": len(fc), "arm_dropped_price": drop_a,
                 "filtered_out_arm": out_a, "bad_arm": bad_a, "bad_control": bad_c},
        "n_sessions": len(keys),
        "base": {"delta_pool_mean": None if mean is None else round(mean, 4),
                 "delta_pool_t": None if t is None else round(t, 2),
                 "delta_ctl_mean": (round(sum(dctl) / len(dctl), 4) if dctl else None),
                 "arm_mean": round(sum(basket[k] for k in keys) / len(keys), 4) if keys else None,
                 "pool_mean": round(sum(pool[k] for k in keys) / len(keys), 4) if keys else None,
                 "arm_median": round(sorted(basket[k] for k in keys)[len(keys) // 2], 4) if keys else None,
                 "delta_median": round(sorted(deltas)[len(deltas) // 2], 4) if deltas else None,
                 "pos": sum(1 for d in deltas if d > 0), "neg": sum(1 for d in deltas if d < 0),
                 "sign_test_p": _sign_p(sum(1 for d in deltas if d > 0),
                                        sum(1 for d in deltas if d < 0))},
    }

    order = sorted(range(len(keys)), key=lambda i: deltas[i], reverse=True)
    res["leave_out"] = {}
    for n in (1, 3, 5):
        if len(deltas) - n >= 5:
            kept = [deltas[i] for i in order[n:]]
            m2, t2 = _t(kept)
            res["leave_out"][f"drop_top{n}"] = {
                "n": len(kept), "delta_mean": round(m2, 4),
                "t": None if t2 is None else round(t2, 2),
                "dropped_sessions": [keys[i][1] for i in order[:n]],
                "dropped_sum_share_pct": round(100 * sum(deltas[i] for i in order[:n])
                                               / sum(deltas), 1) if sum(deltas) else None}
        if len(deltas) - n >= 5:
            kept = [deltas[i] for i in order[:len(deltas) - n]]
            m2, t2 = _t(kept)
            res["leave_out"][f"drop_bottom{n}"] = {
                "n": len(kept), "delta_mean": round(m2, 4),
                "t": None if t2 is None else round(t2, 2)}

    res["trimmed"] = {}
    for frac in (0.10, 0.20):
        m2, t2 = _trimmed(deltas, frac)
        if m2 is not None:
            res["trimmed"][f"trim{int(frac * 100)}"] = {
                "n": len(deltas) - 2 * int(len(deltas) * frac),
                "delta_mean": round(m2, 4), "t": None if t2 is None else round(t2, 2)}

    by_sess = defaultdict(list)
    for r in fa:
        by_sess[(r["fold"], r["date"])].append(r)
    code_delta = defaultdict(float)
    for k in keys:
        rs = by_sess[k]
        gross = [x["gross_pct"] for x in rs]
        pg = sum(gross) / len(gross)
        picks = sorted(rs, key=lambda x: -x["score"])[:a.k]
        for p in picks:
            code_delta[p["code"]] += (p["gross_pct"] - pg) / a.k
    tot = sum(code_delta.values())
    top = sorted(code_delta.items(), key=lambda kv: -kv[1])[:5]
    res["names"] = {
        "n_names": len(code_delta),
        "total_delta_sum": round(tot, 2),
        "top5": [{"code": c, "delta": round(v, 3),
                  "share_pct": round(100 * v / tot, 1) if tot else None} for c, v in top],
        "top5_share_pct": round(100 * sum(v for _, v in top) / tot, 1) if tot else None,
    }

    per_fold = defaultdict(list)
    for k, d in zip(keys, deltas):
        per_fold[k[0]].append(d)
    res["by_fold"] = {str(f): {"n": len(v), "delta_mean": round(sum(v) / len(v), 4),
                               "pos": sum(1 for x in v if x > 0), "neg": sum(1 for x in v if x < 0)}
                      for f, v in sorted(per_fold.items())}

    res["leave_names_out"] = {}
    for cut, label in ((5, "drop_top5_names"), (10, "drop_top10_names")):
        drop = {c for c, _ in sorted(code_delta.items(), key=lambda kv: -kv[1])[:cut]}
        fa2 = [r for r in fa if r["code"] not in drop]
        if not fa2:
            continue
        b2 = F.baskets(fa2, [a.k], rt_pct)[a.k]
        p2 = F.pool_series(fa2, rt_pct)
        ks2 = [k for k in keys if k in b2 and k in p2]
        d2 = [b2[k] - p2[k] for k in ks2]
        m2, t2 = _t(d2)
        m3, t3 = _trimmed(d2, 0.10)
        res["leave_names_out"][label] = {
            "dropped_codes": sorted(drop), "n_sessions": len(d2),
            "delta_mean": None if m2 is None else round(m2, 4),
            "t": None if t2 is None else round(t2, 2),
            "trim10_mean": None if m3 is None else round(m3, 4),
            "trim10_t": None if t3 is None else round(t3, 2),
            "pos": sum(1 for x in d2 if x > 0), "neg": sum(1 for x in d2 if x < 0)}

    d3 = res["leave_out"].get("drop_top3", {})
    tr = res["trimmed"].get("trim10", {})
    ok = (d3.get("delta_mean") is not None and d3["delta_mean"] > 0
          and (d3.get("t") or 0) >= 2
          and (tr.get("delta_mean") or 0) > 0)
    res["verdict"] = "꼬리 아님(강건)" if ok else "꼬리 의존(소수 세션/종목)"
    res["verdict_rule"] = "상위 3세션 제거 후 Δ>0 & t≥2 & 10% 절사평균>0"

    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False, indent=1)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

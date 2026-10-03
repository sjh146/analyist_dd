#!/usr/bin/env python3
"""확률 스케일 진단 — 모델 확률분포 vs 소비자 절대문턱(라이브 경로 정합).

배경(MT116, 2026-10-02): 배포 챔피언 교체 후 swing 신호가 전면 소멸했다 —
val AUC 는 올랐는데(0.5513->0.5548) 라이브 스코어 top20 max 가 0.4733 로 소비자 문턱
0.55 를 한 건도 못 넘었다. 즉 판정 지표(AUC)와 **매매 경로(절대문턱)** 가 다른 축이다.

이 프로브는 walk-forward 테스트 폴드의 **OOS 확률**(wf_label_sweep --dump-preds 산출)을 읽어
(a) 확률분포, (b) 문턱별 선택 건수·정밀도·실현수익, (c) 신뢰도(reliability) 곡선,
(d) top-k 정책을 확률 문턱으로 번역한 값 을 낸다. 읽기 전용.

용법:
  python3 scripts/prob_threshold_probe.py <preds.jsonl> [--thresholds 0.45,0.5,0.55] [--topk 10]
"""
import argparse
import json
import math
import sys
from collections import defaultdict


def auc(pairs):
    """pairs: list[(score, y)] -> AUC (Mann-Whitney, 동점 0.5 가중)."""
    import bisect
    pos = sorted(s for s, y in pairs if y == 1)
    neg = sorted(s for s, y in pairs if y != 1)
    if not pos or not neg:
        return None
    tot = 0.0
    for s in pos:
        lo = bisect.bisect_left(neg, s)
        hi = bisect.bisect_right(neg, s)
        tot += lo + 0.5 * (hi - lo)
    return tot / (len(pos) * len(neg))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("preds")
    ap.add_argument("--thresholds", default="0.45,0.50,0.52,0.55,0.60")
    ap.add_argument("--topk", type=int, default=10)
    a = ap.parse_args()

    rows = []
    with open(a.preds) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("y_pred") is None:
                continue
            rows.append(r)
    if not rows:
        print("행이 없다")
        return 1
    exps = sorted({str(r.get("exp")) for r in rows})
    print(f"파일: {a.preds}")
    print(f"실험: {exps}")
    print(f"행수 {len(rows)} · 폴드 {sorted({int(r['fold']) for r in rows})}")
    dates = sorted({str(r["date"]) for r in rows})
    print(f"날짜 {len(dates)} ({dates[0]}~{dates[-1]})")
    pos = sum(int(r["y_true"]) for r in rows)
    print(f"양성률 {pos/len(rows):.4f} ({pos}/{len(rows)})")

    p = [float(r["y_pred"]) for r in rows]
    y = [int(r["y_true"]) for r in rows]
    print(f"\n확률 분포: min={min(p):.4f} p50={sorted(p)[len(p)//2]:.4f} "
          f"p90={sorted(p)[int(.9*len(p))]:.4f} p99={sorted(p)[int(.99*len(p))]:.4f} max={max(p):.4f}")

    brier = sum((pi - yi) ** 2 for pi, yi in zip(p, y)) / len(y)
    eps = 1e-12
    ll = -sum(yi * math.log(min(max(pi, eps), 1 - eps)) + (1 - yi) * math.log(1 - min(max(pi, eps), 1 - eps))
             for pi, yi in zip(p, y)) / len(y)
    print(f"AUC(pooled)={auc(list(zip(p, y))):.4f} · Brier={brier:.4f} · logloss={ll:.4f}")

    print("\n문턱별 선택(절대문턱 정책):")
    print(f"  {'문턱':>6} {'건수':>7} {'비율':>7} {'정밀도(y=1)':>11} {'평균 fwd_ret':>13}")
    all_fwd = [float(r["fwd_ret"]) for r in rows if r.get("fwd_ret") is not None]
    allfwd_mean = sum(all_fwd) / len(all_fwd) if all_fwd else float("nan")
    print(f"  {'(전체)':>6} {len(rows):>7} {1.0:>7.3f} {pos/len(rows):>11.4f} {allfwd_mean:>13.4f}")
    for t in [float(x) for x in a.thresholds.split(",")]:
        sel = [r for r in rows if float(r["y_pred"]) > t]
        if not sel:
            print(f"  {t:>6.2f} {0:>7} {0.0:>7.3f} {'-':>11} {'-':>13}")
            continue
        prec = sum(int(r["y_true"]) for r in sel) / len(sel)
        fr = [float(r["fwd_ret"]) for r in sel if r.get("fwd_ret") is not None]
        frm = sum(fr) / len(fr) if fr else float("nan")
        print(f"  {t:>6.2f} {len(sel):>7} {len(sel)/len(rows):>7.3f} {prec:>11.4f} {frm:>13.4f}")

    # top-k/day 정책 -> 확률 문턱 번역
    k = a.topk
    byday = defaultdict(list)
    for r in rows:
        byday[str(r["date"])].append(r)
    cut, precs, rets = [], [], []
    for d, g in byday.items():
        g = sorted(g, key=lambda r: -float(r["y_pred"]))
        top = g[:k]
        cut.append(float(top[-1]["y_pred"]) if top else float("nan"))
        precs.append(sum(int(r["y_true"]) for r in top) / len(top))
        fr = [float(r["fwd_ret"]) for r in top if r.get("fwd_ret") is not None]
        if fr:
            rets.append(sum(fr) / len(fr))
    cut = [c for c in cut if c == c]
    print(f"\ntop-{k}/일 정책: 평균 커트오프 확률 = {sum(cut)/len(cut):.4f} "
          f"(중앙 {sorted(cut)[len(cut)//2]:.4f}) · 정밀도 평균 {sum(precs)/len(precs):.4f} "
          f"· top{k} 평균 fwd_ret {sum(rets)/len(rets) if rets else float('nan'):.4f} vs 전체 {allfwd_mean:.4f}")

    # 신뢰도 곡선
    print("\n신뢰도(10분위: 예측평균 vs 실제양성률):")
    order = sorted(range(len(rows)), key=lambda i: p[i])
    n = len(rows)
    for b in range(10):
        idx = order[b * n // 10:(b + 1) * n // 10]
        if not idx:
            continue
        pm = sum(p[i] for i in idx) / len(idx)
        ym = sum(y[i] for i in idx) / len(idx)
        print(f"  분위{b+1}: 예측 {pm:.4f} · 실제 {ym:.4f} · 차이 {pm-ym:+.4f} (n={len(idx)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

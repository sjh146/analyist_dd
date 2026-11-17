#!/usr/bin/env python3
"""CG323 후속(2026-11-18) — 짝 ΔAUC 의 (fold,date) 블록 부트스트랩 검정력 측정.

WHY: 폴드 std 0.10 만 보면 "Δ+0.02 는 검출 불가"로 오독하기 쉽다. 폴드 분산은 국면(폴드별 시장)이
지배하고, 실제 판정에 쓰는 것은 같은 날 같은 종목의 **짝** 차이다. 날짜 블록(그날의 횡단면 전체)을
재표집하면 ΔAUC 의 표준오차와 80% 검정력 최소검출 크기를 실측할 수 있다.

실측(2026-11-18, cg323_preds.jsonl 20,398행·85블록): Δ+0.0103 · SE 0.0033 · 95% CI [+0.0038, +0.0165]
· 최소검출 0.0092 → 사전문턱 +0.02 는 '검출 가능한 크기'였고, 19사이클 무개선의 원인은 검정력이 아니라
가용 신호의 크기다.

사용(컨테이너 안, 덤프가 있는 곳):
    docker exec stock_xgboost_ml python /app/reports/_cg323_power.py
    # 또는 이 파일을 컨테이너로 복사해 --preds/--arm/--ctl 로 다른 런에도 그대로 쓴다
"""
from __future__ import annotations

import argparse
import collections
import json

import numpy as np
from sklearn.metrics import roc_auc_score


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preds", default="/app/reports/overnight/cg323_preds.jsonl")
    ap.add_argument("--arm", default="CG323_ss_h5")
    ap.add_argument("--ctl", default="CG323_ctl_h5")
    ap.add_argument("--n-boot", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()

    d = collections.defaultdict(list)
    for line in open(a.preds):
        r = json.loads(line)
        d[(r["exp"], r["fold"], r["date"])].append((r["y_true"], r["y_pred"]))

    keys = sorted({(f, dt) for (_e, f, dt) in d})
    diffs = []
    for f, dt in keys:
        arm = d.get((a.arm, f, dt))
        ctl = d.get((a.ctl, f, dt))
        if not arm or not ctl:
            continue
        y = [x[0] for x in ctl]
        if len(set(y)) < 2:          # 날짜 안에 양/음이 없으면 AUC 정의 불가
            continue
        diffs.append(roc_auc_score(y, [x[1] for x in arm])
                     - roc_auc_score(y, [x[1] for x in ctl]))
    diffs = np.asarray(diffs, dtype=float)
    if len(diffs) < 10:
        print(f"블록 부족({len(diffs)}) — 판정 불가")
        return 2

    rng = np.random.default_rng(a.seed)
    boot = np.array([diffs[rng.integers(0, len(diffs), len(diffs))].mean()
                     for _ in range(a.n_boot)])
    se = float(boot.std())
    print(json.dumps({
        "blocks": int(len(diffs)),
        "delta_mean": round(float(diffs.mean()), 4),
        "se_block_bootstrap": round(se, 4),
        "ci95": [round(float(np.percentile(boot, 2.5)), 4),
                 round(float(np.percentile(boot, 97.5)), 4)],
        "min_detectable_delta_80pct": round(2.8 * se, 4),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""money_seed_family.py — 같은 프로토콜의 **시드별 돈 지표 JSON** 을 한 파일로 집계한다.

WHY (실측 2026-11-19 CG355): 돈 지표(fillable_topk_expectancy --json-out)는 한 시드당 한 파일을
쓴다. 그런데 백로그 항목의 판정은 "시드 하나가 +0.365(t 1.14)"가 아니라 **시드 부호가 유지되는가**
여야 한다(하드룰: 단일 시드 결과를 개선/악화로 보고 금지). 3시드를 돌려 놓고도 파일이 3개라
파서가 없어 결과가 '판정불가 (metric=None)'로 닫히는 일을 막고, 시드 간 부호 불일치를
기계적으로 드러낸다.

실측 예(CG352 3시드, k=3): s0 +0.365(t 1.14) / s1 +0.171(t 0.81) / s2 −0.543(t −1.00)
→ 시드 평균 Δ −0.002 · 전 시드 양(+) 아님 = s0 의 '이득'은 시드 잡음이었다.

사용:
  python3 scripts/money_seed_family.py \
    --glob "services/xgboost-ml/reports/overnight/cg352_money_s*.json" \
    --ks 3,5,10 --out data/reports/overnight/cg355_money_seed_family.json

출력 스키마는 model_engineer_cycle.parse_money_seed_family 가 읽는다(per_k[k].mean_delta 등).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import statistics
import sys

SEED_RE = re.compile(r"_s(\d+)\.json$")


def load_one(path: str, null: str = "control") -> dict:
    """시드 1개 요약을 읽는다.

    null="pool" 이면 짝 비교를 **그날 체결가능 풀 평균**(무작위 k 기대)으로 바꾼다 — control(같은 런
    재학습 대조군)과 다른 질문이다: "시장 대비 알파가 있는가". CG440 형 '상대 엣지' 항목의 판정 널.
    """
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    m = SEED_RE.search(path)
    seed = int(m.group(1)) if m else None
    fill = ((d.get("conditions") or {}).get("fillable") or {})
    bp = fill.get("baseline_pool") or {}
    bp_pair = bp.get("paired_by_k") or {}
    ks = {}
    for k, blk in (fill.get("k") or {}).items():
        p = (blk or {}).get("paired") or {}
        if null == "pool":
            p = bp_pair.get(str(k)) or p
            ctrl_mean = (bp.get("stat") or {}).get("mean")
        else:
            ctrl_mean = ((blk or {}).get("control") or {}).get("mean")
        ks[str(k)] = {
            "delta": p.get("delta_mean"),
            "t": p.get("t"),
            "sd": p.get("sd"),
            "n_dates": p.get("n_dates"),
            "pos": p.get("pos"),
            "neg": p.get("neg"),
            "arm_mean": ((blk or {}).get("arm") or {}).get("mean"),
            "control_mean": ctrl_mean,
            "arm_pos_pct": ((blk or {}).get("arm") or {}).get("pos_pct"),
            "arm_stable": (((blk or {}).get("arm_halves") or {}).get("stable")),
            "arm_front": (((blk or {}).get("arm_halves") or {}).get("front") or {}).get("mean"),
            "arm_back": (((blk or {}).get("arm_halves") or {}).get("back") or {}).get("mean"),
        }
    return {
        "file": os.path.basename(path),
        "seed": seed,
        "null": null,
        "arm_tag": d.get("arm_tag"),
        "control_tag": (bp.get("desc") if null == "pool" else d.get("control_tag")),
        "exit": d.get("exit"),
        "horizon": d.get("horizon"),
        "fee_roundtrip_pct": d.get("fee_roundtrip_pct"),
        "n_sessions_fillable": d.get("n_sessions_fillable"),
        "pool_median_fillable": d.get("pool_median_fillable"),
        "pair": d.get("pair"),
        "rows": d.get("rows"),
        "k": ks,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="시드별 돈 지표 집계(시드 부호 유지 검정)")
    ap.add_argument("--glob", required=True, help="시드별 --json-out 파일 글롭(호스트 경로)")
    ap.add_argument("--ks", default="3,5,10", help="집계할 top-k 목록(쉼표)")
    ap.add_argument("--null", choices=("control", "pool"), default="control",
                    help="짝 비교의 널: control=같은 런 대조군(재학습) / pool=그날 체결가능 풀 평균(무작위 k 기대)")
    ap.add_argument("--out", required=True, help="집계 JSON 출력 경로")
    a = ap.parse_args(argv)

    files = sorted(glob.glob(a.glob))
    if not files:
        print(f"[FAIL] 파일 없음: {a.glob}", file=sys.stderr)
        return 2
    runs = [load_one(p, null=a.null) for p in files]
    runs = [r for r in runs if r.get("k")]
    if not runs:
        print("[FAIL] fillable.k 블록이 있는 파일이 없다(체결성 필터 경로 확인)", file=sys.stderr)
        return 3
    runs.sort(key=lambda r: (r.get("seed") is None, r.get("seed")))

    ks = [k.strip() for k in a.ks.split(",") if k.strip()]
    per_k = {}
    for k in ks:
        rows = [{"seed": r["seed"], **r["k"][k]} for r in runs if k in r["k"]]
        deltas = [x["delta"] for x in rows if isinstance(x.get("delta"), (int, float))]
        if not deltas:
            per_k[k] = {"n_seeds": 0, "per_seed": rows}
            continue
        n_pos = sum(1 for d in deltas if d > 0)
        arms = [x["arm_mean"] for x in rows if isinstance(x.get("arm_mean"), (int, float))]
        per_k[k] = {
            "n_seeds": len(deltas),
            "per_seed": rows,
            "mean_delta": round(statistics.mean(deltas), 4),
            "mean_arm_mean": (round(statistics.mean(arms), 4) if arms else None),
            "n_positive_arm_seeds": sum(1 for a in arms if a > 0),
            "sd_delta_across_seeds": (round(statistics.pstdev(deltas), 4)
                                      if len(deltas) > 1 else None),
            "min_delta": round(min(deltas), 4),
            "max_delta": round(max(deltas), 4),
            "n_positive_seeds": n_pos,
            "all_positive": n_pos == len(deltas),
            "all_negative": n_pos == 0,
            "pooled_pos": sum(int(x.get("pos") or 0) for x in rows),
            "pooled_neg": sum(int(x.get("neg") or 0) for x in rows),
            "mean_t": round(statistics.mean([x["t"] for x in rows
                                             if isinstance(x.get("t"), (int, float))] or [0.0]), 3),
        }

    payload = {
        "metric_name": "money_seed_family",
        "null": a.null,
        "ks": ks,
        "seeds": [r.get("seed") for r in runs],
        "n_seeds": len(runs),
        "arms": {"arm_tags": [r.get("arm_tag") for r in runs],
                 "control_tags": [r.get("control_tag") for r in runs],
                 "exit": runs[0].get("exit"), "horizon": runs[0].get("horizon"),
                 "fee_roundtrip_pct": runs[0].get("fee_roundtrip_pct"),
                 "n_sessions_fillable": runs[0].get("n_sessions_fillable"),
                 "pool_median_fillable": runs[0].get("pool_median_fillable")},
        "pair": runs[0].get("pair"),
        "per_k": per_k,
        "files": [r["file"] for r in runs],
        "measured_at": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    for k in ks:
        b = per_k.get(k) or {}
        if not b.get("n_seeds"):
            continue
        print(f"k={k}: mean Δ{b['mean_delta']:+.4f} (sd {b.get('sd_delta_across_seeds')}) "
              f"· 시드 양(+) {b['n_positive_seeds']}/{b['n_seeds']} · pooled pos/neg "
              f"{b['pooled_pos']}/{b['pooled_neg']} · mean t {b['mean_t']:+.2f}"
              f" · arm 절대 {b.get('mean_arm_mean')} (양(+) 시드 {b.get('n_positive_arm_seeds')}/{b['n_seeds']})")
    print(f"[out] {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

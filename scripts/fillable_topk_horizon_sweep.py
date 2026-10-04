#!/usr/bin/env python3
"""fillable_topk_horizon_sweep.py — 돈 지표의 **청산 호라이즌 dose-response** 집계기(읽기 전용).

WHY(실측 2026-10-04 CG96): CG96 은 exit=close_h **h5** 에서 모델 top-k 가 세션 풀 평균(무작위 k
기대)을 넘지 못함(Δ ≤ +0.23%p/세션 · t < 1.1 · k=3..30)을 보였다 → 그 양(+) 순기대(+2.2%p/세션)는
**시장 베타**이지 모델 엣지가 아니다. 그런데 스킬에 라벨/보유기간 불일치(챔피언 라벨 h1 vs 트레이더
보유 5일)가 기록돼 있으므로, 돈 축을 닫기 전에 **다른 청산 호라이즌**에서 신호가 사는지 봐야 한다.

이 집계기는 같은 dump·같은 유니버스·같은 필터에서 `exit=close_h` 의 h∈{1,3,5,10} 을 각각 계산해
`top-k − 풀평균`(베타 제거 초과)을 **한 JSON** 으로 낸다. primary(기본 h5) 블록은
`fillable_topk_expectancy.py` 와 **동일 스키마**이므로 기존 판정기(`fillable_topk_vs_pool`)가 그대로
읽고, 전체 호라이즌 표는 `horizon_table` 키로 함께 실린다(사전등록 문턱·판정기는 바꾸지 않는다).

사용(컨테이너 안에서)
  docker exec stock_xgboost_ml sh -c 'cd /app && python scripts/fillable_topk_horizon_sweep.py \
      --arm-jsonl /app/reports/overnight/cg95_q05_all.jsonl --arm-tag cg92_q05 \
      --control-jsonl /app/reports/overnight/cg95_q30_all.jsonl --control-tag cg92_q30 \
      --k 3,5,10,20,30 --horizons 1,3,5,10 --primary 5 \
      --json-out /app/reports/overnight/cg97_money.json'
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fillable_topk_expectancy as fte   # noqa: E402  (핵심 계산 재사용 — 판정 로직 중복 금지)


def _fill(block):
    return (block.get("conditions") or {}).get("fillable") or {}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="돈 지표 청산 호라이즌 dose-response")
    ap.add_argument("--arm-jsonl", required=True)
    ap.add_argument("--arm-tag", default=None)
    ap.add_argument("--control-jsonl", required=True)
    ap.add_argument("--control-tag", default=None)
    ap.add_argument("--k", default="3,5,10,20,30")
    ap.add_argument("--horizons", default="1,3,5,10", help="close_h 청산 호라이즌 목록(쉼표)")
    ap.add_argument("--primary", type=int, default=5, help="기존 판정기가 읽을 기준 호라이즌")
    ap.add_argument("--json-out", required=True)
    a = ap.parse_args(argv)
    hs = [int(x) for x in str(a.horizons).split(",") if x.strip()]

    tmp = tempfile.mkdtemp(prefix="fths_")
    blocks = {}
    for h in hs:
        out_h = os.path.join(tmp, f"h{h}.json")
        rc = fte.main(["--arm-jsonl", a.arm_jsonl, "--arm-tag", a.arm_tag or "",
                       "--control-jsonl", a.control_jsonl, "--control-tag", a.control_tag or "",
                       "--k", a.k, "--exit", "close_h", "--horizon", str(h),
                       "--json-out", out_h])
        if rc != 0:
            print(f"[FAIL] horizon {h} rc={rc}", file=sys.stderr)
            return rc
        with open(out_h, encoding="utf-8") as f:
            blocks[h] = json.load(f)
    if a.primary not in blocks:
        print(f"[FAIL] primary h={a.primary} 가 horizons={hs} 안에 없다", file=sys.stderr)
        return 2

    merged = dict(blocks[a.primary])           # primary = 기존 판정기가 읽는 블록(h5 스키마 동일)
    table = {}
    for h in hs:
        f = _fill(blocks[h])
        pool = f.get("baseline_pool") or {}
        pk = pool.get("paired_by_k") or {}
        ks = f.get("k") or {}
        table[str(h)] = {
            "n_sessions": blocks[h].get("n_sessions_fillable"),
            "pool_mean": (pool.get("stat") or {}).get("mean"),
            "pool_t": (pool.get("stat") or {}).get("t"),
            "arm_mean": {k: (ks[k].get("arm") or {}).get("mean") for k in ks},
            "arm_minus_pool": {k: (pk[k] or {}).get("delta_mean") for k in pk},
            "arm_minus_pool_t": {k: (pk[k] or {}).get("t") for k in pk},
        }
    merged["horizon_table"] = table
    merged["primary_horizon"] = a.primary

    os.makedirs(os.path.dirname(a.json_out) or ".", exist_ok=True)
    with open(a.json_out, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    print(f"[ok] {a.json_out} · horizons {hs} (primary h{a.primary})")
    for h in hs:
        row = table[str(h)]
        cells = " ".join(f"k{k}:{row['arm_minus_pool'][k]:+.3f}(t{row['arm_minus_pool_t'][k]})"
                         for k in sorted(row["arm_minus_pool"], key=int))
        print(f"  h{h}: 풀평균 {row['pool_mean']:+.3f}%p/세션 · {cells}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""dump_split_exp.py — wf_label_sweep 의 다중 arm 덤프(jsonl)를 exp 별 파일로 쪼갠다.

WHY (실측 2026-11-25 CG482): `baseline_ranking_money.py` 는 덤프에 exp 가 2개면 두 arm 의 점수를
**한 랭킹으로 풀링**한다(모델 점수의 분포가 섞임). `--split-exps` 는 세션을 코드공간으로 오프셋해
k 의 의미를 바꾼다. 그래서 arm 별 돈 지표를 같은 프로토콜로 재려면 exp 를 물리적으로 분리해야 한다.
분리 후 `fillable_topk_expectancy.py --arm-jsonl A --control-jsonl B` 로 짝 비교(block t)를 한다.

사용:
  python3 scripts/dump_split_exp.py /app/reports/overnight/cg483_arms.jsonl
  → /app/reports/overnight/cg483_arms__<exp>.jsonl (arm 마다 1개) + 짝 무결성 검사 출력
"""
from __future__ import annotations

import json
import os
import sys


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    src = argv[1]
    outdir = argv[2] if len(argv) > 2 else os.path.dirname(src)
    os.makedirs(outdir, exist_ok=True)
    base = os.path.splitext(os.path.basename(src))[0]
    groups: dict[str, list[str]] = {}
    with open(src, encoding="utf-8") as f:
        for ln in f:
            if not ln.strip():
                continue
            r = json.loads(ln)
            groups.setdefault(r.get("exp") or "NA", []).append(ln)

    keys = {}
    for exp, lines in groups.items():
        path = os.path.join(outdir, f"{base}__{exp}.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(lines)
        keys[exp] = set()
        for ln in lines:
            r = json.loads(ln)
            keys[exp].add((r.get("fold"), r.get("date"), r.get("code")))
        print(f"{exp}: {len(lines)}행 -> {path}")

    ks = list(keys.values())
    same = all(k == ks[0] for k in ks[1:]) if ks else False
    print(f"[짝 무결성] exps={len(ks)} key_equal={same} n_keys={len(ks[0]) if ks else 0}")
    return 0 if (same and len(ks) >= 2) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

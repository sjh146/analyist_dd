#!/usr/bin/env python3
"""robust_paired_seeds.py 의 창(fold) 클러스터 SE 계산 검증.

왜(2026-11-23 CG432 실측): 시드 11개 짝 Δ 는 평균 +0.0237 · SE 0.0071 · t 3.35 로 '문턱 초과'
처럼 보였지만 창별로 쪼개면 fold1 −0.0190(3/11) · fold2 −0.0071(5/11) · fold3 +0.0970(11/11)
로 이득이 한 창에 몰려 있었다. 시드는 같은 날짜창을 공유하므로 시드 SE 는 유사반복으로
과소추정된다 → 창을 클러스터로 본 SE 를 함께 기록해야 한다.

이 테스트는 합성 데이터로 두 성질을 고정한다:
  ① 이득이 한 창에 몰리면 cluster SE > seed SE (과소추정 검출)
  ② 창마다 같은 크기로 이득이 나면 cluster SE ≈ 0 (정상 신호를 노이즈로 만들지 않음)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import io
import contextlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import robust_paired_seeds as rps  # noqa: E402


def _write(d, seed, fold_aucs, tag):
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"{tag}_s{seed}.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"universe": {"seed": seed},
                   "auc_per_date_mean": sum(fold_aucs) / len(fold_aucs),
                   "folds": [{"window": [f"2026-1{i+1}-01", f"2026-1{i+1}-05"], "auc_mean": a}
                             for i, a in enumerate(fold_aucs)]}, f)
    return p


def _run(tmp, a_specs, b_specs):
    a = [_write(os.path.join(tmp, "a"), i, v, "a") for i, v in a_specs]
    b = [_write(os.path.join(tmp, "b"), i, v, "b") for i, v in b_specs]
    out = os.path.join(tmp, "out.json")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = rps.main(["--arm-a", *a, "--arm-b", *b, "--label-a", "A", "--label-b", "B", "--out", out])
    assert rc == 0, f"rc={rc}"
    with open(out, encoding="utf-8") as f:
        return json.load(f)


def case_concentrated(tmp):
    # 6시드 · 2창. 창2에서만 +0.10 (시드별 잡음 ±0.004), 창1은 0.
    a, b = [], []
    for s in range(6):
        j = 0.004 * ((s % 3) - 1)
        b.append((s, [0.50, 0.50]))
        a.append((s, [0.50 + j, 0.60 + j]))
    r = _run(tmp, a, b)
    need = r["window_cluster"]
    assert r["delta_mean"] >= 0.05, r["delta_mean"]
    assert r["pos_seeds"] == 6, r["pos_seeds"]
    assert need["pos_folds"] == 1, need["pos_folds"]
    assert need["se"] > r["delta_se"], (need["se"], r["delta_se"])
    assert abs(need["fold_mean_deltas"][0]) < 1e-9, need["fold_mean_deltas"]
    print(f"OK ① 집중: seed SE {r['delta_se']:.4f} < cluster SE {need['se']:.4f} · pos_folds {need['pos_folds']}/2")


def case_consistent(tmp):
    # 6시드 · 3창. 모든 창에서 +0.03 → 창 간 분산 0 → cluster SE 0
    a, b = [], []
    for s in range(6):
        j = 0.004 * ((s % 3) - 1)
        b.append((s, [0.50, 0.50, 0.50]))
        a.append((s, [0.53 + j, 0.53 + j, 0.53 + j]))
    r = _run(tmp, a, b)
    need = r["window_cluster"]
    assert need["pos_folds"] == 3, need["pos_folds"]
    assert need["se"] == 0.0, need["se"]
    print(f"OK ② 일관: cluster SE {need['se']:.4f} · fold_mean_deltas {need['fold_mean_deltas']}")


def case_fold_mismatch(tmp):
    # 폴드 수가 다르면 클러스터를 만들지 않는다(비교 불가)
    a = [_write(os.path.join(tmp, "ma"), i, [0.5, 0.5], "a") for i in range(3)]
    b = [_write(os.path.join(tmp, "mb"), i, [0.5], "b") for i in range(3)]
    out = os.path.join(tmp, "out_mismatch.json")
    with contextlib.redirect_stdout(io.StringIO()):
        rc = rps.main(["--arm-a", *a, "--arm-b", *b, "--label-a", "A", "--label-b", "B", "--out", out])
    assert rc == 0
    with open(out, encoding="utf-8") as f:
        r = json.load(f)
    assert r["window_cluster"] is None, r["window_cluster"]
    assert r["fold_mismatch"], r["fold_mismatch"]
    print("OK ③ 폴드 불일치 → window_cluster=None · fold_mismatch 기록")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        for fn in (case_concentrated, case_consistent, case_fold_mismatch):
            fn(tmp)
    print("ALL PASS (3/3)")


if __name__ == "__main__":
    main()

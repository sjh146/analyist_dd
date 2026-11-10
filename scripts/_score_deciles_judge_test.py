#!/usr/bin/env python3
"""_score_deciles_judge_test.py — score_deciles 파서·판정기 자체점검(컨테이너 불필요).

왜: CG190 은 'ρ ≥ 0.5 이고 D10−D1 ≥ 0.3%p' 라는 **사전 등록 문턱**으로 판정한다.
판정기가 조용히 반대로 동작하면(예: 미달을 신호로) 축을 잘못 닫거나 잘못 연다 →
합성 요약으로 경계값을 고정한다.
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import model_engineer_cycle as mec  # noqa: E402


def make(path, means, rho=None, paired_t=2.0):
    dec = [{"decile": i + 1, "mean_pct": v, "t_stat": 1.0, "pos_sessions_pct": 50.0, "n_rows": 20}
           for i, v in enumerate(means)]
    d10_minus_d1 = round(means[-1] - means[0], 4)
    doc = {"generated_at": "2026-11-11T07:00:00+0900",
           "config": {"deciles": len(means)},
           "models": {"cand": {"n_sessions": 113, "n_rows_after_filter": 2200,
                               "n_rows_scored": 2251, "deciles": dec,
                               "spearman_rho": rho if rho is not None else 0.9,
                               "d10_minus_d1_pct": d10_minus_d1,
                               "paired_d10_d1": {"mean_pct": d10_minus_d1, "t_stat": paired_t,
                                                 "pos": 60, "neg": 53,
                                                 "front_pct": 0.1, "back_pct": 0.2},
                               "pool_mean_pct": -0.2, "pool_t": -1.0,
                               "monotonic": None}}}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    return path


def main():
    ok = 0
    fails = []
    tmp = tempfile.mkdtemp()
    item = {"id": "CG190", "metric": "score_deciles"}

    # [1] 단조 증가 + Δ 충분 → 신호
    p = make(os.path.join(tmp, "a.json"), [-1.5 + 0.25 * i for i in range(10)], rho=0.95)
    parsed = mec.parse_score_deciles(p, 0.0)
    v, d, delta = mec.judge_score_deciles(item, parsed)
    (ok := ok + 1) if v == "신호" else fails.append(f"[1] 신호 기대, 실제 {v}: {d[:80]}")
    assert parsed["deciles"][0]["decile"] == 1 and parsed["deciles"][-1]["decile"] == 10

    # [2] ρ 는 높지만 Δ 작음(0.1%p) → 노이즈
    p = make(os.path.join(tmp, "b.json"), [-0.5 + 0.011 * i for i in range(10)], rho=0.9)
    v, d, _ = mec.judge_score_deciles(item, mec.parse_score_deciles(p, 0.0))
    (ok := ok + 1) if v == "노이즈" else fails.append(f"[2] 노이즈 기대, 실제 {v}: {d[:80]}")

    # [3] Δ 는 크지만 ρ 낮음(비단조) → 노이즈
    p = make(os.path.join(tmp, "c.json"), [0, 2, -2, 1, 3, -3, 2, 4, -1, 5], rho=0.2)
    v, d, _ = mec.judge_score_deciles(item, mec.parse_score_deciles(p, 0.0))
    (ok := ok + 1) if v == "노이즈" else fails.append(f"[3] 노이즈 기대, 실제 {v}: {d[:80]}")

    # [4] D10 이 D1 보다 낮음(역방향) → 노이즈
    p = make(os.path.join(tmp, "d.json"), [1.0 - 0.2 * i for i in range(10)], rho=-0.9)
    v, d, _ = mec.judge_score_deciles(item, mec.parse_score_deciles(p, 0.0))
    (ok := ok + 1) if v == "노이즈" else fails.append(f"[4] 노이즈 기대, 실제 {v}: {d[:80]}")

    # [5] 요약 미갱신(mtime floor) → 판정불가
    p = make(os.path.join(tmp, "e.json"), [0.1 * i for i in range(10)])
    v, d, _ = mec.judge_score_deciles(item, mec.parse_score_deciles(p, time.time() + 60))
    (ok := ok + 1) if v == "판정불가" else fails.append(f"[5] 판정불가 기대, 실제 {v}: {d[:80]}")

    # [6] 파일 없음 → 판정불가
    v, d, _ = mec.judge_score_deciles(item, mec.parse_score_deciles(os.path.join(tmp, "nope.json"), 0.0))
    (ok := ok + 1) if v == "판정불가" else fails.append(f"[6] 판정불가 기대, 실제 {v}: {d[:80]}")

    # [7] 분위 3개 미만 → 표본 결함(판정불가)
    p = make(os.path.join(tmp, "f.json"), [0.1, 0.2])
    v, d, _ = mec.judge_score_deciles(item, mec.parse_score_deciles(p, 0.0))
    (ok := ok + 1) if v == "판정불가" else fails.append(f"[7] 판정불가 기대, 실제 {v}: {d[:80]}")

    print(f"{ok}/7 PASS" + ("" if not fails else " · FAIL: " + " | ".join(fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())

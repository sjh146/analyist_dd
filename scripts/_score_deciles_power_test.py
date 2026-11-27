#!/usr/bin/env python3
"""_score_deciles_power_test.py — score_deciles 판정의 검정력(MDE) 조건 회귀 (CG527).

실측 결함(2026-11-27): CG523·CG524·CG526 은 표본 17~21세션·h5 중첩이라 MDE80 이 4.61%p
(등록 문턱 0.3%p 의 15배)였다. 스크립트는 power 블록을 요약 JSON 에 썼지만 구동기 파서가
그것을 버리고 `judge_score_deciles` 가 무조건 '노이즈'(=랭킹 무효, 축 종결)로 닫았다 —
검출 불가능한 표본으로 축을 닫은 셈이다. 수리: ① 파서가 power 를 통과 ② 판정은
MDE80 > 0.6%p(2×문턱) 이면 '판정불가(저검정력)'.

실행: python3 scripts/_score_deciles_power_test.py
"""
from __future__ import annotations

import importlib.util
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location("mec", os.path.join(HERE, "model_engineer_cycle.py"))
mec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mec)

FAILS = []


def check(name, ok, extra=""):
    print(("PASS  " if ok else "FAIL  ") + name + (f" — {extra}" if extra else ""))
    if not ok:
        FAILS.append(name)


def parsed(rho, delta, mde=None, req=442):
    p = {
        "spearman_rho": rho, "d10_minus_d1_pct": delta,
        "deciles": [{"decile": 1, "mean_pct": -3.70}, {"decile": 10, "mean_pct": -4.07}],
        "paired_d10_d1": {"mean_pct": delta, "t_stat": -0.62, "pos": 7, "neg": 10,
                          "front_pct": -0.21, "back_pct": -0.50},
        "pool_mean_pct": -3.92, "n_sessions": 17, "n_rows_after_filter": 8506,
    }
    if mde is not None:
        p["power"] = {"d10_minus_d1": {"mde80_pct": mde,
                                       "required_sessions": {"0.3": req, "0.2": 1000, "0.1": 4000}}}
    return p


def main() -> int:
    v, d, delta = mec.judge_score_deciles({}, parsed(-0.3576, -0.3661, mde=4.61))
    check("① MDE80 4.61%p → 판정불가(저검정력)", v == "판정불가(저검정력)", f"{v} | {d[:120]}")
    check("①-b 델타 보존(원장 실측치 유지)", delta == -0.3661, str(delta))
    check("①-c 디테일에 MDE·필요세션 병기", "MDE80 4.61" in d and "442" in d, d[-90:])

    v2, _, _ = mec.judge_score_deciles({}, parsed(0.10, -0.05, mde=0.20))
    check("② MDE80 0.20%p·미달 → 노이즈", v2 == "노이즈", v2)

    v3, _, _ = mec.judge_score_deciles({}, parsed(0.10, 0.05, mde=0.60))
    v4, _, _ = mec.judge_score_deciles({}, parsed(0.10, 0.05, mde=0.61))
    check("③ 경계 0.60=노이즈 / 0.61=판정불가", (v3, v4) == ("노이즈", "판정불가(저검정력)"),
          f"{v3} / {v4}")

    v5, _, _ = mec.judge_score_deciles({}, parsed(0.85, 0.8487, mde=4.61))
    check("④ ρ0.85·Δ+0.85%p → 신호", v5 == "신호", v5)

    v6, _, _ = mec.judge_score_deciles({}, parsed(0.10, -0.05, mde=None))
    check("⑤ power 없음 → 노이즈(하위호환)", v6 == "노이즈", v6)

    f = os.path.join(PROJ, "data/reports/overnight/cg524_baselines.json")
    if os.path.exists(f):
        pp = mec.parse_score_deciles(f, 0.0, label="live")
        pw = (pp.get("power") or {}).get("d10_minus_d1") or {}
        check("⑥ 실측 요약 파서가 power 통과", isinstance(pw.get("mde80_pct"), (int, float)),
              f"mde80={pw.get('mde80_pct')}")
    else:
        print("SKIP  ⑥ 실측 요약 파일 없음(cg524_baselines.json)")

    print(f"\n{len(FAILS)} FAIL / 7 checks")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""_score_deciles_json_out_test.py — 구동기 metric kind `score_deciles` 요약 경로 자체점검 (CG523).

실측 결함(2026-11-27 15:36): CG523 명령은 `--json-out data/reports/overnight/cg523_summary.json` 로
쓰는데 summary_path 가 `--out`/`--summary-out` 만 봐서 기본 경로(cg190_score_deciles.json)를 읽었고,
그 파일 mtime 이 실행 시작 이전이라 판정이 '판정불가'로 닫혔다(측정은 실제로 성공: ρ −0.103).
① `--json-out` 을 요약 경로로 인식 ② 인자 없으면 종전 기본 경로 유지(하위호환).

실행: python3 scripts/_score_deciles_json_out_test.py
"""
from __future__ import annotations

import importlib.util
import os
import sys

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


def main() -> int:
    cmd = ("cd /home/jhshi/analyist_dd && timeout 1200 python3 scripts/cg523_live_universe_deciles.py "
           "--json-out data/reports/overnight/cg523_summary.json")
    sp = mec.summary_path("score_deciles", cmd)
    check("① --json-out 추출", sp == os.path.join(PROJ, "data/reports/overnight/cg523_summary.json"), sp)
    sp_def = mec.summary_path("score_deciles", "python3 scripts/cg190_score_deciles.py")
    check("② 기본 경로 유지", sp_def.endswith("services/xgboost-ml/reports/overnight/cg190_score_deciles.json"),
          sp_def)
    real = os.path.join(PROJ, "data/reports/overnight/cg523_summary.json")
    if os.path.exists(real):
        p = mec.parse_by_metric({"id": "CG523", "metric": "score_deciles"}, real, 0.0)
        check("③ 실측 파싱", not p.get("error"), str(p.get("error") or "")[:80])
        v, d, dl = mec.judge_by_metric({"id": "CG523", "metric": "score_deciles"}, p)
        check("③ 판정=노이즈(ρ<0.5·Δ<0.3)", v == "노이즈" and dl is not None, f"{v} · {d[:100]}")
    else:
        print("SKIP ③ 실측 산출물 없음")
    print(f"\n{len(FAILS)} FAIL")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())

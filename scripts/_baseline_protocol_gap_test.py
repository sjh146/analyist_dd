#!/usr/bin/env python3
"""`baseline_protocol_gap` 파서·판정기 회귀 테스트 (2026-11-14, CG260 후속 수리).

왜: CG260 은 rc=0·16.9분으로 완주해 요약 JSON 에 4 arm 폴드 평균을 남겼는데 metric 파서가 없어
'판정불가'로 닫혔다. 파서를 추가했으니 계약을 고정한다:
  ① 요약에서 arms(폴드 통계)·pairs(같은 런 짝 Δ)를 만든다
  ② **per_exp 를 만들지 않는다**(스코어보드가 패널 교체 잡음을 '돌파'로 오독하는 것을 막는 계약)
  ③ 짝 Δ 평균 < +0.02 → '노이즈', ≥ +0.02 이고 양(+) 폴드 ≥ n−1 → '신호있음'
  ④ 등록 기준선과의 격차는 보고하되, 0.02 미달이면 '기준선 유지'

실행: python3 scripts/_baseline_protocol_gap_test.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_engineer_cycle as M  # noqa: E402

FAIL = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f" — {extra}" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


def write_summary(tmp, arms):
    """wf_label_sweep 요약 스키마(results[].folds{foldN:{mean}}) 를 만든다."""
    results = []
    for exp, folds in arms.items():
        results.append({
            "exp": exp, "desc": exp, "n_rows_used": 32000, "n_effective_features": [30],
            "folds": {f"fold{i+1}": {"mean": v} for i, v in enumerate(folds)},
        })
    p = os.path.join(tmp, "sum.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"finished_at": "2026-11-14T00:00:00+00:00", "results": results}, f)
    return p


ITEM = {
    "id": "CG900", "metric": "baseline_protocol_gap",
    "paired_reference": "LS", "arm_list": ["CAND"],
    "registered_baseline_value": 0.5406,
}

with tempfile.TemporaryDirectory() as tmp:
    # 케이스 A: 실측과 같은 형태(짝 Δ +0.0112 · 양(+) 4/5) → 노이즈
    p = write_summary(tmp, {
        "LS":        [0.5326, 0.5543, 0.5017, 0.5305, 0.5317],
        "CAND":      [0.5273, 0.5634, 0.5207, 0.5389, 0.5567],
        "WORSE":     [0.5295, 0.5548, 0.4933, 0.5287, 0.5271],
    })
    parsed = M.parse_by_metric(ITEM, p, 0.0)
    check("A1 arms 파싱", set(parsed.get("arms", {})) == {"LS", "CAND", "WORSE"}, str(parsed.get("error")))
    check("A2 per_exp 미생성", "per_exp" not in parsed or not parsed.get("per_exp"))
    pairs = parsed.get("pairs") or {}
    check("A3 짝 Δ CAND", pairs.get("CAND", {}).get("mean") == 0.0112, str(pairs.get("CAND")))
    check("A4 양(+) 폴드 수", pairs.get("CAND", {}).get("pos") == 4, str(pairs.get("CAND")))
    check("A5 기준선 격차", parsed.get("baseline_gap_vs_registered") == -0.0104,
          str(parsed.get("baseline_gap_vs_registered")))
    v, d, delta = M.judge_by_metric(ITEM, parsed, {})
    check("A6 판정=노이즈", v == "노이즈", f"{v} / {d}")
    check("A7 delta", delta == 0.0112, str(delta))

    # 케이스 B: 문턱 초과(+0.03 · 5/5) → 신호있음
    p2 = write_summary(tmp, {
        "LS":   [0.50, 0.50, 0.50, 0.50, 0.50],
        "CAND": [0.53, 0.53, 0.53, 0.53, 0.53],
    })
    parsed2 = M.parse_by_metric(ITEM, p2, 0.0)
    v2, d2, delta2 = M.judge_by_metric(ITEM, parsed2, {})
    check("B1 판정=신호있음", v2 == "신호있음", f"{v2} / {d2}")
    check("B2 delta +0.03", delta2 == 0.03, str(delta2))

    # 케이스 C: 폴드 키 불일치 → 짝 Δ 없음(판정불가로 조용히 '노이즈' 만들지 않는다)
    p3 = os.path.join(tmp, "sum3.json")
    with open(p3, "w", encoding="utf-8") as f:
        json.dump({"results": [
            {"exp": "LS", "folds": {"a": {"mean": 0.5}, "b": {"mean": 0.5}}},
            {"exp": "CAND", "folds": {"x": {"mean": 0.6}, "y": {"mean": 0.6}}},
        ]}, f)
    parsed3 = M.parse_by_metric(ITEM, p3, 0.0)
    check("C1 폴드 불일치 → pairs 비어 있음", not (parsed3.get("pairs") or {}))
    v3, d3, _ = M.judge_by_metric(ITEM, parsed3, {})
    check("C2 판정불가(가짜 노이즈 금지)", v3 == "판정불가", f"{v3} / {d3}")

print()
print(f"{len(FAIL)} FAIL / 11 checks" if FAIL else "ALL PASS (11 checks)")
sys.exit(1 if FAIL else 0)

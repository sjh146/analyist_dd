#!/usr/bin/env python3
"""자체점검 — judge_by_metric 이 protocol_noise_floor / protocol_noise_curve 를 판정하는가.

왜: 파서만 배선되고 judge 분기가 없어 원장 헤드라인 verdict 가 '판정불가'로 기록됐다
(실측 2026-10-09 06:05 CG143). 이 테스트가 그 회귀를 막는다.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_engineer_cycle as me  # noqa: E402

FAILS = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


# ── A) protocol_noise_floor ────────────────────────────────────────────────
parsed_under = {
    "metric_name": "protocol_noise_floor", "anchors": {"A": "2026-10-07", "B": "2026-10-06"},
    "determinism_same_anchor": True, "auc": {"a1": 0.4531, "a2": 0.4531, "b1": 0.4356},
    "anchor_shift_delta": 0.0175, "threshold": 0.02, "noise_over_threshold": False,
    "folds": {"a1": [0.4595, 0.4467], "b1": [0.4091, 0.4621]}, "verdict": "잡음바닥 < 문턱",
}
v, d, delta = me.judge_by_metric({"metric": "protocol_noise_floor", "id": "CG143"}, parsed_under)
check("floor: 문턱 미달 → verdict 노출", v == "잡음바닥 < 문턱")
check("floor: delta=None (개선 카운터 무관)", delta is None)
check("floor: detail 에 수치", "Δ0.0175" in d and "0.4531" in d)

parsed_over = dict(parsed_under, anchor_shift_delta=0.025,
                   verdict="잡음바닥 >= 문턱 — 사전문턱 +0.02 는 잡음과 구분 불가")
v2, _, _ = me.judge_by_metric({"metric": "protocol_noise_floor", "id": "CG143b"}, parsed_over)
check("floor: 문턱 초과 → verdict 노출", v2.startswith("잡음바닥 >= 문턱"))

v3, _, _ = me.judge_by_metric({"metric": "protocol_noise_floor", "id": "CG143c"},
                              dict(parsed_under, determinism_same_anchor=False,
                                   verdict="결정성 위반 — 프로토콜 비결정성 결함"))
check("floor: 결정성 위반 → verdict 노출", v3.startswith("결정성 위반"))

v4, d4, d4d = me.judge_by_metric({"metric": "protocol_noise_floor", "id": "CG143d"},
                                 {"error": "요약 파일 없음"})
check("floor: 파서 오류 → 판정불가", v4 == "판정불가" and d4 == "요약 파일 없음" and d4d is None)

# ── B) protocol_noise_curve ────────────────────────────────────────────────
parsed_curve = {
    "metric_name": "protocol_noise_curve", "base_anchor": "2026-10-07",
    "anchors": {"d0": "2026-10-07", "d1": "2026-10-06", "d5": "2026-09-30"},
    "auc": {"d0": 0.4531, "d1": 0.4356, "d5": 0.4100},
    "deltas": {"d0": 0.0, "d1": -0.0175, "d5": -0.0431},
    "folds": {"d0": [0.4595, 0.4467]}, "max_abs_delta": 0.0431, "threshold": 0.02,
    "verdict": "잡음바닥 >= 문턱 — 앵커 이동만으로 사전문턱 도달(일 단위 판정 불가)",
}
v5, d5, delta5 = me.judge_by_metric({"metric": "protocol_noise_curve", "id": "CG144"}, parsed_curve)
check("curve: verdict 노출", v5.startswith("잡음바닥 >= 문턱"))
check("curve: delta=None", delta5 is None)
check("curve: detail 에 Δ·최대|Δ|", "-0.0175" in d5 and "0.0431" in d5)

v6, _, _ = me.judge_by_metric({"metric": "protocol_noise_curve", "id": "CG144b"},
                              {"error": "런 실패(앵커 {'d0': '2026-10-07'})"})
check("curve: 파서 오류 → 판정불가", v6 == "판정불가")

# ── C) 파서 전 구간(합성 JSON, 저장소 밖 /tmp) ──────────────────────────────
with tempfile.TemporaryDirectory() as td:
    p = os.path.join(td, "curve.json")
    json.dump({"metric": "protocol_noise_curve", "base_anchor": "2026-10-07",
               "anchors": {"d0": "2026-10-07", "d1": "2026-10-06"},
               "config": "folds=3 stocks=60", "auc": {"d0": 0.45, "d1": 0.44},
               "deltas": {"d0": 0.0, "d1": -0.01}, "folds": {"d0": [0.45]},
               "max_abs_delta": 0.01, "pre_registered_threshold": 0.02,
               "verdict": "잡음바닥 < 문턱 — 전 앵커 |Δ| < 0.02", "errors": {}}, open(p, "w"))
    pc = me.parse_protocol_noise_curve(p, 0.0)
    check("curve 파서: 정상 파싱", not pc.get("error") and pc["threshold"] == 0.02)
    vc, dc, _ = me.judge_by_metric({"metric": "protocol_noise_curve", "id": "CG144"}, pc)
    check("curve 파서→판정: 문턱 미달 노출", vc.startswith("잡음바닥 < 문턱"))
    # 런 실패 스키마 → 판정불가
    json.dump({"metric": "protocol_noise_curve", "anchors": {"d0": "2026-10-07"},
               "auc": {"d0": None}, "errors": {"d0": "TimeoutError"}}, open(p, "w"))
    pf = me.parse_protocol_noise_curve(p, 0.0)
    check("curve 파서: 런 실패 → error", bool(pf.get("error")))
    # mtime floor → 낡은 요약 거부
    stale = me.parse_protocol_noise_curve(p, os.path.getmtime(p) + 10)
    check("curve 파서: 낡은 요약 거부", "미갱신" in str(stale.get("error")))

# ── D) 실제 요약 JSON 이 있으면 전 구간 ────────────────────────────────────
real = next((p for p in ("services/xgboost-ml/reports/overnight/cg143_noise_floor.json",)
             if os.path.exists(p)), None)
if real:
    pr = me.parse_protocol_noise_floor(real, 0.0)
    vr, dr, deltar = me.judge_by_metric({"metric": "protocol_noise_floor", "id": "CG143"}, pr)
    check("실측 floor JSON → 판정 노출", not pr.get("error") and vr != "판정불가" and deltar is None)
    print(f"  실측 판정: {vr} | {dr}")
else:
    print("SKIP 실측 floor JSON 없음(호스트 경로)")

# ── E) summary_path 배선 ───────────────────────────────────────────────────
sp = me.summary_path("protocol_noise_curve", "bash scripts/cg144_noise_curve.sh")
check("summary_path(curve) 배선", sp.endswith("cg144_noise_curve.json"))

print(f"\n결과: FAIL={len(FAILS)}" + (f" {FAILS}" if FAILS else " (전부 PASS)"))
sys.exit(1 if FAILS else 0)

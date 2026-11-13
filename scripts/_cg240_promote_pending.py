#!/usr/bin/env python3
"""CG240 를 needs_setup → pending 으로 승격(구동기가 집어 실행할 수 있게)."""
import json
import sys

P = "docs/QUANT_MODEL_BACKLOG.json"
CMD = ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 1800 "
       "python -u scripts/wf_anchor_sweep.py --panel /app/app/models/wf/panel_200liq_fresh.npz "
       "--arms CO_smooth_d1_h5,CG240_ex_h5 --asofs 2026-11-02,2026-10-27,2026-10-20 "
       "--folds 5 --seeds 5 --tag cg240 "
       "--summary-out /app/reports/overnight/anchor_sweep/cg240_excess_summary.json'")

doc = json.load(open(P, encoding="utf-8"))
items = doc["items"] if isinstance(doc, dict) else doc
hit = [i for i in items if i.get("id") == "CG240"]
if not hit:
    sys.exit("CG240 없음")
it = hit[0]

it["status"] = "pending"
it["priority"] = 1
it["arm"] = "CG240_ex_h5"
it["baseline"] = "CO_smooth_d1_h5 (같은 패널·앵커·시드, 절대수익 smooth 분위 라벨)"
it["command"] = CMD
it["summary_out"] = "/app/reports/overnight/anchor_sweep/cg240_excess_summary.json"
it["counterfactual"] = ("CO_smooth_d1_h5 — 같은 패널·같은 폴드·같은 시드·같은 HP(depth1·lr0.05), "
                        "라벨만 절대수익 smooth 분위(q0.30)")
it["success"] = ("앵커평균 짝 Δ ≥ +0.02 & 전 앵커 부호 양(+) → 라벨 축 신호. "
                 "|Δ| < 앵커 std → '데이터·조정 축이 아니라 라벨 정의도 레버가 아니다'로 축 종결. "
                 "rc≠0·요약 미갱신 → 실행실패(노이즈로 쓰지 않는다).")
it["est_minutes"] = 8
it["cost"] = "낮음(2 arm × 3 앵커 × 5폴드 × 5시드 = 150 cell, 실측 유사 규모 5~8분)"
it["setup_needed"] = (
    "**해소(2026-11-13 17:1x, 엔지니어 자율)** — 구현·자기검정 16/16 PASS: "
    "`scripts/wf_wave.py::make_labels` 에 excess/excess_smooth/excess_thresh/excess_smooth_thresh 4종 추가"
    "(기본 경로 무변경: quantile·smooth·relative·voladj·abs_thresh 실패널 3종 + 합성 5종 비트 동일 확인) + "
    "`scripts/wf_label_sweep.py` CG240_ex_h5 config. "
    "**설계 정정(실측)**: 'demean + 분위' 는 no-op 이다 — 날짜별 상수 이동은 날짜 내 순서를 보존하므로 "
    "excess_smooth 라벨이 smooth 라벨과 **비트 동일**(합성 140행·실패널 32,166행 동일). 그래서 arm 은 "
    "분위 대신 '당일 풀평균 초과(>=0)' 절대문턱을 쓴다 — 같은 스무딩 위에서 라벨 규칙 하나만 바꾼다. "
    "실측: 양성률 0.4168 · 유효 53,497/54,497행(대조 smooth 32,166행) · 날짜별 양성률 std 0.0553"
    "(min 0.2359~max 0.5650 = 국면 의존)."
)
it.setdefault("attempts", [])
json.dump(doc, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("CG240 → pending 승격 완료 · est_minutes", it["est_minutes"], "· arm", it["arm"])

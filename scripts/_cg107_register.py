#!/usr/bin/env python3
"""CG107 등록 + CG105 로 종결된 재무 as-of 핸드오프(XR1·F2) 기각 기록.

2026-10-05 실측 근거(CG105, rc=0, 7.2분, 같은 런 5폴드 짝):
  재무 16컬럼을 rcept-aware 격자(R12)로 교체(셀 변경 33~95%)해도 게이트 ON AUC 가
  대조군과 소수점까지 동일(전 폴드 Δ 0.0000) — 선별 top30 진입 0개 = 학습행렬 비트 동일.
DB 실측(같은 날): rcept_dt 커버리지 96.9% · 접수일−기간말 연간 p50 77일(p95 82, 가정 90 → 13일 보수적)
  · 가정 초과 44쌍(0.92%) · 반기 p50 45 = 가정 45. → as-of 가정은 거의 정확, 정확화의 ML 임팩트 0.

⚠ 리스트를 리바인딩하지 말고 in-place mutate 한다(등록 누락 사고 방지) — 그리고 저장 후 재확인.
"""
import json
import os
import sys

P = "/home/jhshi/analyist_dd/docs/QUANT_MODEL_BACKLOG.json"
d = json.load(open(P, encoding="utf-8"))
items = d["items"]

EVID = ("CG105(2026-10-05 03:14, rc=0, 7.2분): rcept-aware 격자(R12)로 재무 16컬럼 교체"
        "(셀 변경 33~95%) → 게이트 ON 5폴드 AUC 가 대조군과 소수점까지 동일"
        "(전 폴드 Δ 0.0000, arm=control) = 선별 top30 진입 0개(학습행렬 비트 동일). "
        "DB 실측: financial_ratio_features.rcept_dt 커버리지 96.9% · 접수일−기간말 연간 p50 77일"
        "(p95 82, 가정 90일 → 13일 보수적) · 가정 초과 44쌍(0.92%) · 반기 p50 45일 = 가정 45일 "
        "(초과 1.19%). → as-of 지연 가정은 실측상 거의 정확하고, 그 정확화의 ML 임팩트는 0.")

closed_ids = ["XR1", "F2"]
touched = []
for it in items:
    if it.get("id") in closed_ids:
        it["status"] = "closed_rejected"
        it["closed_at"] = "2026-10-05T04:0x+09:00"
        it["closed_by"] = "quant-model-engineer (CG105 실측)"
        it["evidence"] = EVID
        it["closed_reason"] = (
            "재무 as-of 정확화 축은 CG105 로 ML 임팩트 0 이 실측됐다 — 격자 교체가 선별 top30 에 "
            "들어가지 않으면 AUC 는 움직이지 않는다(CG67/EV1 과 같은 기제). 재개 조건: 재무 컬럼이 "
            "게이트 top30 에 진입하는 새 피처 정의가 생길 때만.")
        touched.append(it["id"])
    if it.get("id") == "XR12":
        it["evidence"] = EVID + (" 또한 격자의 roe/per_current/pbr_current 는 같은 패널의 "
                                 "quality_roe/value_per/value_pbr 와 100% 동일(중복열)이다.")
        touched.append("XR12")

# ── CG107: 게이트 ON 라벨 정의를 돈 지표(절대 %p)에 정합시키는 축 ───────────────
NEW = {
    "id": "CG107",
    "title": "라벨-돈 정합: 절대수익 임계 라벨(≥ +2%) vs 분위(q0.30) — 교육 목적함수를 돈 단위에 맞춘다 (기록상 0회)",
    "status": "needs_setup",
    "priority": 4,
    "affects_model": True,
    "arm": "AT_00_30..AT_120_150 (게이트 ON + kind=abs_thresh(thresh=+0.02, h5) + 5서로소 구간)",
    "baseline": {"value": None,
                 "source": "US_00_30..US_120_150 (같은 런·같은 구간·게이트 ON + 분위0.30 h5, 라벨 정의만 다름)"},
    "hypothesis": (
        "현재 모든 라벨은 **상대(relative)** 다(분위 q / 시장상대 중앙값) — 그런데 돈 지표는 **절대**(%p/세션)다. "
        "분위 라벨은 하락일에도 '그날 상위 30%' 를 양성으로 강제해, '아무것도 안 오른 날'을 학습 신호로 넣는다. "
        "절대 임계 라벨(ret >= +2%)은 상승일엔 양성이 늘고 하락일엔 줄어 **시장 국면에 적응**하며, "
        "돈 지표와 같은 단위(절대 수익)를 최적화한다. 'AUC 는 올랐는데 돈은 평평'(CG96~CG103)의 한 원인이 "
        "이 단위 불일치인가."),
    "evidence": (
        "① CG96~CG103 실측: 모델 top-k 가 세션 풀 평균을 넘지 못한다(k=3..30 Δ≤+0.23, t<1.1). "
        "② 라벨 kind 축에서 rel(시장상대 중앙값) vs abs(방향)는 측정됐지만(CG36/CG37: 0.4410 vs 0.4719), "
        "**절대 임계(threshold)** 라벨은 기록상 0회다(모든 config 의 kind 는 quantile/smooth/voladj/relative). "
        "③ 분위 라벨은 중간 분위를 버려 행 도메인이 좁아지지만(양성 30%/일 고정), 절대 임계는 전 행을 "
        "남기고 양성률이 날짜에 따라 변한다 — 과제 정의가 달라 직접 AUC 비교가 아니라 **같은 런·같은 구간 짝 Δ** 로만 판정한다."),
    "method": (
        "① wf_wave.make_labels 에 kind='abs_thresh' 분기 신설: ret = 선행 h일 수익률(shift(-h)), "
        "y = 1.0 if ret >= thresh else 0.0, 결측 유지(중간 분위 버리지 않음). 기본 인자 thresh=None 이면 "
        "기존 경로 비트 동일(프로덕션 무변경). 미지원 kind 는 **즉시 RuntimeError**(조용한 폴백 금지 — EV1 사고). "
        "② config AT_* 5개 등록(게이트 ON·h5·codes_slice, US_* 와 라벨 정의만 다름). "
        "③ 자체점검 `scripts/_abs_thresh_label_test.py`: 경계값·결측·원소 단위 대조(합성 df) + "
        "기본 경로(quantile) 회귀. ④ 시점정합: 선행수익만 사용(누수 없음)."),
    "command": ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 24000 "
                "python -u scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_prod200.npz "
                "--folds 5 --seeds 5 --only AT_00_30,AT_30_60,AT_60_90,AT_90_120,AT_120_150,"
                "US_00_30,US_30_60,US_60_90,US_90_120,US_120_150'"),
    "check": "docker exec -e PYTHONPATH=/app stock_xgboost_ml python /app/scripts/_abs_thresh_label_test.py",
    "success": ("구간 짝 Δ 평균 >= +0.02 이고 양(+) 구간 >= 4/5 → 라벨-돈 단위 정합이 신호"
                "(→ 후속: 생산 경로 retrain_champion 라벨 옵션 반영 검토). 그 밖은 '노이즈'로 축 종결."),
    "counterfactual": "US_00_30..US_120_150 (풀링 edge top30 · 게이트 ON · 분위0.30 h5, 같은 런·같은 구간)",
    "setup_needed": (
        "코드 구현 필요(이 역할 소유): ① wf_wave.make_labels kind='abs_thresh' 분기 + 미지원 kind RuntimeError "
        "② wf_label_sweep config AT_* 5개 ③ 자체점검 _abs_thresh_label_test.py. "
        "⚠ 착수 전에 panel_prod200.npz 존재·청정성(panel_leak_gate CLEAN)을 확인하고 추정 est_minutes/컨테이너 "
        "timeout 을 직전 런 실측 cell 속도로 채운다(CG105 7.2분 = 참조)."),
    "est_minutes": 40,
    "pairs": [["AT_00_30", "US_00_30"], ["AT_30_60", "US_30_60"], ["AT_60_90", "US_60_90"],
              ["AT_90_120", "US_90_120"], ["AT_120_150", "US_120_150"]],
}
if not any(i.get("id") == "CG107" for i in items):
    items.append(NEW)
    touched.append("CG107")

d["items"] = items          # ★ 리바인딩 방지: 되꽂기
d["updated_at"] = "2026-10-05T04:0x+09:00"
json.dump(d, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

# 저장 후 재확인(조용한 등록 실패 방지)
chk = json.load(open(P, encoding="utf-8"))
ids = {i["id"]: i for i in chk["items"]}
assert "CG107" in ids, "CG107 등록 실패"
assert any(i["id"] == "XR1" for i in chk["items"])
print("touched:", touched)
for k in ("XR1", "F2", "XR12", "CG107"):
    print(f"  {k}: status={ids[k].get('status')}")
print("total items:", len(chk["items"]))

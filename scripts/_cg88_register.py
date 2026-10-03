#!/usr/bin/env python3
"""CG88 등록(pending) + CG73 노트에 청정 패널 스크린 실측 반영.

배경(2026-10-04 00:2x 실측):
 - CG87: 청정 패널(panel_prod200·200종목) 등록 프로토콜 5폴드×5시드 —
   LS_quant_q30_h5(게이트 OFF) 0.5302±0.0168 · CO_core30_h5(게이트 ON 대조군) 0.5305±0.0199 ·
   CO_smooth_d1_h5(배포가능 arm) 0.5355±0.0143. 배포가능 arm Δ+0.0050 = 노이즈.
   기록 기준선 0.5406 대비 청정 참조치 0.5305 = −0.0101 (< 0.02 → 기준선 재등록 불요).
 - panel_feature_screen(prod200): 누수 0 · 종목상수 34 · 시장레벨 56 · 무정보 120/213.
   시간가변(비종목상수·비시장레벨) 149개 중 |AUC−0.5| ≥ 0.044(선별 문턱) = 8개, 그중 7개가 SNS.
   SNS 계열 실측 fill = 1.84%(1,010/54,800) 이고 **구간이 2026-05-27~2026-09-23** 뿐이다(결측은 NaN).
   문턱을 넘는 전구간-커버 시간가변 피처는 bayes_volatility 하나(0.0453, fill 1.0, ic_t 3.01)뿐.
"""
import json

PATH = "/home/jhshi/analyist_dd/docs/QUANT_MODEL_BACKLOG.json"
d = json.load(open(PATH, encoding="utf-8"))
assert not any(i.get("id") == "CG88" for i in d["items"]), "CG88 already exists"

item = {
    "id": "CG88",
    "priority": 2,
    "affects_model": True,
    "title": "청정 패널(prod200)에서 CG28 강제투입 3-arm 재측정 — 누수 제거 후 '기존 피처 재배치=노이즈' 결론이 유지되는가",
    "status": "pending",
    "metric": "wf_sweep_summary",
    "arm": "CO_core30_g11_h5",
    "counterfactual": "CO_core30_h5",
    "hypothesis": (
        "CG28(2026-09-29, Δ+0.0005)과 CG29(이벤트 강제투입, Δ+0.0005)는 CG85 가 LEAKY 로 판정한 "
        "panel_420_asofpatch 계열에서 나왔다. 누수 컬럼(value_per/pbr/quality_*)이 top30 선별 슬롯을 먹던 "
        "동안의 '강제투입 무효' 결론이 청정 패널에서도 유지되는지 미측정이다. 청정 패널에서는 선별 구성이 "
        "시간가변 피처로 바뀌므로(스크린: 무정보 120/213, 문턱 통과 시간가변 8개) 강제투입의 여지가 다를 수 있다. "
        "동시에 이 런은 CG28 의 placebo 용량통제(pv10)를 포함해 '같은 수·다른 정보량' 교란을 그대로 통제한다."
    ),
    "command": (
        "docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 7200 python -u "
        "scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_prod200.npz --folds 5 --seeds 5 "
        "--only CO_core30_h5,CO_core30_g11_h5,CO_core30_pv10_h5 "
        "--out /app/reports/overnight/cg88_sweep.jsonl --summary-out /app/reports/overnight/cg88_summary.json'"
    ),
    "success": (
        "(g11 − core30) ≥ +0.02 **이고** (g11 − pv10) ≥ +0.02, 폴드 짝 부호 ≥4/5 → '청정 패널에서 강제투입 신호' "
        "(→ 데이터 축 후보 유지·승격 검토). 미달이면 '기존 피처 강제투입은 누수 제거 후에도 노이즈'로 축을 확정 종결한다."
    ),
    "expected": (
        "노이즈 쪽 무게. g11 의 11개는 전부 전구간 커버(fill 1.0)이고 그중 최강은 bayes_volatility"
        "(|AUC−0.5| 0.0453 = 선별 문턱 바로 위, ic_t 3.01) — 청정 패널의 top30 구성이 이미 그 계열을 "
        "일부 포함하고 있어 잔여 증분이 작을 개연성이 높다."
    ),
    "cost": "3 config × 5폴드 × 5시드 = 75 cell · CG87 실측 8.5분/75 cell → 약 9분",
    "est_minutes": 20,
    "note": (
        "2026-10-04 00:3x 신설. 계기: ① CG87(청정 패널 등록 프로토콜) 완료 — 배포가능 arm Δ+0.0050 노이즈, "
        "청정 참조치 0.5305(기록 기준선 대비 −0.0101, 재등록 불요) ② 같은 패널 피처 스크린 실측으로 '문턱을 넘는 "
        "시간가변 피처가 사실상 없다'(전구간 커버는 bayes_volatility 0.0453 하나, 나머지 7개는 SNS·fill 1.84%·"
        "2026-05-27~ 4개월) ③ CG28·CG29 의 결론이 누수 패널 산이므로 청정 재측정이 필요. "
        "⚠ gate_add 11/10 컬럼이 panel_prod200 에 전부 존재함을 확인(scripts/_cg88_gateadd_check.py). "
        "⚠ 코드 변경 0 — 기존 config 재사용이라 회귀 위험 없음."
    ),
}

d["items"].append(item)

# CG73 노트에 청정 패널 스크린 실측 반영(사전조건 정량화)
for it in d["items"]:
    if it.get("id") == "CG73":
        it["note"] = (it.get("note") or "") + (
            " | 2026-10-04 00:2x 실측(청정 패널 panel_prod200·213피처·누수 0): 종목상수 34 · 시장레벨 56 · "
            "무정보 120/213 · 시간가변(비종목상수·비시장레벨) 149개 중 |AUC−0.5| ≥ 0.044(선별 문턱) = **8개**, "
            "그중 **7개가 SNS**(kalman_attention/sns_attention_score 0.6071 · sns_author_quality_score 0.5889 "
            "ic_t 6.17 · sns_author_quality_score_max_corr 0.5769 …). 그런데 SNS 계열 실측 fill = **1.84%**"
            "(1,010/54,800) 이고 구간이 **2026-05-27~2026-09-23(약 4개월)** 뿐이다(결측은 NaN — `col != 0` "
            "으로 세면 NaN 이 True 로 잡혀 'fill 100%' 로 오독한다). 문턱을 넘는 **전구간-커버** 시간가변 피처는 "
            "bayes_volatility 하나(|AUC−0.5| 0.0453, fill 1.0, ic_t 3.01)뿐이다 → CG73 의 성공 기준"
            "('문턱 이상 5종 이상, 1종 이상 시간가변')은 **현 피처풀 안에서 미달**이고, 병목은 SNS **이력**이다"
            "(수집 범위만 넓혀도 폴드 1~3 은 NaN 이라 측정 불가). 실측 도구: scripts/panel_feature_screen.py "
            "--panel /app/app/models/wf/panel_prod200.npz → services/xgboost-ml/reports/panel_screen_prod200.json."
        )

d["updated_at"] = __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds")
with open(PATH, "w", encoding="utf-8") as f:
    json.dump(d, f, ensure_ascii=False, indent=2)

d2 = json.load(open(PATH, encoding="utf-8"))
assert any(i.get("id") == "CG88" for i in d2["items"]), "CG88 not persisted"
print("inserted CG88; total items", len(d2["items"]))

#!/usr/bin/env python3
"""CG126/CG127 등록 — 시장 국면 조건화 스크린 + 저변동 국면 우위 재현(사전등록).

2026-10-06 장외 자율 세션. 규율: ① id 는 max+1 · 추가 전 any() 확인 ② in-place mutate
③ indent=2 ④ 쓴 뒤 다시 읽어 assert.
"""
import json
import os

P = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "docs", "QUANT_MODEL_BACKLOG.json")
d = json.load(open(P, encoding="utf-8"))
items = d["items"]
existing = {i.get("id") for i in items}
mx = max(int(i["id"][2:]) for i in items if str(i.get("id", "")).startswith("CG")
         and i["id"][2:].isdigit())
cg126, cg127 = "CG%d" % (mx + 1), "CG%d" % (mx + 2)
assert cg126 not in existing and cg127 not in existing, (cg126, cg127, "id 충돌")

CG126 = {
    "id": cg126,
    "title": "시장 국면 조건화 스크린 — 국면(추세·변동성)별 모델 랭크 IC 분해 (XR11 재개 조건의 실행화)",
    "status": "pending",
    "priority": 2,
    "affects_model": False,
    "metric": "regime_ic_screen",
    "regime_test": "trend_up_vs_down",
    "arm": "cg108 AT_00_30 덤프(225세션·체결성 필터) 시장 국면별 IC 분해",
    "counterfactual": "국면 무구분(all) IC — 같은 덤프·같은 행에서 분해만 하는 자기대조(ΔIC up−down)",
    "hypothesis": ("모델측 AUC 축(변환·HP·앙상블 가중·선별규칙·표본가중·목적함수·라벨·유니버스·창·정규화)과 "
                   "돈 축(top-k vs 풀평균·팩터 랭킹·하위꼬리)이 전부 실측으로 닫혔다. 남아 있던 유일한 "
                   "'재개 조건'은 XR11 노트의 국면 조건화다. ⚠ 종목피처 × 날짜상수 국면의 곱은 트리·IC "
                   "모두에서 no-op(per-day 단조변환)이므로, 검정 대상은 곱 피처가 아니라 **국면별로 랭킹 "
                   "효력(IC)이 다른가**다 — 참이면 소비측 국면 게이트가 레버가 되고, 거짓이면 축이 닫힌다."),
    "evidence": ("도구 신설 scripts/regime_ic_screen.py(런타임 몇 초, DB 필요 없음은 아님 — market_data 로 "
                 "동일가중 지수수익률 산출). 실측 2026-10-06 02:0x · cg108_at_preds(체결성필터 후 2,787행·"
                 "211세션): all IC 0.0171(t 0.77) · trend up IC −0.0175(t −0.60, n 115) · trend down "
                 "IC +0.0586(t 1.72, n 96) → **ΔIC(up−down) −0.0761 · t −1.70** = 사전문턱(|t|≥2) 미달."),
    "command": ("docker exec -e PYTHONPATH=/app stock_xgboost_ml python /app/scripts/regime_ic_screen.py "
                "--arm-jsonl /app/reports/overnight/cg108_at_preds.jsonl --arm-tag AT_00_30 --fillable "
                "--json-out /app/reports/overnight/cg126_regime.json"),
    "success": ("사전등록: |ΔIC(up−down)| ≥ 0.03 AND |t| ≥ 2.0 AND 양 버킷 세션 ≥ 15 → '신호있음' = "
                "국면 의존 IC(소비측 국면 게이트 후보). 미달이면 '국면 조건화도 정보 없음' = 노이즈로 축 종결."),
    "expected": "0 근처. 근거: 이 스택의 모든 국면/시장레벨 축이 실측으로 정보 0(XR11·MK1).",
    "cost": "수 초(시장 지수 계산 + IC 분해, 학습 없음)",
    "est_minutes": 2,
    "risk": ("다중비교: 추세 2분면 + 변동성 3분면 = 5개 버킷을 본다 → 사전등록은 추세(2분면)만 채점하고 "
             "변동성 결과는 **탐색적**으로만 기록한다(결과를 본 뒤 문턱을 낮추지 않는다)."),
    "note": ("2026-10-06 02:0x 신설(장외 자율). metric regime_ic_screen 을 같은 커밋에서 "
             "summary_path·parse·judge 3곳에 배선(판정 분기는 항목 플래그 regime_test — 없으면 판정불가). "
             "실행은 이미 완료(탐색적 1차) → --ingest 로 편입. 부수 관찰(탐색적·비사전등록): 변동성 "
             "하위1/3 IC 0.0972(t 3.16) vs 중 −0.0437·상 −0.0018 로 저변동 국면 우위처럼 보임 → "
             f"{cg127} 에서 서로소 2표본으로 사전등록 재현한다."),
    "attempts": [],
    "result": None,
    "finding": None,
    "setup_needed": None,
}

CG127 = {
    "id": cg127,
    "title": "저변동 국면 IC 우위의 재현(사전등록·서로소 2표본) — 관찰-후-재현 규율 적용",
    "status": "pending",
    "priority": 2,
    "affects_model": False,
    "metric": "regime_ic_screen",
    "regime_test": "vol_low_vs_rest",
    "min_delta_ic": 0.03,
    "min_t": 2.0,
    "min_sessions": 15,
    "arm": "cg120_q05_all(98세션·seed7·체결성필터) + cg100_q05_all(80세션·seed0) 저변동 국면 IC",
    "counterfactual": "같은 덤프의 중·고변동 국면 IC(ΔIC low−rest) — 자기대조(같은 행·분해만)",
    "hypothesis": ("CG126 탐색 관찰: cg108 에서 변동성 하위1/3 국면 IC 0.0972(t 3.16, n 70) 가 중(−0.0437)·"
                   "상(−0.0018) 을 크게 상회 → '모델 랭킹은 저변동 국면에서만 유효하다'면 소비측 국면 게이트가 "
                   "레버다. 단 이는 **결과를 본 뒤의 관찰**이므로 승격 근거가 아니다 → 문턱을 지금 고정하고 "
                   "서로소 표본에서 재현해야 한다(CG123→CG124, CG117→CG121 과 같은 규율)."),
    "evidence": ("CG126 실측(2026-10-06, 탐색적): cg108 vol low IC 0.0972(t 3.16) · mid −0.0437 · high −0.0018 "
                 "→ ΔIC(low−rest) +0.117(강해 보임). 이 항목이 재현 검정이다."),
    "command": ("docker exec -e PYTHONPATH=/app stock_xgboost_ml python /app/scripts/regime_ic_screen.py "
                "--arm-jsonl /app/reports/overnight/cg120_q05_all.jsonl --arm-tag cg120q05 --fillable "
                "--json-out /app/reports/overnight/cg127_a.json"),
    "success": ("사전등록(단측): ΔIC(vol low−rest) ≥ +0.03 AND t ≥ 2.0 AND n_low ≥ 15 AND n_rest ≥ 30 → "
                "'신호있음'(저변동 국면 우위 재현) → 후속으로 소비측 국면 게이트 실험 등록. 미달이면 "
                "'국면 조건화 축 종결'(방향 무관 이 결과로 닫는다)."),
    "expected": "미재현(0 근처 또는 부호 반전). 근거: 이 스택의 단일 유니버스 강한 t(CG123 t−5.5 포함)가 서로소에서 전부 부호 반전.",
    "cost": "수 초 × 2표본",
    "est_minutes": 2,
    "risk": "국면 버킷이 전체 세션의 1/3 이라 n 이 작다(98세션 → 32) — t 가 커지려면 효과크기가 커야 한다.",
    "note": ("2026-10-06 02:0x 신설. 사전등록은 실행 **전에** 고정했다(단측 Δ≥+0.03·t≥2·n≥15). "
             "2표본 = ①cg120_q05_all(98세션·2025-06-30~2026-08-26·exp cg120q05) ②cg100_q05_all(80세션·"
             "exp cg92_q05). 구동기는 첫 --json-out 만 판정하므로 ①을 정본으로 하고 ② 결과는 result.note 에 적는다."),
    "attempts": [],
    "result": None,
    "finding": None,
    "setup_needed": None,
}

items.append(CG126)
items.append(CG127)
with open(P, "w", encoding="utf-8") as f:
    json.dump(d, f, ensure_ascii=False, indent=2)

# 쓴 뒤 재확인
d2 = json.load(open(P, encoding="utf-8"))
ids = [i.get("id") for i in d2["items"]]
assert cg126 in ids and cg127 in ids, "등록 실패"
print("registered %s, %s ; total items %d" % (cg126, cg127, len(ids)))

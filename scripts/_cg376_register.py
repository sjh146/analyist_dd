#!/usr/bin/env python3
"""CG376 등록 — event_rolling_features 의 **집계 8종**을 패널에 배선하는 준비 항목.

왜 새 항목인가(중복 방지 근거):
  - 패널(panel_420_asofpatch 210피처 · panel_prod200 213피처)에는 이미 event_* 18종이 있고
    EV1 이 그 축을 닫았다(EV_all_h5 0.5414 vs EV_none_h5 0.5414 → Δ+0.0000, 게이트 ON top30 미진입).
  - 그 18종은 **유형별 5일 카운트**(event_mna_5d 등)로 대부분 0 이다.
  - 새 테이블 event_rolling_features 는 **그룹 집계 20일 레벨·추세**를 추가로 갖는다:
    event_level_20d(nnz 75.0%) · event_any_60d(nnz 99.3%) · event_trend_5v20(56.1%) ·
    disclosure_level_20d(75.5%) · governance(60.6%) · business(49.0%) · funding(32.1%) · risk(6.0%).
    즉 '희소 유형 카운트'가 아니라 '정보가 실제로 채워진 집계'다 — EV1 과 다른 입력이다.
"""
import json

P = "/home/jhshi/analyist_dd/docs/QUANT_MODEL_BACKLOG.json"
d = json.load(open(P))
assert not any(i["id"] == "CG376" for i in d["items"])

item = {
    "id": "CG376",
    "status": "needs_setup",
    "priority": 1,
    "kind": "데이터축",
    "owner": "엔지니어",
    "affects_model": True,
    "metric": None,
    "title": "[데이터 축·신규] event_rolling_features 그룹 집계 8종을 패널에 배선 — 컬럼은 이미 있으나 집계 레벨·추세가 없다(EV1 과 다른 입력)",
    "hypothesis": "EV1 이 닫은 것은 '유형별 5일 카운트 16종'(대부분 0)이다. 새 테이블의 그룹 집계"
                  "(event_level_20d·event_any_60d·event_trend_5v20·disclosure/funding/governance/business/risk_level_20d)는 "
                  "비영 비율 32~99% 로 정보가 실제로 채워져 있고, 20일 레벨·5v20 추세는 '공시 강도 변화'라는 축을 준다 — "
                  "패널에 같은 이름의 20d 집계가 없으므로 in-run A/B(집계 ON vs OFF)를 돌릴 가치가 있다.",
    "setup_needed": "① 시점정합 검증(선행): event_rolling_features 의 각 행이 '그 날짜까지 공지된 사건'만 반영하는지 확인 — "
                    "rcept_dt/공시일 기준 rolling 인지, 사건일 기준인지 spec(docs/spec_event_rolling.md)과 빌더 코드로 확인하고, "
                    "사건일 > 패널일 셀이 0 임을 SQL 로 센다(rcept-aware 격자, CG105 방식). 위반이면 보수적 지연(1~2거래일) 적용. "
                    "② services/xgboost-ml feature_engine 의 피처 목록에 집계 8종 추가(기본 OFF 플래그, 기존 패널 비트 불변) "
                    "③ wf_wave.build_panel 로 패널 재빌드(--end-date 고정·체크포인트, 패널 1개당 1~2h) "
                    "④ 같은 런 in-run A/B: 집계 ON vs OFF, 5폴드×5시드, 판정은 폴드평균 Δ ≥ +0.02 "
                    "⑤ 돈 지표 병기(체결성 필터 + 왕복 0.21%p) — AUC 만 오르면 승격 금지.",
    "command": None,
    "counterfactual": "EV1(유형별 5일 카운트 16종, panel_420_asofpatch): EV_all_h5 0.5414 vs EV_none_h5 0.5414 → Δ+0.0000 · "
                      "게이트 ON top30 에 0개 진입. CG105(재무 as-of 격자 교체)도 Δ0.0000 — '선별에 안 들어가는 피처'는 "
                      "as-of 를 고쳐도 AUC 레버가 아니다. 따라서 이 항목은 '희소 카운트'가 아니라 '비영 32~99% 집계'로 승부한다.",
    "success": "① 시점정합 검증에서 미래 셀 0(또는 지연 적용 후 0) ② 집계 ON arm 의 폴드평균 Δ ≥ +0.02(5폴드×5시드) "
               "③ 폴드 승률 ≥ 0.8 ④ 짝 Δ 기준(같은 런·같은 시드)과 절대 AUC 기준을 함께 기재 ⑤ Δ < +0.02 면 '노이즈'로 축을 닫고 "
               "이유를 돈 지표와 함께 기록",
    "expected": "조정 축 상한(+0.011)을 넘는 유일한 남은 레버가 데이터 축이다. 집계 20d 는 지금까지 한 번도 모델 입력에 들어간 적이 없다.",
    "rationale": "구동기의 미배선 원천 스윕이 event_rolling_features 를 게이트 통과로 올렸는데, 원천 테이블은 이미 존재하고 "
                 "패널에는 그중 '유형별 5d'만 들어가 있다 — 즉 배선 실험이 아니라 **집계 컬럼 증설**이 필요한 상태다. "
                 "실측 근거: data/reports/event_rolling.json(780,091행·2,710종목·2025-08-01~2026-11-18) 비영 비율.",
    "est_minutes": 180,
    "created_at": "2026-11-20T19:25:00+09:00",
    "needs_approval": False,
    "note": "needs_setup 이유: 패널 재빌드(1~2h) + 배선 코드가 선행이다. 준비 순서 ①이 끝나면(시점정합 SQL 검증, 10분) "
            "그 결과를 note 에 적고 시점정합이 통과하면 pending 승격. 시점정합이 깨지면 '지연 적용'을 먼저 구현한다.",
}

d["items"].append(item)
d["updated_at"] = "2026-11-20T19:25:00+09:00"
json.dump(d, open(P, "w"), ensure_ascii=False, indent=2)
print("CG376 등록 — items:", len(d["items"]))
print([i["id"] for i in d["items"] if i["status"] in ("pending", "needs_setup")][:8])

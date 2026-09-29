#!/usr/bin/env python3
"""백로그에 CG31(pending) 추가 — 배포 챔피언의 다중창 견고 AUC 실측."""
import json

P = "/home/jhshi/analyist_dd/docs/QUANT_MODEL_BACKLOG.json"
d = json.load(open(P))
if any(i.get("id") == "CG31" for i in d["items"]):
    print("CG31 이미 존재")
    raise SystemExit(0)
d["items"].append({
    "id": "CG31",
    "title": "배포 챔피언의 다중창 견고 AUC 실측 — 승격 기준선이 단일분할(0.5513)인 결함을 실측으로 교체",
    "status": "pending",
    "priority": 2,
    "affects_model": True,
    "baseline": {"value": 0.5513,
                 "source": "champion/robust_auc.json — protocol='ensemble_auc on candidate val split (n_rows=12629)' "
                           "= **단일 시간분할** 값(하드룰 #1 이 승격 기준선으로 금지한 형태)"},
    "arm": None,
    "counterfactual": None,
    "hypothesis": "승격 게이트(champion_promote)의 비교 기준선은 champion/robust_auc.json 의 0.5513 인데, 그 값은 "
                  "단일 시간분할(20%) ensemble AUC 다 — 하드룰 #1('단일 분할 AUC 는 승격 기준선으로 쓰지 않는다')을 "
                  "정면으로 어긴다. 배포된 챔피언 자체를 다중 시간창·실제 추론 경로로 평가해 정직한 기준선을 세운다. "
                  "이 값이 없으면 '챌린저 0.5406 이 기준선 미달' 과 '챌린저가 챔피언보다 나쁘다' 를 구분할 수 없다.",
    "evidence": "① champion/robust_auc.json: robust_auc 0.5513 · metric='ensemble_auc' · auc_std=null · "
                "protocol='ensemble_auc on candidate val split (n_rows=12629)' · recorded 2026-09-24. "
                "② app/reports/champion_robust_eval.json **부재** — 이 프로토콜로 챔피언을 평가한 적이 없다(스크립트만 존재). "
                "③ 스크립트(scripts/champion_robust_eval.py)는 창별 날짜별 크로스섹션 AUC 평균±std 와 풀링 AUC 를 함께 낸다. "
                "④ 속도 실측(2026-09-29 16:19): 1폴드×2일×40종목=80건 빌드에 74초 → 약 1.1건/초 → 기본값(5×10×80=4,000건) 약 70분.",
    "method": "배포 챔피언(app/models/champion)을 실제 추론 경로(FeaturePipeline.build_features → 크로스섹션 랭크 → "
              "feature_names 순서 → EnsembleModel.predict)로 5시간창×10일×80종목 평가. 라벨은 h5 시장상대 초과수익(창 끝 purge). "
              "`--write` 는 쓰지 않는다(승격 기준선 파일 champion/robust_auc.json 을 함부로 덮지 않는다 — 기준선 교체는 승인 대상).",
    "command": "docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 4200 python scripts/champion_robust_eval.py "
               "--folds 5 --dates-per-fold 10 --stocks 80 --out /app/reports/champion_robust_eval.json'",
    "check": "docker exec stock_xgboost_ml cat /app/reports/champion_robust_eval.json",
    "check_target": {"op": ">=", "value": 1},
    "success": "5창 창별 AUC 평균±std 와 풀링 AUC 를 얻는다(판정 대상은 '개선'이 아니라 기준선 실측이다). "
               "결과는 승격 기준선 교체안과 함께 사용자 승인 항목으로 올린다.",
    "expected": "미지 — 스윕 기준선(0.5406)과 다른 프로토콜이라 직접 비교 금지. 참고로 챔피언 학습기 자체의 단일분할 값은 0.5513.",
    "cost": "약 70분(4,000건 피처 빌드 · 실측 1.1건/초) · timeout 4200s(19:55 이전 자체 종료 → 20:00 컨테이너 재생성 창 회피)",
    "est_minutes": 75,
    "metric": "champion_robust_eval",
    "caution": "20:00 파이프라인 재생성 전에 끝나야 한다(timeout 4200s 로 강제). --write 금지(승격 기준선 파일 보호). "
               "U3 패널 빌드(런처 20:35 시작)와 겹치지 않는다.",
    "note": "2026-09-29 16:2x 신설 — CG29/CG30 으로 패널 내 축이 모두 닫힌 뒤, 남은 승격 경로의 유일한 공백이 "
            "'챔피언 자체의 정직한 기준선'임을 확인하고 등록. 이 항목은 새 레버가 아니라 **승격 게이트의 기반 정비**다"
            "(하드룰 #1·#4).",
})
d["updated_at"] = "2026-09-29T16:22:00+09:00"
json.dump(d, open(P, "w"), ensure_ascii=False, indent=1)
print("CG31 추가 | pending:", [i["id"] for i in d["items"] if i.get("status") == "pending"])

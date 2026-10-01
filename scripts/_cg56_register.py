#!/usr/bin/env python3
"""CG56 백로그 등록(2026-10-01) — 라벨 다양성 앙상블(챔피언 + cand_cg51) 10시드 짝 실험."""
import json
import os

P = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "docs", "QUANT_MODEL_BACKLOG.json")
b = json.load(open(P, encoding="utf-8"))
items = b["items"] if isinstance(b, dict) and "items" in b else b
assert not any(i.get("id") == "CG56" for i in items), "CG56 already exists"

item = {
 "id": "CG56",
 "title": "라벨 다양성 앙상블 — 절대 h1 챔피언 + 시장상대 h5 후보의 rank-평균 결합 (10시드 짝)",
 "status": "pending",
 "priority": 2,
 "affects_model": True,
 "arm": "blend(rank-avg) = champion(절대 h1 방향) + cand_cg51(시장상대 h5) — 같은 런·같은 시드·같은 창에서 preds 덤프 후 (fold,date) 내 순위평균",
 "counterfactual": "app/models/champion (절대 h1) — 같은 런·같은 시드·같은 창(5폴드×10일·60종목·training 유니버스)",
 "counterfactual_value": 0.5139,
 "baseline": {"value": 0.5139,
              "source": "CG53 챔피언 10시드 평균(app/reports/cg51_champ_s0..9.json, 2026-10-01 08:23) — 같은 창 구성·같은 유니버스 함수·같은 라벨(rel h5)"},
 "hypothesis": "조정 축(피처변환·HP·유니버스·창·가중·정규화·k·q·부활 데이터·학습창 길이)이 전부 사전문턱 +0.02 미달로 닫혔다. 남은 미시험 축은 **서로 다른 라벨로 학습한 두 모델의 결합**이다. 두 모델은 같은 피처풀을 쓰지만 오차가 완전히 겹치지 않으므로(CG53: champ 0.5139±0.0063 vs cand 0.5272±0.0124, 10시드 짝 Δ+0.0133) 순위평균 결합이 분산을 줄여 문턱을 넘을 수 있는가를 묻는다.",
 "evidence": "① CG53 실측(2026-10-01 08:23, 10시드 짝): champ 0.5139 vs cand_cg51 0.5272 → Δ+0.0133 · SE 0.0052 · t 2.54 · 양(+) 8/10 · 부호검정 p=0.055 — 문턱 미달이지만 두 모델의 순위가 동일하지 않다는 증거. ② CG55 실측: 같은 모델·같은 시드는 비트 동일(반복성 0) → 짝 설계의 잡음 바닥은 0 이고, 단일 런 비교(창 구성 교체)만 +0.0261 로 문턱을 넘는다 → 판정은 반드시 같은 런 짝으로. ③ CG30: 시드 앙상블 이득 최대 +0.0057(축 종결) — 이 항목은 시드가 아니라 **라벨 종류가 다른 두 모델**의 결합이므로 다른 축이다.",
 "method": "scripts/cg56_run.sh 가 시드 0..9 각각에서 champion·cand_cg51 을 같은 인자(--train-start 2026-06-25 --train-end 2026-09-30 = 두 모델 학습구간의 합집합, --folds 5 --dates-per-fold 10 --stocks 60 --label-kind rel --horizon 5)로 평가하며 --dump-preds 로 예측을 남기고, scripts/blend_eval.py 가 (fold,date) 내 순위평균 결합 AUC 를 계산해 in-run 짝 Δ(blend−champ, blend−cand)를 낸다. 요약은 champion_robust_eval 스키마로 기록된다.",
 "command": "docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 timeout 5400 sh scripts/cg56_run.sh --out app/reports/cg56_summary.json'",
 "metric": "champion_robust_eval",
 "success": "in-run 짝 Δ(blend − champ, 같은 런 10시드) ≥ +0.02 **그리고** 양(+) 시드 ≥ 8/10 (SE ≈0.005 → 약 3σ). 미달이면 '모델 결합' 축도 닫고, 남은 레버는 원천 확보뿐임을 보고한다.",
 "expected": "미지 — 두 모델 순위 상관이 높으면 이득 없음(그 경우 축 종결)",
 "cost": "20회 평가 × 약 2.4분 ≈ 50~60분 (학습 없음 — 평가만)",
 "est_minutes": 60,
 "note": "① 첫 --out 만 구동기가 판정에 읽으므로 시드별 --out 은 셸 래퍼 안에 뒀다(CG55 재발 방지). ② 판정의 정본은 요약 JSON 의 `paired` 블록(같은 런 챔피언 평균 대비)이고, 구동기의 Δ 는 등록 대조값 0.5139(CG53) 대비다. ③ **배포 제약**: 두 모델 확률을 평균하려면 추론 경로(app/inference/predictor.py = 단일 모델 디렉터리) 변경이 필요하다 → 신호가 나와도 승격 후보가 아니라 **승인 요청**이다. ④ 순위평균은 확률 스케일이 다른 두 모델(절대 h1 vs 시장상대 h5)을 그대로 평균하면 지배당하므로 필수 선택이다.",
 "created_at": "2026-10-01T18:20:00+09:00",
}
if isinstance(b, dict) and "items" in b:
    b["items"].append(item)
else:
    b.append(item)
json.dump(b, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("registered CG56; total items =", len(b["items"] if isinstance(b, dict) else b))

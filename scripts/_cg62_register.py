#!/usr/bin/env python3
"""CG62 백로그 등록 (2026-10-02 야간 자율 세션)."""
import json

P = '/home/jhshi/analyist_dd/docs/QUANT_MODEL_BACKLOG.json'
b = json.load(open(P, encoding='utf-8'))
items = b['items']
assert not any(i.get('id') == 'CG62' for i in items), 'CG62 exists'

cmd = ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 timeout 10800 "
       "sh scripts/cg62_run.sh --agg-out app/reports/cg62_summary.json --expect-seeds 10'")

item = {
    "id": "CG62",
    "title": "배포 모델 선택 판정 — 현재 배포 챔피언(10-01 승격분) vs 배포 가능 최선 후보(cand_cg51) 생산 프로토콜 10시드 짝",
    "status": "pending",
    "priority": 1,
    "affects_model": True,
    "arm": "app/models/cand_cg51 (rel h5 시장상대 라벨 재학습 · 생산 유니버스 200종목 · 90일 · 추론 계약 무변경)",
    "counterfactual": "app/models/champion (2026-10-01 21:26 게이트 승격분 · 단일 val split +0.0035) — 같은 창·같은 시드·같은 라벨(rel h5)",
    "hypothesis": ("CG61 이 '10-01 승격은 개선이 아니다'(10시드 짝 Δ-0.0097 · SE 0.0058 · t -1.66 · 양(+) 5/10)를 확정했다. "
                   "그 승격 마진(+0.0035)은 프로토콜 잡음(+0.0261 창 구성 · +0.0211 유니버스 시드)의 1/7 이다. "
                   "그렇다면 지금 배포된 모델과, 우리가 가진 배포 가능 최선 후보(cand_cg51 — CG53 에서 옛 챔피언 대비 Δ+0.0133 · "
                   "t 2.54 · 양(+) 8/10) 중 어느 쪽이 생산 프로토콜에서 더 좋은가? 승격·교체 판단의 정직한 근거는 이것뿐이다."),
    "evidence": ("① CG61 실측(2026-10-02 02:48): prev 0.5045±0.0108 vs new 0.4949±0.0155 · 짝Δ -0.0097(SE 0.0058 · t -1.66) — "
                 "새 챔피언은 옛 챔피언보다 약 0.0096 낮다. ② CG53 실측(2026-10-01): 같은 생산 프로토콜(rel h5·60종목)에서 "
                 "옛 챔피언 0.5139 vs cand_cg51 0.5272 = 짝Δ +0.0133(t 2.54). ③ 두 사실을 합치면 현재 챔피언 대비 cand_cg51 의 "
                 "기대 Δ ≈ +0.023 으로 사전문턱 +0.02 를 명목상 넘을 수 있다 — 10시드 짝이면 SE 0.005 로 4σ 검출. "
                 "④ 배포 게이트의 기준선은 여전히 단일 val split(robust_auc.json: protocol='ensemble_auc on candidate val split')이라 "
                 "'배포된 모델이 최선인가'를 아무도 확인하지 않았다."),
    "method": ("scripts/cg62_run.sh — 두 arm 을 **같은 런**에서 채점한다: champion_robust_eval --train-start 2026-06-25 "
               "--train-end 2026-10-01 --universe training --label-kind rel --horizon 5 --folds 5 --dates-per-fold 10 "
               "--stocks 60 --universe-seed 0..9 (20회 평가) → champion_seed_family_agg --expect-seeds 10 으로 짝 판정. "
               "재학습 없음(두 모델 모두 디스크에 있음)."),
    "command": cmd,
    "metric": "champion_seed_family",
    "success": ("짝 Δ(cand − champ) 평균이 +0.02 이상이고 양(+) 시드 10/10 → '배포 모델 교체' 승인 요청의 정량 근거로 쓴다. "
                "Δ < +0.02 → 배포 가능 축(현 피처풀·추론 계약 무변경 범위) 종결로 기록하고, 남은 레버는 데이터 축·게이트 기준선 개편뿐임을 명시. "
                "|t| < 2 이고 부호 뒤섞임이면 '현 챔피언 vs 후보를 구분할 증거 없음'으로 정직하게 적는다."),
    "expected": "미지 — 옛 챔피언 대비 +0.0133 이고 현재 챔피언이 그보다 0.0096 낮으므로 명목상 +0.023 근처. 문턱 경계라 결과가 판정을 가른다.",
    "cost": "평가 20회(2 arm × 10시드) × 실측 5.1분 ≈ 102분(직렬, 장외) + 집계 수초",
    "est_minutes": 115,
    "note": ("2026-10-02 03:0x 등록(야간 자율 세션, CG61 종결 직후). ⚠ 남는 OOS 창은 학습구간 **이전**(2025-12~2026-05)뿐이다"
             "(DB 마지막 거래일 10-01 → 전방 창 없음) — 보고 시 명시. "
             "⚠ 라벨 정합 비교다: champ 는 abs h1 로 학습돼 rel h5 채점에서 off-task, cand 는 rel h5 자기 과제 — CG51/CG53 과 같은 설계이므로 "
             "절대값이 아니라 **짝 Δ**만 인용한다. "
             "⚠ 승격/교체는 이 루프에서 하지 않는다(champion_promote --dry-run + 사람 승인)."),
}
items.append(item)
json.dump(b, open(P, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print('registered CG62; total', len(items), '| pending:', sum(1 for i in items if i.get('status') == 'pending'))

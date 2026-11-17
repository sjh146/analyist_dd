"""CG321 등록기 — 배포 챔피언 160세션 국면별 재채점(사전등록)."""
import json

P = '/home/jhshi/analyist_dd/docs/QUANT_MODEL_BACKLOG.json'

cmd = (
    "docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 timeout 13800 python -u "
    "scripts/champion_robust_eval.py --model-dir app/models/champion --label-kind rel --horizon 5 "
    "--folds 10 --dates-per-fold 20 --stocks 200 --universe training --universe-seed 0 "
    "--asof-date 2026-11-13 --zero-features rank_ --dump-all "
    "--dump-preds /app/reports/overnight/cg321_champ_preds.jsonl --dump-tag cg321_champ "
    "--out /app/reports/overnight/cg321_champ_robust.json'"
    " && docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 timeout 1800 python -u "
    "scripts/fillable_topk_expectancy.py --arm-jsonl reports/overnight/cg321_champ_preds.jsonl "
    "--arm-tag cg321_champ --control-jsonl reports/overnight/cg297_norank_preds.jsonl "
    "--control-tag cg297_norank --k 3,5 --sides top --regime-split pool_sign --stride 5 "
    "--exit close_h --horizon 5 --json-out reports/overnight/cg321_s5.json'"
    " && docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 timeout 1800 python -u "
    "scripts/fillable_topk_expectancy.py --arm-jsonl reports/overnight/cg321_champ_preds.jsonl "
    "--arm-tag cg321_champ --control-jsonl reports/overnight/cg297_norank_preds.jsonl "
    "--control-tag cg297_norank --k 3,5 --sides top --regime-split pool_sign --stride 1 "
    "--exit close_h --horizon 5 --json-out reports/overnight/cg321_s1.json'"
)

item = {
    "id": "CG321",
    "title": "[배포모델·국면] 서빙 동일 챔피언(rank_* 0.0)을 평가창 전수 160세션으로 재채점하고 국면별 top-k 엣지를 재측정 — CG302(80세션 '국면 무관')와 CG318(뒤 절반 부호반전)의 충돌 해소",
    "status": "pending",
    "priority": 1,
    "owner": "model_engineer",
    "kind": "money",
    "hypothesis": (
        "q0.05 후보(CG314 덤프) 160세션 stride5 재측정에서 top-k 초과수익은 상승국면 +0.326%(t 0.25)/+2.100(t 1.55) · "
        "하락국면 -0.725%(t -0.83)/-0.385(t -0.55) 로 부호가 갈렸다(CG321 stepA · 필터 ON · 32 독립세션). "
        "CG318 도 앞 80세션 top3 +1.808(t 3.45) vs 뒤 80세션 -3.218(t -3.67) 로 '엣지가 국면과 함께 뒤집힘'을 보였다. "
        "그런데 배포 모델로 측정한 CG302 는 80세션에서 두 국면 모두 Δ>0(상승 +1.795 t1.02 · 하락 +2.353 t2.67)이라 정면 충돌한다. "
        "차이는 ① 후보 vs 배포 모델 ② 80 vs 160 세션 ③ stride1 중첩 중 어느 것인지 모른다 → 배포 모델을 같은 160세션으로 재채점해 갈라야 한다."
    ),
    "command": cmd,
    "counterfactual": "CG302(배포 챔피언 cg297_norank · 80세션 stride1 · 상승 Δ+1.795 t1.02 / 하락 Δ+2.353 t2.67 = 국면 무관) · 풀평균(같은 세션 무작위 k 기대)",
    "success": (
        "사전등록(변경 금지) — 판정 대상은 Δ(top-k − 풀평균) 이고 절대 순기대는 별도 표기: "
        "① 상승국면 Δ>0 & t≥1.5 이고 하락국면 Δ<0 이면 '배포 모델은 국면 베타 증폭기' 확정 → 트레이더 소비 규칙(하락국면 비중축소/진입중단) 승인 요청(내 권한 밖). "
        "② 두 국면 모두 Δ>0 이고 차이 <1.0%p 이면 CG302 재현 = 국면 무관으로 기록하고 배포 유지 근거로 쓴다. "
        "③ 하락국면 Δ<0 이지만 t<1.5 면 '부호는 일관·유의성 미달'로 노이즈 표기(중단 근거로 쓰지 않음). "
        "④ stride5(독립)와 stride1 의 부호가 다르면 중첩창 효과로 적고 stride5 를 판정에 쓴다."
    ),
    "est_minutes": 120,
    "metric": "fillable_topk_expectancy",
    "summary_out": "/app/reports/overnight/cg321_s1.json",
    "created_at": "2026-11-17T16:20:00+09:00",
    "updated_at": "2026-11-17T16:20:00+09:00",
    "note": (
        "2026-11-17 16:2x 엔지니어 자율(장외 틱). 오늘 q0.05 라벨 축은 돈으로 종결됐다(CG318 재측정: 160세션 Δpool -0.209 t-0.55 · "
        "앞/뒤 절반 부호반전). 그 종결의 부산물이 이 항목이다 — '엣지가 언제 있었나'가 국면과 겹친다는 신호. "
        "⚠ 성능 레버 재시험이 아니다(변환·게이트·라벨 재시험 금지). 배포 중인 모델을 트레이더가 실제로 어떤 국면에서 쓰고 있는지의 측정이다. "
        "⚠ 절대 AUC 는 CG319/CG320 의 널 바닥(+0.011~0.021) 만큼 과대이므로 이 런의 AUC 도 Δ 로만 인용한다."
    ),
}

d = json.load(open(P))
items = [item if i.get('id') == 'CG321' else i for i in d['items']]
if not any(i.get('id') == 'CG321' for i in d['items']):
    items.append(item)
d['items'] = items
d['updated_at'] = "2026-11-17T16:20:00+09:00"
json.dump(d, open(P, 'w'), ensure_ascii=False, indent=1)
print("items", len(items))
print("CG321", [i['status'] for i in items if i['id'] == 'CG321'])

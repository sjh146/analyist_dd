import json, os
P='docs/QUANT_MODEL_BACKLOG.json'
b=json.load(open(P))
by={it['id']:it for it in b['items']}
cmd=("docker exec -e UNIVERSE_ASOF_DATE=2026-11-20 stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 3300 "
     "python -u scripts/champion_robust_eval.py --folds 5 --dates-per-fold 10 --stocks 80 --universe training "
     "--horizon 5 --dump-preds /app/reports/overnight/cg419_champ.jsonl --dump-all --dump-tag champion "
     "--out /app/reports/overnight/cg419_champ_50d.json && OMP_NUM_THREADS=2 timeout 900 "
     "python -u scripts/fillable_topk_expectancy.py --arm-jsonl /app/reports/overnight/cg419_champ.jsonl "
     "--arm-tag champion --control-jsonl /app/reports/overnight/cg419_champ.jsonl --control-tag champion "
     "--k 3,5,10 --sides top --primary-side top --stride 5 --exit close_h --horizon 5 "
     "--json-out /app/reports/overnight/cg419_money_50d.json'")
it={
 "id":"CG419","kind":"measurement","owner":"engineer","affects_model":False,"priority":1,"status":"pending",
 "metric":"fillable_topk_vs_pool","summary_out":"/app/reports/overnight/cg419_money_50d.json",
 "est_minutes":45,
 "title":"[머니 기준선·수리] CG401(rc=2 실행실패) 후속 — 배포 챔피언 돈 기준선을 50날짜·비중첩(stride 5)으로 다시 잰다",
 "arm":"배포 챔피언(단일 모델) top-k vs 세션 풀평균",
 "counterfactual":"로컬 robust_oos.json(2026-10-05 · 순기대 -0.182%p · 87세션 · age 47.7d) · CG401 실패 덤프(20세션·1,580행)",
 "command":cmd,
 "hypothesis":("승격 게이트가 비교하는 챔피언 돈 기준선은 47.7일 전 값이고, CG401 은 50날짜로 갱신하려다 rc=2 로 죽었다"
               "(fillable 단계에서 --control-jsonl 누락으로 argparse 종료 — 같은 덤프를 arm/control 로 넘기는 경로가 깨졌다). "
               "같은 유니버스에서 50날짜·비중첩 표본으로 다시 재면 ①기준선 신선도 ②'배포 모델이 + 기대를 낸 적이 있는가'가 함께 판정된다."),
 "evidence":("스코어카드 money: created 2026-10-05 · 순기대 -0.182%p(t -0.81) · 87세션/1340거래 · age 47.7d · "
             "CG401 로그 꼬리: 'fillable_topk_expectancy.py: error: the following arguments are required: --control-jsonl' (rc=2) · "
             "CG400/CG415: 후보 arm 은 상대 Δ 양(+)·절대 ≈0 이고 하락 국면 방어 틸트(CG417 재현) → 챔피언도 같은 성질인지 미확인."),
 "success":("rc=0 · 세션수 기록 · 판정: ① 세션 ≥ 40 & 순기대 > 0 & t ≥ 2 면 '배포 모델도 플러스' ② |순기대| < 0.2%p 면 "
            "'60일 창에서도 엣지 없음'으로 기준선을 갱신 ③ 표본 < 40 이면 판정불가로 남기고 원인(덤프 행수)을 적는다. "
            "기준선 파일은 reports/overnight 에만 쓴다(게이트 기준선 교체는 승인 대상)."),
 "expected":"순기대 0 근처(-0.2~0.0%p). 그래도 '게이트 기준선이 47일 낡았다'가 숫자로 갱신된다.",
 "cost":"50날짜×80종목 평가 ≈ 35분 + 집계 2분",
 "created":"2026-11-22T06:35:00+09:00",
}
if it['id'] not in by:
    b['items'].append(it)
tmp=P+'.tmp'
json.dump(b, open(tmp,'w'), ensure_ascii=False, indent=1)
os.replace(tmp,P)
print('added CG419', len(b['items']))

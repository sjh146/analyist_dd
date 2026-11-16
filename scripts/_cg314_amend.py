import json, datetime

p = 'docs/QUANT_MODEL_BACKLOG.json'
b = json.load(open(p))
items = b['items']
by = {i['id']: i for i in items}
now = datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()

c = by['CG314']
new = (
    "docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 timeout 12600 python -u "
    "scripts/champion_robust_eval.py --model-dir app/models/champion_cand_cg306 --label-kind quantile "
    "--label-q 0.05 --folds 10 --dates-per-fold 20 --stocks 200 --universe training --universe-seed 0 "
    "--asof-date 2026-11-13 --dump-all --dump-preds /app/reports/overnight/cg314_preds.jsonl "
    "--out /app/reports/overnight/cg314_robust.json && "
    "OMP_NUM_THREADS=2 timeout 1200 python -u scripts/fillable_topk_expectancy.py "
    "--arm-jsonl reports/overnight/cg314_preds.jsonl --control-jsonl reports/overnight/cg297_norank_preds.jsonl "
    "--control-tag cg297_norank --k 3,5,10 --sides top --regime-split pool_sign --stride 5 "
    "--exit close_h --horizon 5 --json-out reports/overnight/cg314_stride5.json && "
    "OMP_NUM_THREADS=2 timeout 1200 python -u scripts/fillable_topk_expectancy.py "
    "--arm-jsonl reports/overnight/cg314_preds.jsonl --control-jsonl reports/overnight/cg297_norank_preds.jsonl "
    "--control-tag cg297_norank --k 3,5,10 --sides top --regime-split pool_sign --stride 1 "
    "--exit close_h --horizon 5 --json-out reports/overnight/cg314_stride1.json'"
)
c['command'] = new
c['command_amended_at'] = now
c['amendment'] = (
    "2026-11-17 03:2x 결함 수리 2건. (1) 돈 지표 단계 누락: 사전등록 success 가 k=3 Δ(arm-풀평균) t>=2 인데 "
    "명령은 AUC 평가만 했다 -> fillable_topk_expectancy(stride5/stride1) 2회를 체인에 추가. "
    "(2) 후보풀 오염: CG311~313 이 쓴 cg306_preds.jsonl 은 실측 20행/date(=200종목의 10%)·y_true 800/800 "
    "완전균형 = 라벨 꼬리로 사후 선택된 풀(quantile 중간행 미덤프, CG95 함정). 그 풀의 top-k 는 "
    "'그날 크게 움직일 종목을 미리 안다'를 전제하므로 매매 불가 -> CG311 +6.07%p 는 트레이더블 수치가 아니다. "
    "이 항목은 --dump-all 로 전 유니버스 풀을 덤프하므로 같은 결함이 없다. "
    "eval timeout 13800->12600s 로 줄여 expectancy 단계까지 07:50 전 종료(개장 09:00 여유)."
)
c['precondition'] = (
    "덤프 검증: cg314_preds.jsonl 의 date 별 행수가 ~200 이고 y_true null 이 존재해야 한다. "
    "20행/date 면 --dump-all 미적용 -> 결과 무효."
)

defects = {
    'CG311': ("CG311 이 쓴 arm 예측파일(cg306_preds.jsonl)이 라벨 꼬리 사후선택 풀(20행/date·y_true 완전균형)이라 "
              "top-k 순기대 +6.069%p 는 매매 불가 풀 측정치다. 재측정은 CG314(--dump-all) 로 대체."),
    'CG312': ("집중도 분해도 같은 오염 풀 위에서 계산됐다(상위3종목 53% = 꼬리 풀 안에서의 집중). "
              "전 유니버스 풀 재측정은 CG314 결과로 갱신."),
    'CG313': ("stride5·지연1일 재측정도 오염 풀 위. 단 '지연1일에서도 부호 유지'라는 방향성은 풀과 무관한 성질이므로 "
              "부분 유효 — 크기(%p)는 신뢰 금지. 전량 재측정은 CG314."),
}
for cid, why in defects.items():
    by[cid]['defect'] = why
    by[cid]['defect_found_at'] = now

by['CG315']['note'] = (
    by['CG315'].get('note', '')
    + " | " + now[:16] + " 보류 조건 추가: CG313 수치가 라벨 꼬리 풀 오염(CG311 defect) 위 측정이라, "
      "CG314 전 유니버스 풀 재측정에서 k=3 Δ(arm-풀평균) t>=2 가 유지될 때만 착수한다."
)

b['updated_at'] = now
open(p, 'w').write(json.dumps(b, ensure_ascii=False, indent=1))
json.load(open(p))
print('OK amended_at', now, 'cmd_len', len(c['command']))

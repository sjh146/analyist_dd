import json, datetime

P = 'docs/QUANT_MODEL_BACKLOG.json'
d = json.load(open(P))
items = d['items']
ids = {i['id'] for i in items}
now = datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()

assert 'CG313' not in ids, 'CG313 exists'
assert 'CG314' not in ids, 'CG314 exists'
assert 'CG315' not in ids, 'CG315 exists'

cg313 = {
 "id": "CG313",
 "status": "done",
 "priority": 1,
 "kind": "measurement",
 "owner": "quant-model-engineer",
 "affects_model": False,
 "metric": "fillable_topk_expectancy",
 "title": "[중첩창·실행지연] q0.05 top-k 신호가 겹침창 인플레이션인가, 하루 늦게 사도 남는가 — stride5 + 지연1일 플레이스보",
 "hypothesis": "CG311 의 k=3 +6.069%p/세션(t 3.83)은 두 가지를 검증하지 않았다. ① h=5 보유창이 세션마다 겹쳐 t 가 sqrt(5)≈2.2배 부풀려진다(CG290) → stride=5 로 비중첩 20세션을 보면 정직한 t 가 나온다. ② 진입가가 신호일 t 의 종가라 실제 주문(다음날)에서는 못 산다 → 예측일을 한 거래일 미룬 arm 으로 재채점하면 실행 가능한지 판별된다.",
 "evidence": "CG311: k=3 +6.069%p(t 3.83·앞 +10.749/뒤 +1.389) · CG312: 상위3종목 제외 +3.214(t 1.76) · 상위5세션 제외 +4.212(t 2.74) · CG290: 겹침창 t 인플레이션 sqrt(5).",
 "arm": "champion_cand_cg306 top-k (cg306_preds.jsonl / 지연본 cg313_preds_delay1.jsonl)",
 "baseline": "세션 체결가능 풀평균(무작위 k 기대) + 대조군 cg297_norank",
 "counterfactual": "cg297_norank (같은 폴드·같은 날짜·같은 필터)",
 "command": "docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 timeout 300 python -u scripts/_cg313_delay_placebo.py --arm-jsonl reports/overnight/cg306_preds.jsonl --calendar-jsonl reports/overnight/cg297_norank_preds.jsonl --out reports/overnight/cg313_preds_delay1.jsonl && OMP_NUM_THREADS=2 timeout 900 python -u scripts/fillable_topk_expectancy.py --arm-jsonl reports/overnight/cg306_preds.jsonl --control-jsonl reports/overnight/cg297_norank_preds.jsonl --control-tag cg297_norank --k 3,5,10 --sides top --regime-split pool_sign --stride 5 --exit close_h --horizon 5 --json-out reports/overnight/cg313_stride5_closeh.json && OMP_NUM_THREADS=2 timeout 600 python -u scripts/fillable_topk_expectancy.py --arm-jsonl reports/overnight/cg313_preds_delay1.jsonl --control-jsonl reports/overnight/cg297_norank_preds.jsonl --control-tag cg297_norank --k 3,5,10 --sides top --regime-split pool_sign --stride 1 --exit open_h1 --horizon 1 --json-out reports/overnight/cg313_delay1_h1.json && OMP_NUM_THREADS=2 timeout 600 python -u scripts/fillable_topk_expectancy.py --arm-jsonl reports/overnight/cg313_preds_delay1.jsonl --control-jsonl reports/overnight/cg297_norank_preds.jsonl --control-tag cg297_norank --k 3,5,10 --sides top --regime-split pool_sign --stride 1 --exit close_h --horizon 5 --json-out reports/overnight/cg313_delay1_h5.json'",
 "summary_out": "/app/reports/overnight/cg313_stride5_closeh.json",
 "est_minutes": 8,
 "success": "사전등록: ① stride5(독립 20세션) k=3 Δ(arm−풀평균) t ≥ 2 & halves both_positive → 겹침창 인공물 아님 ② 지연1일 open_h1 k=3 Δ(arm−풀평균) t ≥ 1.5 & 순기대 > 0 → 실행 가능 ①·② 모두 충족 = 신호 유지(다음 = 표본 확대 CG314). ①만 충족 = '당일 종가 진입 전용'으로 축 제한. ① 미달 = 겹침창 인공물 → 라벨 축 종결.",
 "result": "① stride5: k=3 arm +6.582%p/세션(t 2.13) · Δpool +6.412(t 2.93) · Δ대조 +8.056(t 2.74) · 폴드 15/20 · 앞/뒤 +12.865/+0.298(both_positive) · k=5 Δpool +4.980(t 2.42) · 세션 20. ② 지연1일 open_h1 h1(70세션): k=3 arm +2.588(t 2.91) · Δpool +1.977(t 2.76) · Δ대조 +2.331(t 2.49) · 앞/뒤 +4.122/+1.054 · k=5 Δpool +1.221(t 2.62). ③ 지연1일 close_h h5: k=3 arm +5.977(t 3.85) · Δpool +4.127(t 3.63) · Δ대조 +6.172(t 4.23) · 앞/뒤 +9.514/+2.440.",
 "verdict": "신호 유지 — 겹침창 인플레이션 아님(t 2.93 > 2) & 하루 늦게 사도 남는다(Δpool +1.98 t 2.76 · 1일 청산). 단 독립세션 20 · 뒤 절반이 +0.3%p 로 사실상 0 · 종목 집중(CG312 상위3종목 53%) → 표본 확대 전 승격 금지.",
 "verdict_note": "근거 파일 reports/overnight/cg313_stride5_closeh.json · cg313_delay1_h1.json · cg313_delay1_h5.json · 지연 arm 생성 도구 scripts/_cg313_delay_placebo.py(폴드 내 다음 거래일 매핑, 1400/1600행 유지).",
 "done_at": now,
 "created_at": now,
}

cg314 = {
 "id": "CG314",
 "status": "pending",
 "priority": 1,
 "kind": "measurement",
 "owner": "quant-model-engineer",
 "affects_model": False,
 "metric": "fillable_topk_expectancy",
 "title": "[표본 확대] CG306 예측을 평가창 전수(2026-01-13~11-06, 200거래일)로 다시 뽑아 독립세션 40+ 로 재판정",
 "hypothesis": "CG313 이 보인 신호는 독립세션 20(비중첩)에서만 확인됐고 뒤 절반이 0 이었다. CG306 평가는 10폴드 × 8날짜 = 80세션만 썼다(가용 200거래일). 같은 모델·같은 학습구간을 그대로 두고 --dates-per-fold 20 으로 평가창을 전수로 늘리면 세션이 2.5배(200→stride5 독립 40)가 되어, '신호가 실재하는가'를 표본 부족 없이 판정할 수 있다. 재학습 불필요(모델 재사용).",
 "evidence": "CG313: stride5 독립 20세션 Δpool +6.412(t 2.93)·앞 +12.865/뒤 +0.298 / 지연1일 h1 Δpool +1.977(t 2.76, 70세션). 표본 하한: champion_promote 돈 게이트 세션 ≥40. 데이터: stock_prices 2026-01-13~2026-11-06 200거래일 가용(실측).",
 "arm": "champion_cand_cg306 (q0.05 라벨 재학습 후보, 200종목·2026창·rank 제거)",
 "baseline": "세션 체결가능 풀평균 + 대조군 cg297_norank(같은 폴드·같은 날짜)",
 "counterfactual": "cg297_norank (같은 폴드·같은 날짜·같은 필터)",
 "command": "docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 timeout 12600 python -u scripts/champion_robust_eval.py --model-dir app/models/champion_cand_cg306 --label-kind quantile --label-q 0.05 --folds 10 --dates-per-fold 20 --stocks 200 --universe training --universe-seed 0 --asof-date 2026-11-13 --dump-all --dump-preds /app/reports/overnight/cg314_preds.jsonl --out /app/reports/overnight/cg314_robust.json'",
 "summary_out": "/app/reports/overnight/cg314_robust.json",
 "est_minutes": 150,
 "success": "사전등록: 예측 전수 채점 후 ① stride5(독립 40세션) k=3 Δ(arm−풀평균) t ≥ 2 & halves both_positive ② stride1 k=3 순기대 > 0 & Δ대조 t ≥ 2 → 표본 확대에서도 유지 = 승격 후보로 리뷰보드에 올린다(라벨 규칙 변경 승인 대상, 최종 승격은 사람). ① 미달(Δ < +0.1%p 또는 t < 1.5) = 20세션 우연 → 라벨 축 종결.",
 "note": "2026-11-17 02:2x 자율 등록(CG313 결과 직후). 런타임 실측 근거: 10폴드×8날짜(=80날짜) 평가가 55분 → 200날짜 ≈ 137분. 컨테이너 timeout 12600s(3.5h)로 06:55 하드컷(개장 3시간 전 여유). 학습 없음 · GPU/CPU 경쟁은 CG310 종료 후 시작 전제.",
 "created_at": now,
}

cg315 = {
 "id": "CG315",
 "status": "backlog",
 "priority": 3,
 "kind": "measurement",
 "owner": "quant-model-engineer",
 "affects_model": False,
 "metric": "fillable_topk_expectancy",
 "title": "[유니버스 교체] q0.05 라벨 신호를 300종목·다른 시드 유니버스에서 같은 런으로 재측정 — 유니버스 특이성 반증",
 "hypothesis": "교훈 실측(2026-09-28): 49종목 패널의 +0.0338 신호가 150종목 패널에서는 전 성분 음수로 뒤집혔다. CG306/CG313 신호는 200종목·시드0 유니버스 한 점에서만 확인됐다 → 300종목·시드1 로 재학습·재채점해 부호가 유지되는지 본다.",
 "evidence": "CG313 신호(Δpool +6.412 t 2.93)는 단일 유니버스(200종목·seed 0) 측정. 교훈: 조정 축 신호는 넓은 유니버스에서 부호가 뒤집힌 사례 있음.",
 "arm": "champion_cand_cg315 (q0.05 · 300종목 · 시드1)",
 "baseline": "같은 런 대조군 + 세션 풀평균",
 "counterfactual": "cg306/cg313(200종목·시드0) 같은 프로토콜",
 "command": "docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 timeout 11100 python -u -m app.training.retrain_champion --label-kind quantile --label-q 0.05 --horizon 5 --start-date 2025-08-22 --end-date 2025-11-20 --stock-limit 300 --universe-seed 1 --drop-serving-blind-features --out-dir app/models/champion_cand_cg315'",
 "summary_out": "/app/reports/overnight/cg315_robust.json",
 "est_minutes": 200,
 "success": "사전등록: k=3 Δ(arm−풀평균) 부호가 + 이고 t ≥ 2 이면 유니버스 강건(승격 검토 유지). 부호 반전 또는 t < 1 이면 '200종목 특이'로 축 종결.",
 "note": "2026-11-17 자율 등록. CG314(표본 확대) 결과가 유지일 때만 착수 — 순서 의존. 재학습 3h + 평가 2h 라 밤 1회분.",
 "created_at": now,
}

items.extend([cg313, cg314, cg315])
d['items'] = items
d['updated'] = now
d['updated_at'] = now
open(P, 'w').write(json.dumps(d, ensure_ascii=False, indent=1))
print('items', len(items))

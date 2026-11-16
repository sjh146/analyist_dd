import json

p = 'docs/QUANT_MODEL_BACKLOG.json'
b = json.load(open(p))
by = {i['id']: i for i in b['items']}

c = by['CG310']
c['result'] = {
    'folds3': {'auc': 0.4963, 'std': 0.0191, 'folds': [0.47, 0.515, 0.5038], 'min': 0.47, 'max': 0.515,
               'win_vs_0.5': '2/3', 'dates': 24, 'rows': 480},
    'folds5': {'auc': 0.5105, 'std': 0.0354, 'folds': [0.5363, 0.5525, 0.48, 0.4587, 0.525], 'min': 0.4587,
               'max': 0.5525, 'win_vs_0.5': '3/5', 'dates': 40, 'rows': 800},
    'folds10': {'auc': 0.5430, 'std': 0.1092,
                'folds': [0.7588, 0.545, 0.5875, 0.6325, 0.3588, 0.5813, 0.435, 0.4813, 0.6, 0.45],
                'min': 0.3588, 'max': 0.7588, 'win_vs_0.5': '6/10', 'dates': 80, 'rows': 1600},
    'delta_vs_baseline_0.5406': [-0.0443, -0.0301, 0.0024],
    'n_folds_below_chance': '8/18 (3폴드 1 · 5폴드 2 · 10폴드 5)',
}
c['verdict'] = (
    '노이즈 — 폴드 설정 의존 (사전등록 미달: 3설정 모두 ΔAUC≥+0.02 & 폴드승률≥0.7 이어야 강건인데 '
    'ΔAUC -0.0443/-0.0301/+0.0024 · 폴드승률 0.67/0.60/0.60)'
)
c['verdict_note'] = (
    '10폴드 0.5430 은 폴드1(0.7588) 단일 이상치가 끌어올린 값이고 std 0.1092(가정 ±0.03의 3.6배)다. '
    '폴드 수를 5·3 으로 줄이면 0.5105 → 0.4963 로 떨어져 0.5 미만. 평균은 표본(날짜수 24/40/80)에 따라 '
    '단조 상승(0.4963→0.5105→0.5430)하지만 마지막 56날짜가 0.5627 를 만드는 구조라 시점 편중도 배제 못함. '
    '→ AUC 축으로는 승격 근거 없음(등록 기준선 0.5406 대비 개선 0). 단 최우선 규칙대로 AUC 는 판정자가 아니며, '
    '돈 축은 후보풀 오염(CG311 defect)을 고친 CG314(--dump-all, 200날짜)가 판정한다.'
)
c['evidence_file'] = ('services/xgboost-ml/reports/overnight/cg310_robust_f3.json · cg310_robust_f5.json · '
                      'cg306_robust.json(10폴드)')

b['updated_at'] = '2026-11-17T03:15:00+09:00'
open(p, 'w').write(json.dumps(b, ensure_ascii=False, indent=1))
json.load(open(p))
print('CG310 result recorded; verdict:', c['verdict'][:60])

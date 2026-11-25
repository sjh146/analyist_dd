import json, shutil, datetime

P = 'docs/QUANT_MODEL_BACKLOG.json'
shutil.copy(P, P + '.bak_20261125_0745')
b = json.load(open(P))
items = b['items']

by_id = {i['id']: i for i in items}

# 1) CG197: 측정무효(데이터 커버리지) — 후속 CG450 으로 대체
c = by_id['CG197']
c['status'] = 'superseded'
c['result'] = {
    'verdict': '측정무효 — 인트라데이 원천 커버리지 게이트 탈락',
    'detail': ('사전등록 ①(결측률<20%) 실측 탈락. 패널 61,640행 중 봉이 있는 행: '
               'KIS minute_bars 379(0.61%, id_* 18컬럼 전부 all-zero) · '
               'Naver minute_close_bars 666(1.08%, idc_* 비영 572~666행). '
               '원인 1: --only CG197_ctl_h5/CG197_intraday_h5 설정이 CONFIGS 에 미등록 → rc=1 '
               '(실측 2026-11-25 07:16). 원인 2(더 근본): 패널 구간 2025-08-04~2026-11-17 은 '
               '분봉 이력(완전일 11일·2026-11-10~11-24)과 겹치는 날짜가 3~4일뿐이라 '
               '피처를 붙여도 98.9%가 0 → 어떤 A/B 도 노이즈. 후속: 완전일 20 도달(추정 12-10) '
               '+ panel v7 빌드 후 CG450 에서 재측정.'),
    'delta': None, 'per_exp': None, 'rc': 1}
c['note'] = ((c.get('note') or '')[:600] +
             ' | 2026-11-25 07:4x 엔지니어 판정: 설정 미등록(rc=1)은 고칠 수 있지만 '
             '커버리지 1.08% 로 사전등록 게이트를 못 넘으므로 측정무효로 닫고 CG450(needs_setup)으로 '
             '이관했다. 같은 결론의 근거 파일: services/xgboost-ml/reports/overnight/panel_intraday_patch.json'
             '(id_ 0.61%) · panel_idc_patch.json(idc_ 1.08%).')

# 2) CG450 선행 조건에 실측 커버리지 기록
g = by_id['CG450']
g['note'] = ((g.get('note') or '')[:600] +
             ' | 2026-11-25 실측(엔지니어): 원천 커버리지가 선행 조건의 실체다 — '
             'panel_prod200 구간(2025-08-04~2026-11-17)에 분봉이 있는 행은 1.08%(666/61,640, '
             'Naver minute_close_bars) · KIS minute_bars 0.61%(id_* 전컬럼 0). '
             'CG197 을 이 항목으로 대체했다. 완전일 11/20(2026-11-25 적재, 추정 12-10 도달).')

# 3) CG466: 오늘 07:08 실행 완료(완전일 11/20) → 오늘 16:10 이후로 미룸(중복 수집 방지)
h = by_id['CG466']
h['not_before'] = '2026-11-25T16:10'
h['note'] = ((h.get('note') or '')[:600] +
             ' | 2026-11-25 07:08 실행 rc=0 · 완전일 11/20 · 커버 0.99 · 봉/일 330 → '
             'not_before 16:10(장 마감 후 적재)으로 옮겨 같은 날 중복 수집을 막았다.')

# 4) 다음 가설 CG482 등록 (같은 런 2-arm 돈 분해)
new = {
    'id': 'CG482',
    'title': ('[돈·분해] 챔피언 열세의 원인 — 같은 런·같은 폴드에서 173피처 단일 vs core48 단일의 '
              '돈 k5 와 AUC 를 짝으로 분해'),
    'status': 'pending',
    'priority': 1,
    'owner': 'engineer',
    'command': ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 5400 "
                "python -u scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_prod200_v6o.npz "
                "--folds 5 --seeds 3 --only LS_quant_q30_h5 CO_core30_h5 "
                "--dump-preds /app/reports/overnight/cg482_arms.jsonl --dump-all-codes' && "
                "docker exec stock_xgboost_ml sh -c 'cd /app && timeout 900 python -u "
                "scripts/baseline_ranking_money.py --dump /app/reports/overnight/cg482_arms.jsonl "
                "--split-exps --k 3,5,10 --horizon 5 --exit close_h --rand-reps 20 "
                "--json-out /app/reports/overnight/cg482_money.json'"),
    'counterfactual': ('LS_quant_q30_h5 (173피처 전량·edge top30) — CG474 실측에서 배포 챔피언 앙상블 '
                       '-0.4469%p vs core48 arm +0.5819%p (Δ-1.0288, block t -2.03, 255세션·체결필터·수수료 0.21)'),
    'success': ('① rc=0 · 덤프에 arm 2개×5폴드×3시드 존재 ② core48 − 173피처 k5 Δ ≥ +0.5%p AND block t ≥ 2 '
                '(같은 런·같은 폴드) → 원인을 "피처 수(무정보 126개 노이즈)"로 특정, 승격 후보 등록 '
                '③ |Δ| < 0.5%p 또는 t < 2 → 원인은 앙상블 평균/학습창(다음 분해: 앙상블 vs 단일 same-features) '
                '④ AUC 폴드 평균차도 병기(±0.02 미만이면 노이즈로만 기록) ⑤ rc≠0/요약 미갱신 = 실행실패'),
    'hypothesis': ('CG474 는 배포 챔피언(3모델 앙상블·173피처)이 단순 core48 arm 보다 돈이 1.03%p/세션 나쁘다고 쟀다'
                   '(t -2.03). 그런데 arm 의 학습조건이 "게이트 없는 48피처"로 고정된 비교가 아니라 원인(피처 수·앙상블·'
                   '학습창)이 미분리다. 같은 런·같은 폴드에서 173 vs 48 단일모델을 짝지으면 원인을 조건 하나로 좁힌다. '
                   '선행 근거: 패널 210피처 중 무정보 126개·종목상수 48개(panel_feature_screen 실측) → '
                   '무정보 피처가 랭킹을 희석한다는 가설이 가장 먼저 검정할 대상이다.'),
    'est_minutes': 70,
    'note': ('2026-11-25 07:4x 엔지니어 자율 신설(머니 축 5연속 노이즈 후 다음 가설). '
             '다중비교 주의: 최근 CG477~CG481 은 같은 덤프 재사용 arm 이라 판정 문턱을 낮추지 않는다. '
             '이 항목은 새 학습(같은 런 2-arm)이라 덤프 재사용이 아니다.'),
    'result': None,
}
items.append(new)

json.dump(b, open(P, 'w'), ensure_ascii=False, indent=2)
print('CG197 ->', by_id['CG197']['status'])
print('CG466 not_before ->', by_id['CG466']['not_before'])
print('added CG482; items=', len(items))

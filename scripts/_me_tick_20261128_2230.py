#!/usr/bin/env python3
"""2026-11-28 22:2x — CG542 분할 실측 보강 + CG544(k 격자) 등록."""
import json
import datetime

P = '/home/jhshi/analyist_dd/docs/QUANT_MODEL_BACKLOG.json'
bl = json.load(open(P, encoding='utf-8'))
items = {i['id']: i for i in bl['items']}
now = datetime.datetime.now().astimezone().isoformat(timespec='seconds')

cg = items['CG542']
cg['result']['detail'] += (
    ' 분할(paired session_deltas 앞/뒤 140세션): k=1 +0.262/-0.122(neither) · k=2 +0.295/+0.199(both+) · '
    'k=3 +0.299/+0.186(both+).'
)
cg['notes'] = (cg.get('notes') or '').replace(
    '갭: 요약 JSON 에 **paired 분할(front/back) 이 없다**(arm_halves 만 있음) — 사전문턱의 \'분할 both_positive\' 를 '
    '기계 판정하려면 fillable_topk_expectancy 에 paired halves 를 추가해야 한다(수리 후보).',
    '분할은 요약의 paired.session_deltas(세션별 Δ) 앞/뒤 절반으로 계산했다(별도 코드 불필요).'
)
cg['updated_at'] = now

assert 'CG544' not in items
items['CG544'] = {
    'id': 'CG544',
    'title': ('[돈·배포규모 경계] 익일시가 h1 arm 의 Δ·t 가 문턱(t≥2·분할 both+)을 유지하는 **최소 k** 를 격자로 확정 — '
              '배포 규칙(하루 몇 건) 결정 근거 (CG542 후속)'),
    'kind': 'experiment',
    'owner': 'engineer',
    'priority': 1,
    'affects_model': True,
    'metric': 'fillable_topk_expectancy',
    'summary_out': '/app/reports/overnight/cg544_kgrid.jsonl',
    'est_minutes': 40,
    'arm': 'LBL_overnight_h1',
    'counterfactual': 'LS_quant_q30_h5 (같은 패널·같은 런) — k=5 Δ+0.282(t 2.78, CG534)',
    'command': ("docker exec stock_xgboost_ml sh -c 'cd /app && export PYTHONPATH=/app && for K in 4 5 6 7 8 10; do "
                "timeout 900 python -u scripts/fillable_topk_expectancy.py "
                "--arm-jsonl /app/reports/overnight/cg534_po.jsonl --arm-tag LBL_overnight_h1 "
                "--control-jsonl /app/reports/overnight/cg534_po.jsonl --control-tag LS_quant_q30_h5 "
                "--stride 1 --exit next_open --entry close --horizon 1 --k $K "
                "--fee-buy 0.0005 --fee-sell 0.0005 --tax-sell 0.002 "
                "--json-out /app/reports/overnight/cg544_k${K}.json; done'"),
    'hypothesis': ('CG542 실측: k=1 Δ+0.070(t 0.29·분할 neither) · k=2 +0.247(t 1.49·both+) · k=3 +0.243(t 1.80·both+). '
                   'CG534 의 k=5 는 Δ+0.282(t 2.78·both+), CG537 의 k=10 은 Δ+0.186(t 2.90·분할 unstable). '
                   '부호와 분할은 k≥2 에서 일관되지만 t 는 k=3 까지 2 미만 — t 가 2 를 넘는 최소 k 를 찾으면 '
                   '"하루 몇 종목을 담아야 이 라벨이 성립하는가"에 숫자로 답할 수 있다.'),
    'success': ('사전등록(변경 금지): ① k 격자 전체 표를 그대로 보고(최고 k 만 골라 인용 금지 — 다중비교) '
                '② t≥2 & 분할 both_positive & Δ≥+0.1%p 를 동시에 만족하는 최소 k 를 기록(k*) '
                '③ k* 가 5 이상이면 "배포 규칙이 하루 ≥k* 건일 때만 라벨 변경 근거 있음"으로 트레이더 결정사항에 올린다 '
                '④ 어떤 k 도 통과 못 하면 라벨 축 완전 종결 ⑤ rc≠0 = 실행실패'),
    'expected': 'k 증가에 따라 t 상승(분산 축소). k=5~6 근처에서 t 2 를 넘을 것으로 본다.',
    'cost': '40분(기존 덤프 재사용, 학습 0)',
    'created_at': now,
    'note': ('2026-11-28 22:3x 엔지니어 자율(CG542 후속). 사전문턱은 CG542/CG534 와 동일(0.30%p·체결성 필터 ON·stride 1·280세션). '
             '이 격자는 "가장 좋은 k 고르기"가 아니라 **경계 찾기**다 — 결과 표 전체를 원장에 남긴다.'),
    'status': 'pending',
    'attempts': [],
    'reported': False,
}
bl['updated_at'] = now
json.dump(bl, open(P, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print('ok; pending:', [i['id'] for i in bl['items'] if i.get('status') == 'pending'])

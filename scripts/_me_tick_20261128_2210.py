#!/usr/bin/env python3
"""2026-11-28 22:1x 엔지니어 자율 틱 — CG541 종결 + CG542(다음 가설) + CG543(수리 기록)."""
import json
import datetime

P = '/home/jhshi/analyist_dd/docs/QUANT_MODEL_BACKLOG.json'
bl = json.load(open(P, encoding='utf-8'))
items = {i['id']: i for i in bl['items']}
now = datetime.datetime.now().astimezone().isoformat(timespec='seconds')

cg541 = items['CG541']
cg541['status'] = 'done'
cg541['result'] = {
    'verdict': '측정 불가(사전등록 전제 붕괴) — 판본 동일날짜 교집합 0일',
    'detail': (
        'scripts/version_overlap_probe.py 신설·실행(2026-11-28 22:1x, 읽기 전용, ml_predictions 137,418행). '
        'v1.0 = 63,436행·15날짜(2026-09-22~10-08) / champ-20261002T024420-0.5513 = 73,982행·20날짜(2026-10-09~11-27) '
        '→ **공통 날짜 0일** → 사전등록 ①②(날짜별 ρ·top-k 겹침) 모두 적용 불가. '
        '즉 전방 성적표의 "판본혼합"은 같은 날짜 동시혼합이 아니라 **시간상 순차 배포**(09-22~10-08 v1.0 → 10-09~ champ)다. '
        '판본별 체결 순기대(k=10·수수료 왕복 0.21%p·체결성 필터 ON, h1): '
        'champ 13세션 topk +1.318% vs 풀 +0.294% Δ+1.024%p(t 1.10, 분할 +1.397/+0.705, 양세션 6/13) · '
        'v1.0 9세션 topk -0.627% vs 풀 -0.368% Δ-0.259(t -0.34, +0.453/-0.828, 3/9). '
        'h5: champ Δ-0.851(t -0.66) · v1.0 Δ-2.305(t -1.58) — 둘 다 음. '
        '⚠ 판본 간 비교는 시점 교란이다(풀 널 기준선부터 h1 champ +0.294 vs v1.0 -0.368 = 국면 차이) → 모델 우열 근거로 쓰지 않는다. '
        '결합(rank-mean 앙상블) 측정은 같은 날짜에 두 판본 예측이 있어야 가능 → 불가. 게다가 앙상블 축은 CG30 에서 최대 +0.0057 로 이미 종결됐다. '
        '→ 축 종결: 판본 결합은 재개하지 않는다. 전방 성적표는 pooled 헤드라인 대신 판본별 슬라이스를 1급으로 읽는다(CG543 으로 체결 머니 헤드라인 교체).'
    ),
    'rc': 0,
    'evidence_file': '/app/reports/overnight/cg541_version_overlap.json',
}
cg541['notes'] = (cg541.get('notes') or '') + (
    f" | {now[:16]} 엔지니어: scripts/version_overlap_probe.py 구현·실행(체결성 필터·수수료·널 기준선은 "
    "forward_scorecard.py 함수 재사용 = 단일 진실원). 판본 교집합 0일 실측 → 사전등록 ②(결합 arm)는 요건 미충족, "
    "①(ρ≥0.95 → 종결)도 측정 불가지만 결론은 동일하게 '축 종결'. 판본별 1차 수치를 남겼다."
)
cg541['updated_at'] = now
cg541['attempts'] = cg541.get('attempts') or []
cg541['attempts'].append({'at': now, 'rc': 0, 'note': 'version_overlap_probe.py 실행(읽기 전용)'})

assert 'CG542' not in items
cg542 = {
    'id': 'CG542',
    'title': ('[돈·배포규모] 익일시가 h1 arm 의 Δ가 **하루 1·2·3건**(실배포 규모)에서도 남는가 — '
              'k=5/10 이득이 소수 종목 분산의 산물인지 (CG534 후속)'),
    'kind': 'experiment',
    'owner': 'engineer',
    'priority': 1,
    'affects_model': True,
    'metric': 'fillable_topk_expectancy',
    'summary_out': '/app/reports/overnight/cg542_kgrid.jsonl',
    'est_minutes': 25,
    'arm': 'LBL_overnight_h1',
    'counterfactual': 'LS_quant_q30_h5 (같은 패널·같은 폴드·같은 런) — k=5 는 CG534 실측 Δ+0.282(t 2.78)',
    'command': ("docker exec stock_xgboost_ml sh -c 'cd /app && export PYTHONPATH=/app && for K in 1 2 3; do "
                "timeout 900 python -u scripts/fillable_topk_expectancy.py "
                "--arm-jsonl /app/reports/overnight/cg534_po.jsonl --arm-tag LBL_overnight_h1 "
                "--control-jsonl /app/reports/overnight/cg534_po.jsonl --control-tag LS_quant_q30_h5 "
                "--stride 1 --exit next_open --entry close --horizon 1 --k $K "
                "--fee-buy 0.0005 --fee-sell 0.0005 --tax-sell 0.002 "
                "--json-out /app/reports/overnight/cg542_k${K}.json; done'"),
    'hypothesis': (
        '모든 익일시가 h1 라벨 실측(CG533~CG539)은 k=5·10 바스켓이다. 그런데 실제 배포 설정은 하루 5건(단타 전용)이고 '
        'k 가 줄면 선택이 상위 소수 종목에 집중돼 같은 신호라도 Δ·t 가 붕괴할 수 있다(전례 CG496: 상위5종목이 Δ의 51.9%). '
        'k=1·2·3 에서 Δ 부호와 t 가 유지되는지가 "이 라벨 변경이 실거래 규모에서 쓸 수 있는가"를 가른다.'
    ),
    'success': (
        '사전등록(변경 금지): ① k=1·2·3 **각각** Δ ≥ +0.1%p/세션 & t ≥ 2 & 분할 both_positive → "배포 규모 강건" '
        '(라벨 변경 승인 요청 근거로 넘긴다) ② k=1 에서만 붕괴(k≥2 유지) → "k≥2 필요" 로 기록하고 배포 규칙(하루 5건)과 정합 확인 '
        '③ k≤3 전부 Δ < +0.1%p 또는 t < 2 → "다건 분산 전용"으로 라벨 축 종결 ④ rc≠0·요약 없음 = 실행실패(노이즈로 쓰지 않는다)'
    ),
    'expected': 'k 감소에 따라 분산↑·t↓ 가 예상. 부호 유지 여부가 핵심.',
    'cost': '25분(기존 덤프 재사용, 학습 0)',
    'created_at': now,
    'note': '2026-11-28 22:1x 엔지니어 자율(CG541 종결 후속). 기존 cg534_po.jsonl(280세션·10폴드)만 재사용하므로 '
            'GPU/CPU 학습 없이 수 분에 끝난다 — 틱이 그대로 실행 가능. 수수료는 CG537(배포 가정 0.3%p)과 같은 값을 쓴다.',
    'attempts': [],
    'reported': False,
}

assert 'CG543' not in items
cg543 = {
    'id': 'CG543',
    'title': '[측정정합성 수리] 전방 성적표 헤드라인이 체결성 필터 **전** top10 을 1급으로 보고한다 — 체결 머니로 교체',
    'kind': '수리',
    'owner': 'engineer',
    'priority': 2,
    'affects_model': False,
    'metric': None,
    'created_at': now,
    'status': 'done',
    'result': {
        'verdict': '수리 완료(회귀 PASS)',
        'detail': (
            'scripts/model_engineer_cycle.py `_forward_summary_line`: 헤드라인을 `money` 블록(체결성 필터 + 수수료 왕복 + '
            '풀 널 기준선)으로 교체하고 미필터 top10/all 은 "미필터(참고)" 로 격하, kept_ratio 병기. '
            '실측 근거(2026-11-28 22:1x, 같은 파일·같은 창): 미필터 top10 -0.1398% vs 전체 +0.2376% 인데 '
            '체결 top10 +0.5225% vs 풀 +0.0231% (Δ+0.4995%p, t 0.79) → **부호가 갈린다**. 원인은 top10_ret_mean 이 '
            '체결성 필터 전 순위로 뽑기 때문(kept_ratio 0.2657 = 74%가 살 수 없는 행, 상한가·급등 종목이 당일 최고점수를 차지). '
            '회귀: scripts/_forward_scorecard_money_test.py 36/36 PASS(헤드라인 4건 신설) · scripts/_forward_refresh_test.py 16/16 PASS.'
        ),
        'rc': 0,
    },
    'note': ('최우선 규칙 3(체결성 필터 없이 계산한 기대값은 보고 금지)이 매시간 요약 줄에서 위반되고 있었다. '
             '판정 지표(money.Δ)는 이미 필터 경유였으나, 사용자가 먼저 읽는 헤드라인이 미필터 값이라 '
             '"top10 이 전체보다 나쁘다"는 반대 방향 인상을 줬다.'),
}

bl['items'].append(cg542)
bl['items'].append(cg543)
bl['updated_at'] = now
if isinstance(bl.get('updated'), str):
    bl['updated'] = now[:10]

json.dump(bl, open(P, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print('ok items', len(bl['items']))

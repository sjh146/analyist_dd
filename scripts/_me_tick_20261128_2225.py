#!/usr/bin/env python3
"""2026-11-28 22:2x — CG542(k=1·2·3 배포규모) 판정 기록."""
import json
import datetime

P = '/home/jhshi/analyist_dd/docs/QUANT_MODEL_BACKLOG.json'
bl = json.load(open(P, encoding='utf-8'))
items = {i['id']: i for i in bl['items']}
now = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
cg = items['CG542']
cg['status'] = 'done'
cg['result'] = {
    'verdict': '사전등록 ③ — 배포 규모(1~3건) 유의성 미달(다건 k≥5 전용)',
    'detail': (
        'arm LBL_overnight_h1 vs 대조군 LS_quant_q30_h5 · exit next_open · entry close · stride 1 · '
        '수수료 왕복 0.30%p · 체결성 필터 ON · 280세션(10폴드) · cg534_po.jsonl 재사용(학습 0, 22:2x 실행 ~1분/arm). '
        'k=1: arm +0.066% vs ctl -0.005 → Δ+0.070(t 0.29, 양세션 162/114/4=57.9%, 폴드 양 8/10) · '
        'k=2: arm +0.158 vs ctl -0.089 → Δ+0.247(t 1.49, 165/114/1=58.9%, 폴드 9/10) · '
        'k=3: arm +0.129 vs ctl -0.113 → Δ+0.243(t 1.80, 162/117/1=57.9%, 폴드 9/10). '
        '사전문턱(각 k 에서 Δ≥+0.1%p & t≥2 & 분할 both_positive) → k=1 탈락(Δ·t 동시 미달), k=2·3 은 **t<2 로 탈락**. '
        '부호는 세 k 모두 양(+)이고 k=2·3 은 방향 일관이나 유의성 미달. '
        '참고(필터 OFF): k=2 Δ+0.395(t 2.36) · k=3 Δ+0.375(t 2.41) — 필터가 t 를 2 아래로 떨어뜨린다(살 수 없는 상한가 종목이 이득의 일부). '
        '대조: 같은 런의 k=5 는 CG534 Δ+0.282(t 2.78) 로 문턱 통과, k=10 은 CG537 Δ+0.186(t 2.90) 이나 분할 unstable [+0.297/-0.124]. '
        '→ 판정: 이 라벨의 돈 엣지는 **k≥5 바스켓에서만** 문턱을 넘는다. 하루 1~3건 규모(집중 매매)에서는 근거 없음. '
        '배포 규칙이 하루 5건(상한)이라 k=5 는 상한에서만 성립 — 라벨 축은 "다건 분산 전용"으로 종결하고, '
        '승격/라벨 변경 논의는 트레이더가 하루 5건 다건 규칙을 유지한다는 전제에서만 성립한다(전략 소유자 결정).'
    ),
    'rc': 0,
    'evidence_files': ['/app/reports/overnight/cg542_k1.json',
                       '/app/reports/overnight/cg542_k2.json',
                       '/app/reports/overnight/cg542_k3.json'],
}
cg['notes'] = (cg.get('note') or '') + (
    f" | {now[:16]} 실행·판정 완료(티크가 아니라 이 세션에서 직접 3-arm 실행, 각 ~1분). "
    "갭: 요약 JSON 에 **paired 분할(front/back) 이 없다**(arm_halves 만 있음) — 사전문턱의 '분할 both_positive' 를 "
    "기계 판정하려면 fillable_topk_expectancy 에 paired halves 를 추가해야 한다(수리 후보)."
)
cg['updated_at'] = now
cg['attempts'] = [{'at': now, 'rc': 0, 'note': 'k=1·2·3 실행(fillable_topk_expectancy, cg534_po.jsonl)'}]
bl['updated_at'] = now
json.dump(bl, open(P, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print('ok', cg['status'], len(bl['items']))
print('pending:', [i['id'] for i in bl['items'] if i.get('status') == 'pending'])

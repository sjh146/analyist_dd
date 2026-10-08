"""일회성: 백로그 정리(축 종결 3건) + CG141(공매도 수집범위) 등록 + CG73 증거 보강. idempotent."""
import json, sys

P = 'docs/QUANT_MODEL_BACKLOG.json'
d = json.load(open(P))
items = d['items']
by = {i.get('id'): i for i in items}
assert len(by) == len(items), 'duplicate ids!'

CLOSE = {
 'U1B': ("축 종결 — 150종목 패널 q0.25 vs q0.30 은 이미 실측됨(CG18) 이라 별도 항목 불필요. "
         "CG18(2026-09-28, panel_150u·게이트 ON·5폴드×5시드·같은 런): q0.30 0.5189±0.0135 vs q0.25 0.5070 → Δ−0.0119(q0.25 열세). "
         "게다가 이 항목의 재개조건('U1 이 유니버스 신호를 낼 때')은 미충족 — U1 Δ−0.0266(악화). "
         "라벨 분위 축은 CG19/CG20/CG24 로도 종결(q0.05 이득이 두 번째 유니버스에서 미재현)."),
 'U2':  ("축 종결 — 유니버스 크기/구성 축은 이미 닫혔다: CG18 밀도교정 150 vs 49 Δ−0.0059 · "
         "CG40 배포경로 유니버스 교체 짝 Δ+0.0056(1/3 창) · CG13 같은 크기 서로소 5구간 폭 0.0287(=교체 잡음 > 사전문턱 +0.02). "
         "500종목(≈14h) 빌드는 같은 축의 반복이라 기대이득 0 이므로 착수하지 않는다(장시간 빌드 비용만 든다)."),
 'C1':  ("축 종결 — 확률 보정은 순위를 바꾸지 않는다(실측 CG83, 2026-10-03, 같은 OOS 26,685행·225일·cross-fitted): "
         "Platt 보정으로 Brier 0.2552→0.2494·ECE 0.0534→0.0139 로 신뢰도는 회복되지만 AUC 0.5305→0.5300(불변)이고 "
         "확률이 0.5 근처로 압축된다(p90 0.5378·max 0.6364) → 절대문턱 0.55 선택이 raw 37.0% → 보정후 6.2% 로 줄어든다. "
         "즉 '보정 후 문턱으로 실현수익 개선'은 성립하지 않는다(보정은 0.53 AUC 모델에서 0.55 문턱을 살릴 수 없다). "
         "소비 경로 수리는 보정 배선이 아니라 정책(절대문턱 → 분위 top-k)이며 그 대상은 트레이더 소유다(CG82, 승인 대기)."),
}
for cid, res in CLOSE.items():
    it = by.get(cid)
    if it is None:
        print('MISSING', cid); continue
    if it.get('status') in ('done', 'closed_rejected'):
        print('already closed:', cid); continue
    it['status'] = 'closed_rejected'
    it['result'] = res
    print('closed:', cid)

# CG73 증거 보강
g73 = by['CG73']
note = g73.get('note', '')
add = (" | 2026-10-09 실측(수집 범위): krx_short_selling 17,786행·210종목·2026-05-26~10-07, 값 자체는 정상"
       "(short_volume/total_volume/short_ratio 비영 97.9~98.6%, NULL 은 balance_quantity 만 100%) — 그런데 "
       "**수집 종목 ∩ 학습 유니버스 = 11/200**(날짜 교집합 84일) → 패널 short_selling_ratio 비영 1.51% 는 배선 결함이 "
       "아니라 수집 범위 갭(news_events 12/200 과 동형). 재현: scripts/short_selling_scope_probe.py")
if '2026-10-09 실측(수집 범위)' not in note:
    g73['note'] = note + add
    print('CG73 note updated')
else:
    print('CG73 note already updated')

# CG141 등록
if 'CG141' in by:
    print('CG141 exists — skip')
else:
    new = {
        "id": "CG141", "status": "needs_setup", "priority": 1,
        "title": "[수집기/리서처] 공매도(krx_short_selling) 수집 종목을 학습 유니버스로 확장 — 11/200 교집합",
        "hypothesis": ("공매도 비율은 실재하는 횡단면 팩터인데, 현재 원천(krx_short_selling)의 수집 종목 집합이 "
                       "학습 유니버스(panel_prod200)와 11종목만 겹쳐 패널에서 사실상 죽어 있다(비영 1.51%). "
                       "수집 종목을 유니버스와 일치시키면 시점정합 공매도 피처가 200종목 전부에서 살아난다."),
        "success": ("수집 종목 ∩ 학습 유니버스 ≥ 90% AND 패널 short_selling_ratio 비영 ≥ 20%(가용 구간) "
                    "AND 게이트 ON 5폴드×5시드 짝 Δ ≥ +0.02(대조 CO_core30_h5)"),
        "counterfactual": "CO_core30_h5",
        "metric": "wf_sweep_summary",
        "est_minutes": 60,
        "setup_needed": ("선행 조건 2개: ① 수집 범위 — services/krx-collector(또는 scripts/kis_short_selling_backfill.py)의 "
                         "종목 목록을 학습 유니버스(select_training_universe)와 일치(수집기 소유·승인 대상) "
                         "② 이력 — 현재 2026-05-26~ = 약 95거래일이라 279일 패널의 마지막 폴드만 덮는다 "
                         "(부분 커버는 폴드 std 로 검출 불가). 즉 확장만으로는 부족하고 누적이 필요하다."),
        "note": ("2026-10-09 엔지니어 실측: DB krx_short_selling 17,786행·210종목·2026-05-26~2026-10-07 · "
                 "short_volume/total_volume/short_ratio 비영 97.9~98.6%(소스 자체는 건강) · balance_quantity 만 100% NULL. "
                 "그러나 종목 교집합 11/200(날짜 교집합 84일) → feature_pipeline.py L874-890 의 배선은 정상이고(짝 837행 중 "
                 "패널 0 은 9행뿐) 죽은 원인은 순수하게 수집 범위다. 재현 도구 scripts/short_selling_scope_probe.py. "
                 "뉴스(news_events 12/200)·SNS(2026-06~ 누적) 와 같은 계열의 범위/누적 갭이다."),
        "attempts": [],
    }
    items.append(new)
    print('CG141 added')

d['items'] = items
json.dump(d, open(P, 'w'), ensure_ascii=False, indent=2)

# 재검증
d2 = json.load(open(P))
ids2 = [i['id'] for i in d2['items']]
assert 'CG141' in ids2, 'CG141 not persisted!'
assert not any(len([x for x in ids2 if x == i]) > 1 for i in set(ids2)), 'dup ids'
print('verify: total', len(ids2), '| CG141 status=', [i for i in d2['items'] if i['id'] == 'CG141'][0]['status'],
      '| U1B/U2/C1 =', [i['status'] for i in d2['items'] if i['id'] in ('U1B', 'U2', 'C1')])

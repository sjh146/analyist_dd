#!/usr/bin/env python3
"""2026-11-22 06:2x — CG414 재판정 + CG415/CG416/CG417 기록 + CG418 등록 (일회성 기록 스크립트)."""
import json
import os

P = 'docs/QUANT_MODEL_BACKLOG.json'
b = json.load(open(P))
items = b['items']
by = {it['id']: it for it in items}

# 1) CG414 재판정 (판정기 k 하드코딩 수리 후)
c414 = by['CG414']
c414['verdict'] = ("노이즈 — 하위 k 는 풀평균을 유의하게 하회하지 않는다(k5 Δ(풀) −0.179 t −0.24 · "
                   "k10 −0.572 t −1.35). top-k 는 Δ+1.978(t 3.71)이지만 분할표본 불안정(앞 +1.03/뒤 −0.86).")
c414['detail'] = ("k=5 arm +0.074%p vs 풀 −1.904 (Δ+1.978 t 3.71 · 77/48) · k=10 −0.647 (Δ+1.257 t 2.80) · "
                  "하위k k5 −2.083 (Δ(풀) −0.179 t −0.24) · k10 −2.476 (Δ −0.572 t −1.35) · "
                  "사전등록(하위k Δ≥+0.5%p & t≥2) 미달 → 모델 정보는 롱쪽 비대칭, 숏/회피 축 종결. "
                  "판정기가 ('3','5') 하드코딩으로 이 측정을 '판정불가'로 닫았으나 수리 후 재판정"
                  "(_fillable_topk_pool_test 20/20 PASS).")
c414['result'] = {"verdict": c414['verdict'], "detail": c414['detail'], "delta": 1.978,
                  "per_exp": None, "rc": 0,
                  "note": "판정기 수리 후 재판정(2026-11-22 06:2x). 측정 자체는 rc=0 정상이었다."}
if c414.get('attempts'):
    c414['attempts'][-1]['verdict'] = '노이즈(재판정)'


def mk(**kw):
    it = {"kind": "measurement", "owner": "engineer", "affects_model": False, "priority": 2,
          "status": "done", "metric": "fillable_topk_vs_pool",
          "created": "2026-11-22T06:25:00+09:00"}
    it.update(kw)
    return it


cg415 = mk(
    id="CG415", priority=1, est_minutes=10,
    title="[머니·국면] top-k 의 널 대비 우위가 상승/하락 국면 어디서 나오는가 — 비중첩(stride 5)+국면분해 동시 적용",
    arm="CO_rank_smooth_d1_h5 top-k (cg409_nogate)",
    counterfactual="같은 덤프의 세션별 풀평균(무작위 k 기대) — 상승/하락 국면별",
    command=("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 timeout 900 python -u "
             "scripts/fillable_topk_expectancy.py --arm-jsonl /app/reports/overnight/cg411_dump.jsonl "
             "--arm-tag cg409_nogate --control-jsonl /app/reports/overnight/cg411_null.jsonl "
             "--control-tag cg409_hashnull --k 5,10 --sides top,bottom --primary-side top "
             "--regime-split pool_sign --stride 5 --exit close_h --horizon 5 "
             "--json-out /app/reports/overnight/cg415_money_regime.json'"),
    summary_out="/app/reports/overnight/cg415_money_regime.json",
    hypothesis=("CG413 의 Δ+1.978(t 3.71)은 stride 1 중첩 t 인플레 위에 있다. 비중첩(stride 5)으로 줄이고 "
                "국면 부호로 갈라 어느 쪽에서 이득이 나오는지 본다."),
    evidence=("CG413: 전 125세션 stride1 Δ+1.978(t 3.71) · 절대 +0.074%p · CG414: 하위k 무엣지(Δ−0.18~−0.57) · "
              "CG412 10폴드에서 절대 순기대가 −0.034로 뒤집힘."),
    success=("사전등록: 상승 국면에서도 Δ(풀) ≥ +0.5%p & t≥2 이면 '선택 알파' · 상승 Δ≈0/음수 & 하락 Δ>0 이면 "
             "'방어 틸트'로 규정하고 롱 알파 축을 닫는다."),
    expected="상승 국면 Δ≈0, 하락 국면 Δ>0 (방어 틸트).",
    cost="집계만 1초(재학습 0)",
    verdict=("국면의존(방어 틸트) — 27세션 비중첩 표본에서 상승 12세션 Δ(풀) −0.297(t −0.24), "
             "하락 15세션 Δ(풀) +5.109(t 4.21). 10폴드 덤프 재현(CG417)에서도 상승 −1.715(t −1.80)·"
             "하락 +3.827(t 2.85) → 롱 선택 알파 아님, 하락 방어 틸트. 하위k 무엣지(Δ −0.79/−0.62) 유지."),
    detail=("stride 5(비중첩) k=5 arm +1.318%p vs 대조 −2.020 (Δ+3.337 t 2.51) · 풀 −1.388 · "
            "Δ(풀) +2.706(t 2.71 · 18/9) · k=10 Δ(풀) +1.962(t 2.14) · 국면분해: 상승 12세션 풀 +4.888 → "
            "top +4.591 (Δ −0.297 t −0.24) / 하락 15세션 풀 −6.409 → top −1.300 (Δ +5.109 t 4.21) · "
            "하락 국면에서도 절대 −1.3%p → '덜 잃는' 엣지이지 버는 엣지가 아니다."),
)

cg416 = mk(
    id="CG416", priority=2, est_minutes=8,
    title="[머니·사전규칙] 사후 국면분해의 이득을 진입 시점 정보만(trailing_pool 5일 게이트)으로 잡을 수 있는가",
    arm="CO_rank_smooth_d1_h5 top-k + trailing_pool 게이트",
    counterfactual="같은 덤프 전체(게이트 없음) · CG316(풀 대상 타이밍 게이트 = 노이즈)",
    command=("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 timeout 600 python -u "
             "scripts/fillable_topk_expectancy.py --arm-jsonl /app/reports/overnight/cg411_dump.jsonl "
             "--arm-tag cg409_nogate --control-jsonl /app/reports/overnight/cg411_null.jsonl "
             "--control-tag cg409_hashnull --k 5,10 --sides top --primary-side top --gate-rule trailing_pool "
             "--gate-days 5 --gate-min-pct 0 --stride 5 --exit close_h --horizon 5 "
             "--json-out /app/reports/overnight/cg416_gate_trailing.json'"),
    summary_out="/app/reports/overnight/cg416_gate_trailing.json",
    hypothesis=("국면을 사후에 알면 이득이 보이지만, 살 수 있는 시점 정보(과거 5세션 풀수익 부호)로도 갈린다면 "
                "트레이더 규칙이 된다."),
    evidence=("CG415: 상승 Δ −0.30 / 하락 Δ +5.11 · CG316: 풀 대상 trailing_pool 게이트는 ON 풀 +0.116(t 0.31)로 "
              "노이즈 종결(모델 top-k 대상은 미시험)."),
    success=("사전등록: ON 대역 Δ(풀) ≥ +1.0%p & t≥2 & ON 세션 ≥ 40 이면 트레이더 규칙 후보 · "
             "표본 <20세션이면 판정불가로 남긴다."),
    expected="stride 5 표본이 27세션뿐이라 ON/OFF 가 10세션대로 쪼개져 판정 불가.",
    cost="1초",
    verdict="판정불가(표본부족) — ON 7세션 / OFF 5세션. ON Δ(풀) ≈ +2.97(n=7)·OFF +1.06(n=5) 로 방향은 맞지만 t 검정 불가.",
    detail=("게이트 ON 7세션: 풀 −3.205 → top5 −0.238(t −0.10) · OFF 5세션: 풀 +3.921 → top5 +4.977(t 1.36) · "
            "채점 12/27세션(게이트가 이력을 요구) · 사전문턱 'ON≥40세션' 미달 → 전 세션(stride 1, n=125)에서 재시험(CG418)."),
)

cg417 = mk(
    id="CG417", priority=1, est_minutes=6,
    title="[재현성] 국면 조건부 구조가 폴드 기하에 의존하는가 — 10폴드 덤프에 같은 국면분해 적용",
    arm="CO_rank_smooth_d1_h5 top-k (10폴드 덤프 cg412)",
    counterfactual="3폴드 덤프(cg411)의 같은 분해(CG415): 상승 −0.297 / 하락 +5.109",
    command=("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 timeout 600 python -u "
             "scripts/fillable_topk_expectancy.py --arm-jsonl /app/reports/overnight/cg412_dump.jsonl "
             "--arm-tag cg409_nogate --control-jsonl /app/reports/overnight/cg412_null.jsonl "
             "--control-tag cg409_hashnull --k 5,10 --sides top --primary-side top --regime-split pool_sign "
             "--stride 5 --exit close_h --horizon 5 "
             "--json-out /app/reports/overnight/cg417_regime_10f.json'"),
    summary_out="/app/reports/overnight/cg417_regime_10f.json",
    hypothesis=("CG412 에서 절대 순기대가 기하에 따라 +1.318→−0.034 로 뒤집혔다. 국면 조건부 구조까지 "
                "기하 의존이면 '방어 틸트' 결론도 한 런의 우연이다."),
    evidence="CG412(10폴드): Δ(풀) +1.569(t 1.54) · 절대 −0.034%p/세션. CG415(3폴드): 상승 Δ −0.297 / 하락 Δ +5.109.",
    success="두 기하에서 부호 구조(상승 ≤0, 하락 >0)가 같으면 구조는 재현된 것 — 다르면 국면 결론을 철회한다.",
    expected="구조 유지(하락 >0, 상승 ≤0), 크기는 축소.",
    cost="1초",
    verdict="재현 확인 — 10폴드에서도 상승 11세션 Δ(풀) −1.715(t −1.80)·하락 16세션 Δ(풀) +3.827(t 2.85). 절대 순기대 −0.034%p(사실상 0).",
    detail=("상승 풀 +5.390 → top5 +3.675 · 하락 풀 −6.410 → top5 −2.583 · 전체 Δ(풀) k5 +1.569(t 1.54) "
            "k10 +1.442(t 1.28) · 부호 구조 두 기하 동일 → 방어 틸트 결론 유지."),
)

cg418 = mk(
    id="CG418", priority=1, est_minutes=8, status="pending",
    title="[머니·사전규칙 후속] 예측가능 국면 게이트를 전 세션(stride 1, n≈125)에서 재시험 — CG416 표본부족(12세션) 해소",
    arm="CO_rank_smooth_d1_h5 top-k + trailing_pool 게이트(전 세션)",
    counterfactual="게이트 없음(전 세션 Δ(풀) +1.978 t 3.71) · CG316 풀 대상 게이트(노이즈)",
    command=("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 timeout 900 python -u "
             "scripts/fillable_topk_expectancy.py --arm-jsonl /app/reports/overnight/cg411_dump.jsonl "
             "--arm-tag cg409_nogate --control-jsonl /app/reports/overnight/cg411_null.jsonl "
             "--control-tag cg409_hashnull --k 5,10 --sides top --primary-side top --gate-rule trailing_pool "
             "--gate-days 5 --gate-min-pct 0 --stride 1 --exit close_h --horizon 5 "
             "--json-out /app/reports/overnight/cg418_gate_full.json'"),
    summary_out="/app/reports/overnight/cg418_gate_full.json",
    hypothesis=("stride 5 는 표본을 27세션으로 줄여 게이트를 12세션으로 쪼갰다. 전 세션(n=125)에서 게이트를 걸면 "
                "ON/OFF 40세션 이상이 확보돼 t 검정이 가능하다(중첩 인플레는 감수하고 '스크린'으로만 읽는다)."),
    evidence=("CG416: ON 7/OFF 5세션 → 판정불가 · CG415/CG417: 사후 국면 이득은 하락 전용(상승 Δ −0.30~−1.72) · "
              "CG316: 풀 대상 게이트는 ON 풀 +0.116(t 0.31)로 노이즈 종결."),
    success=("사전등록: ON 대역 top-k Δ(풀) ≥ +1.0%p & t≥2 & ON 세션 ≥ 40 이고 OFF 대역 Δ ≤ +0.5%p 면 "
             "'예측 게이트로 방어 틸트 포획 가능' → 트레이더 규칙 후보로 핸드오프. 미달이면 타이밍 축을 최종 종결."),
    expected="ON 대역 Δ 양(+)·OFF 대역 Δ 0 근처 — 방향은 맞을 가능성이 있으나 중첩 t 인플레로 과대평가된다.",
    cost="1초",
)

for it in (cg415, cg416, cg417, cg418):
    if it['id'] not in by:
        items.append(it)
        by[it['id']] = it

tmp = P + '.tmp'
with open(tmp, 'w', encoding='utf-8') as f:
    json.dump(b, f, ensure_ascii=False, indent=1)
os.replace(tmp, P)

# 2) 원장에 재판정·신규 측정 4건 기록
import datetime
ts = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
recs = [
    ("CG414", "노이즈", c414['detail']),
    ("CG415", cg415['verdict'], cg415['detail']),
    ("CG416", cg416['verdict'], cg416['detail']),
    ("CG417", cg417['verdict'], cg417['detail']),
]
titles = {i['id']: i.get('title') for i in items}
with open('data/reports/model_engineer_ledger.jsonl', 'a', encoding='utf-8') as f:
    for i, (iid, v, d) in enumerate(recs):
        f.write(json.dumps({
            "ts": ts, "id": iid, "title": titles.get(iid), "rc": 0,
            "metric": "fillable_topk_vs_pool", "verdict": v, "detail": d,
            "mode": "manual-followup", "reported": True,
            "note": ("판정기 수리 후 재판정" if iid == "CG414" else "틱 세션에서 즉시 실행(집계만, 재학습 0)"),
        }, ensure_ascii=False) + "\n")

print("ok:", len(items), "items")
for i in ('CG414', 'CG415', 'CG416', 'CG417', 'CG418'):
    print(i, by[i]['status'], '|', str(by[i].get('verdict'))[:60])

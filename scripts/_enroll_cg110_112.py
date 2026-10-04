#!/usr/bin/env python3
"""CG110/CG111 등록(done) + CG112 등록(pending) — 2026-10-05 06:1x 세션.

교훈(백로그 조작): 리스트를 리바인딩하면 d['items'] 가 옛 리스트를 가리킨다 → in-place mutate 만 쓰고,
커밋 전에 같은 파일을 다시 읽어 any(i['id']==...) 를 assert 한다.
"""
import json
import os

P = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "QUANT_MODEL_BACKLOG.json")
P = os.path.abspath(P)
d = json.load(open(P, encoding="utf-8"))
items = d["items"]
have = {i.get("id") for i in items}

CG110 = {
    "id": "CG110",
    "title": "비ML 기준 랭킹(모멘텀·반전·저변동·무작위)의 돈 지표 스크리닝 — 모델 vs 단순 팩터(기록상 0회)",
    "status": "done",
    "priority": 2,
    "metric": "fillable_topk_vs_pool",
    "command": ("docker exec stock_xgboost_ml sh -c 'cd /app && python scripts/baseline_ranking_money.py "
                "--dump /app/reports/overnight/cg109_preds.jsonl --k 3,5,10 --horizon 5 "
                "--json-out /app/reports/overnight/cg110_baseline_money.json'"
                " && 같은 명령을 cg108_at_preds.jsonl 로(150종목 풀링)"),
    "counterfactual": "무작위 랭킹(rand, seed 고정) + 세션 풀 평균(널 = 무작위 k 기대값)",
    "success": ("모델과 **같은 행·같은 세션·같은 체결성 필터·같은 수수료**에서 단순 팩터 랭킹의 "
                "Δ(top-k − 풀평균)이 사전문턱(arm>0 · Δ≥+0.1%p/세션 · t≥2 · 분할 both_positive)을 넘는가. "
                "넘으면 'ML 없이도 돈이 되는 랭킹이 있다'(트레이더 즉시 대체 후보), 못 넘으면 "
                "'단순 팩터도 돈 정보가 없다'로 데이터 축 결론을 보강한다."),
    "est_minutes": 5,
    "result": {
        "verdict": "노이즈",
        "detail": (
            "계측기 = scripts/baseline_ranking_money.py(신설) — champion_robust_eval --dump-preds 의 같은 행에 "
            "기준 랭킹 점수를 채워 fillable_topk_expectancy 경로(enrich→apply_filters→baskets→pool_series→"
            "paired_stats)를 그대로 재사용. **검증: 같은 덤프로 돌린 model arm 이 CG108/CG109 참조 수치와 "
            "소수점까지 일치**(k=3 +0.367/Δ+0.409/t0.98/[+1.24/−0.50] · k=5 +0.667/+0.710/t2.09/[+1.05/+0.28] · "
            "CG109 k=3 +0.714/+0.854/t2.01/[+1.46/−0.03]). "
            "블록[150:200) 50종목·225세션: 풀평균 −0.140 · model k3 Δ+0.854 t2.01 unstable · k5 +0.776 t2.47 "
            "unstable · mom20 k3 +0.977 t1.93 unstable · mom60 k3 −0.337 · rev5 k3 +0.476 t1.23 · "
            "lowvol20 k3 −0.264 · rand k3 +0.259 t0.77. "
            "블록[0:150) 150종목·225세션(풀링): 풀평균 −0.043 · model k5 +0.710 t2.09 both_positive · "
            "k10 +0.682 t3.11 both_positive · mom20 k3 −0.506/k5 −0.039 · mom60 k5 0.000 · lowvol20 k5 +0.269 t0.88 "
            "· rev5 k5 +1.003 t2.64 unstable[+2.43/−0.50] · rand k5 −0.493 t−1.71. "
            "→ **어떤 단순 랭킹도 사전문턱(분할 both_positive 포함)을 통과하지 못했다** — 모멘텀·저변동은 "
            "풀평균 수준이거나 그 이하, 5일 반전만 큰 Δ(+1.0)이나 분할 전반부에 몰려 있어(unstable) 잡음 "
            "패턴이다. 무작위 대조가 +0.26 ~ −0.49 로 움직여 '단일 런 Δ ≤0.5 는 랭킹 무관'임을 보여준다."),
        "delta": None,
        "per_exp": None,
        "rc": 0,
    },
    "note": ("한계 명시: 단일 기간(2025-10-14~2026-09-10)·단일 유니버스·각 225세션 → 검출 바닥이 ±0.4(k=3) "
             "수준이라(CG111) 이 런은 '스크리닝'이지 종결 근거가 아니다. 그래도 결론은 보강된다: "
             "'ML 모델 대신 단순 팩터'로 갈 수 있는 후보가 실측으로 없다. "
             "계측기 경로: scripts/baseline_ranking_money.py(--standard 로 metric `fillable_topk_vs_pool` "
             "표준 스키마도 출력 가능)."),
    "session": "2026-10-05",
}

CG111 = {
    "id": "CG111",
    "title": "돈 지표 검출 바닥(MDE) 실측 — 무작위 랭킹 대조 21회로 Δ(top-k−풀평균) 분포 정량화(기록상 0회)",
    "status": "done",
    "priority": 2,
    "metric": "fillable_topk_vs_pool",
    "command": ("docker exec stock_xgboost_ml sh -c 'cd /app && python scripts/baseline_ranking_money.py "
                "--dump /app/reports/overnight/cg108_at_preds.jsonl --baselines rand --k 3,5,10 --horizon 5 "
                "--rand-reps 21 --json-out /app/reports/overnight/cg111_mde_rich.json'"),
    "counterfactual": "무작위 랭킹 21개 시드(같은 행·같은 세션) — Δ 의 '랭킹 무관' 분포",
    "success": ("무작위 대조 Δ 분포의 sd 를 실측해 **돈 판정의 최소검출효과(MDE)** 를 확정한다. "
                "이후 모든 돈 verdict 에 '이 Δ 는 검출 바닥 이하' 를 명시한다(AUC 의 '동전 0.5' 에 해당하는 "
                "장치가 지금까지는 '풀 평균' 뿐이었고, '무작위 랭킹과 구분되는가' 는 없었다)."),
    "est_minutes": 3,
    "result": {
        "verdict": "기준선 실측",
        "detail": (
            "블록[0:150) 150종목·225세션·h5·수수료 0.21%p, 무작위 랭킹 21회: "
            "k=3 Δ mean −0.069 · **sd 0.415** · min −0.778 · max +0.744 · SE 0.093 · "
            "k=5 mean −0.069 · **sd 0.301** · min −0.508 · max +0.503 · SE 0.067 · "
            "k=10 mean −0.031 · **sd 0.159** · min −0.261 · max +0.340 · SE 0.036. "
            "→ 해석: **단일 런의 Δ 는 k=3 에서 ±0.42(1sd) 까지 무작위로 나온다** — 모델의 k=3 Δ+0.409 는 "
            "무작위 1.0sd(z1.0)로 '동전 랭킹과 구분 불가', k=5 Δ+0.710 은 z2.4, k=10 Δ+0.682 는 z4.3. "
            "즉 k=3 을 포함한 'k=3 AND k=5' 사전등록 조건은 검출력이 낮은 조건이었다(그래서 CG108/CG109 가 "
            "둘 다 노이즈로 닫혔다). 검출 바닥을 낮추는 유일한 길은 세션 수 확대(검정력)다."),
        "delta": None,
        "per_exp": None,
        "rc": 0,
    },
    "note": ("⚠ 이 결과를 근거로 **문턱을 낮추지 마라**(사후 선택 = 목표 이동). k=5/k=10 의 z>2 는 힌트이며, "
             "확인 경로는 ①세션 수 2배(장기 청정 패널) 또는 ②배포 챔피언 자체의 돈 판정(CG112)이다. "
             "sd 는 k 에 반비례한다(k=10 이 k=3 의 2.6배 좁다) — 넓은 바스켓일수록 검출력이 높다."),
    "session": "2026-10-05",
}

CG112 = {
    "id": "CG112",
    "title": "배포 챔피언(app/models/champion)의 돈 지표 **직접** 판정 — 청정 유니버스·전 유니버스 덤프(기록상 0회)",
    "status": "pending",
    "priority": 1,
    "metric": "fillable_topk_vs_pool",
    "est_minutes": 55,
    "command": (
        "docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 4200 python -u "
        "scripts/champion_robust_eval.py --model-dir app/models/champion --universe training --stocks 200 "
        "--folds 5 --dates-per-fold 10 --label-kind abs --horizon 1 "
        "--train-start 2026-06-25 --train-end 2026-09-23 "
        "--dump-preds /app/reports/overnight/cg112_champ_all.jsonl --dump-tag champ --dump-all "
        "--out /app/reports/overnight/cg112_champ_robust.json' && docker exec stock_xgboost_ml sh -c "
        "'cd /app && python scripts/fillable_topk_expectancy.py "
        "--arm-jsonl /app/reports/overnight/cg112_champ_all.jsonl --arm-tag champ "
        "--control-jsonl /app/reports/overnight/cg112_champ_all.jsonl --control-tag champ "
        "--k 3,5,10 --exit close_h --horizon 5 --json-out /app/reports/overnight/cg112_champ_money.json'"),
    "counterfactual": "세션 풀 평균(널 = 무작위 k 기대값) — 같은 덤프·같은 필터·같은 수수료. arm vs arm 은 베타를 구분 못 한다(CG95).",
    "success": ("k=3·k=5 둘 다: 순기대>0 · Δ(arm−풀평균)≥+0.1%p/세션 · t≥2 · 분할 both_positive → "
                "'배포 모델이 시장 베타를 넘는 돈 엣지가 있다'(승격은 별도 절차). 미달이면 "
                "'배포 챔피언의 돈 엣지 미검출' 로 기록하고 CG111 검출 바닥(±0.42/±0.30/±0.16 by k)을 함께 적는다."),
    "note": ("왜 필요한가: CG96~CG109 의 돈 판정은 전부 **arm 모델**(app/models/wf/cg92_q05 등)과 abs_thresh "
             "스윕 모델이었다 — 실거래에 쓰는 app/models/champion 은 돈 지표로 한 번도 채점된 적이 없다"
             "(전방 스코어카드는 11일 표본, 창 기반은 AUC). ⚠ 학습구간 겹침 창은 --train-start/--train-end 로 "
             "제외하므로 남는 창은 학습 **이전**이다 = 전방 검증 아님(CG45/58/61/62 와 같은 한계). "
             "두 커맨드를 && 로 이어 붙였고 구동기는 `--json-out`(돈 지표)을 요약으로 읽는다(--out 은 champion "
             "산출물이라 오탐하지 않는다 — _arg 는 정확 토큰 비교)."),
    "hypothesis": ("실제로 매매에 쓰는 모델이 돈 지표에서도 무신호라면, 트레이더가 소비하는 확률·신호 자체가 "
                   "돈과 무관하다는 결론이 배포 경로에서 직접 확인된다(지금까지는 대리 모델로만 측정)."),
    "session": "2026-10-05",
}

new = [x for x in (CG110, CG111, CG112) if x["id"] not in have]
for x in new:
    items.append(x)
json.dump(d, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

# 재읽기 assert — 조용한 등록 실패 방지
d2 = json.load(open(P, encoding="utf-8"))
ids = {i.get("id") for i in d2["items"]}
for x in (CG110, CG111, CG112):
    assert x["id"] in ids, f"등록 실패: {x['id']}"
print("enrolled:", [x["id"] for x in new])
print("total items:", len(d2["items"]))

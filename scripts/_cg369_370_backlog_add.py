#!/usr/bin/env python3
"""CG369/CG370 등록 (2026-11-20 04:5x) — 실측: 적립 시리즈는 표본이 아니라 **모집단**이 매번 바뀐다.

실측 근거(2026-11-20, 같은 날 09:54Z vs 18:07Z 두 실행):
  · CG360(22날짜) 0.5132 vs CG366(23날짜) 0.5349 → Δ+0.0217. 23번째 날짜 하나로는 불가능한 이동.
  · 두 덤프의 날짜별 종목 교집합 **70/200 (35%)** — `_default_date_from()` 이 UNIVERSE_ASOF_DATE
    미설정 시 now()-60일 창을 쓰고, 적격 집합이 조금만 변해도 rng.shuffle 입력 순서가 바뀌어
    200종목이 통째로 재추첨된다(app/training/universe.py docstring 이 이미 기록한 함정).
  · 같은 22날짜 안에서 A전용 130종목 0.5086 vs B전용 130종목 0.5405 → 모집단 교체만으로 Δ−0.0319.
    CG13(서로소 30종목 5구간) 폭 0.0287 과 같은 크기 = 사전문턱 +0.02 보다 크다.
  · 같은 70종목에서도 예측이 재현되지 않는다(날짜 70행 중 동일 2~10행, max|Δpred| 0.066; 표본 라벨 39/1540 변경).
  ⇒ CG366 의 판정 '미해상 — 표본 미증가'는 사실이나, 표본을 늘려도 **모집단이 매일 바뀌면** 적립 비교는
    성립하지 않는다. 먼저 유니버스를 동결해 재현성을 증명해야 한다(CG369), 그 위에서 적립을 재개한다(CG370).

동결 수단(검증 완료, 2026-11-20 04:2x): 컨테이너에서
`UNIVERSE_ASOF_DATE=2026-10-01 select_training_universe(limit=200, seed=0)` 2회 → 200/200 일치.
"""
import json

P = "docs/QUANT_MODEL_BACKLOG.json"
d = json.load(open(P))
TS = "2026-11-20T04:55:00+09:00"

FREEZE_ENV = "-e UNIVERSE_ASOF_DATE=2026-10-01"

cg369 = {
    "id": "CG369",
    "status": "pending",
    "priority": 1,
    "kind": "측정정합성",
    "owner": "엔지니어",
    "affects_model": False,
    "metric": "reproducibility_overlap",
    "title": (
        "[측정 정합성·차단 해소] 적립 시리즈 유니버스 동결(UNIVERSE_ASOF_DATE) — 같은 플래그 8시간 간격 "
        "두 실행의 종목 교집합이 70/200 이었고, 그 교체만으로 AUC 가 −0.0319 움직인다"
    ),
    "hypothesis": (
        "CG360→CG366 의 Δ+0.0217 은 새 날짜가 아니라 **모집단 교체**다(교집합 35%, 같은 런 안 전용집단 "
        "AUC 차 −0.0319). UNIVERSE_ASOF_DATE 로 창을 고정하면 같은 플래그·같은 날짜의 두 실행이 "
        "200/200 동일 모집단·동일 예측을 내고, 그때서야 날짜 적립(SE 축소)이 성립한다."
    ),
    "command": (
        "docker exec " + FREEZE_ENV + " stock_xgboost_ml sh -c 'cd /app && "
        "OMP_NUM_THREADS=4 timeout 2400 python -u scripts/champion_robust_eval.py --model-dir app/models/champion "
        "--stocks 200 --folds 1 --dates-per-fold 6 --asof-date 2026-11-19 --train-end 2026-10-01 "
        "--oos-start 2026-10-02 --universe training --universe-seed 0 "
        "--dump-preds /app/reports/overnight/cg369_frozen_a.jsonl --dump-tag cg369a "
        "--out /app/reports/overnight/cg369_frozen_a.json && "
        "OMP_NUM_THREADS=4 timeout 2400 python -u scripts/champion_robust_eval.py --model-dir app/models/champion "
        "--stocks 200 --folds 1 --dates-per-fold 6 --asof-date 2026-11-19 --train-end 2026-10-01 "
        "--oos-start 2026-10-02 --universe training --universe-seed 0 "
        "--dump-preds /app/reports/overnight/cg369_frozen_b.jsonl --dump-tag cg369b "
        "--out /app/reports/overnight/cg369_frozen_b.json && "
        "python -u scripts/_cg369_repro_check.py --a reports/overnight/cg369_frozen_a.jsonl "
        "--b reports/overnight/cg369_frozen_b.jsonl --json reports/overnight/cg369_repro_frozen.json'"
    ),
    "counterfactual": (
        "동결 없음(CG360 vs CG366 실측): 교집합 70/200(0.35) · 전용집단 AUC 0.5086 vs 0.5405 · "
        "max|Δpred| 0.066 · 판정 '재현 안 됨'"
    ),
    "success": (
        "① rc=0 · 두 실행 모두 6날짜 채점 ② 교집합 200/200 (overlap_frac 1.0) ③ max|Δpred| < 1e-9 "
        "④ |ΔAUC| ≤ 0.005 → '재현됨(적립 비교 가능)' 확정. 미달이면 남은 원인을 분해해 기록"
        "(동결로도 예측이 흔들리면 시간의존 피처(그래프 감쇠 등)가 원인 — 그 축은 별건)"
    ),
    "expected": "측정 잡음 바닥을 고정해 이후 적립 실험의 Δ 가 '모델 vs 모집단' 혼합이 아니게 만든다.",
    "rationale": (
        "21사이클 연속 무개선 판정의 측정 계열(CG358·360·362·363·366)이 모두 '같은 유니버스'를 전제했는데 "
        "그 전제가 거짓이었다. 이걸 고치지 않으면 앞으로의 모든 적립 Δ 도 해석 불가."
    ),
    "est_minutes": 14,
    "created_at": TS,
    "attempts": 0,
    "needs_approval": False,
    "note": (
        "동결 검증(2026-11-20 04:2x): UNIVERSE_ASOF_DATE=2026-10-01 로 select_training_universe 2회 → 200/200 일치. "
        "이후 모든 적립 명령은 " + FREEZE_ENV.replace("-e ", "") + " 를 반드시 붙인다."
    ),
}

cg370 = {
    "id": "CG370",
    "status": "pending",
    "priority": 2,
    "kind": "측정정합성",
    "owner": "엔지니어",
    "affects_model": False,
    "metric": "robust_auc_se",
    "title": (
        "[적립 재개] 동결 유니버스로 30날짜까지 — '표본을 늘리면 판정된다'가 성립하는지 실측"
        "(23날짜 SE 0.013 · 판정 3σ 에 필요한 86날짜와 창 천장 비교)"
    ),
    "hypothesis": (
        "날짜를 23→30 으로 늘리면 날짜 SD(0.0623) 기준 SE 는 0.013→0.0114 로 줄지만, 사전문턱 +0.02 를 "
        "3σ 로 판정하려면 SE 0.0067(=86날짜)이 필요하다. 창 하한은 학습 종료(2026-10-01) 뒤로 못 내리고 "
        "상한은 하루 1거래일씩만 늘어난다 → 200종목 적립은 **구조적으로 판정 불가**이며, 600종목"
        "(날짜 SD 0.0444, CG363) 로 30날짜를 쓰는 편이 SE 0.0081 로 더 빠르다."
    ),
    "command": (
        "docker exec " + FREEZE_ENV + " stock_xgboost_ml sh -c 'cd /app && "
        "OMP_NUM_THREADS=4 timeout 4500 python -u scripts/champion_robust_eval.py --model-dir app/models/champion "
        "--stocks 200 --folds 1 --dates-per-fold 30 --train-end 2026-10-01 --oos-start 2026-10-02 "
        "--universe training --universe-seed 0 --dump-preds /app/reports/overnight/cg370_frozen200.jsonl "
        "--dump-tag cg370_frozen --out /app/reports/overnight/cg370_frozen200.json'"
    ),
    "counterfactual": (
        "CG366(동결 없음·23날짜): 견고 AUC 0.5349 · SD 0.0623 · SE 0.013 · 판정 미해상 — 값 자체는 "
        "모집단이 달라 비교 불가(그래서 동결 후 재측정이 필요)"
    ),
    "success": (
        "① rc=0 · 30날짜 채점 ② 동결 유니버스 기준 견고 AUC 와 날짜 SD 를 남긴다 ③ "
        "SE 실측이 예측(0.0114)과 ±15% 내면 '날짜 적립으로는 판정 불가 → 600종목 축으로 전환'을 근거와 함께 확정 "
        "④ SD 가 0.0623 대비 크게 낮아지면(≤0.05) 적립 지속"
    ),
    "expected": "적립 축의 지속/전환을 숫자로 결정한다(지금은 매일 40분을 판정 불가한 SE 에 쓰고 있다).",
    "rationale": "CG366 의 '표본 미증가' 는 시간이 지나면 풀리는 것처럼 보이지만, SE 목표(0.0067=86날짜)는 창 천장과 모순이다.",
    "est_minutes": 40,
    "created_at": TS,
    "attempts": 0,
    "needs_approval": False,
    "note": "다음 밤 날짜가 늘면 같은 명령의 --dates-per-fold 만 올려 재등록(36날짜 = SE 0.0104 예상).",
}

items = [i for i in d["items"] if i.get("id") not in ("CG369", "CG370")] + [cg369, cg370]
d["items"] = items
d["updated_at"] = TS
d["updated"] = TS
json.dump(d, open(P, "w"), ensure_ascii=False, indent=1)
print("wrote CG369/CG370 · items:", len(items))

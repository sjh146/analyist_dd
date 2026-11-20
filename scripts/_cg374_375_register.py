#!/usr/bin/env python3
"""CG374/CG375 백로그 등록 (2026-11-20 19:2x 엔지니어 자율).

CG374 = 첫 '같은 창' 짝 비교(챔피언 vs 재학습 후보 retrain_cg346).
CG375 = 적립 재개(11-20 일봉 적재 후 29날짜) + not_before 로 무정보 재실행 차단.
"""
import json

P = "/home/jhshi/analyist_dd/docs/QUANT_MODEL_BACKLOG.json"
d = json.load(open(P))
ids = {i["id"] for i in d["items"]}
assert "CG374" not in ids and "CG375" not in ids, "이미 등록됨"

RUN = ("docker exec -e UNIVERSE_ASOF_DATE=2026-10-01 stock_xgboost_ml sh -c 'cd /app && "
       "for m in champion retrain_cg346; do for s in 0 1 2; do OMP_NUM_THREADS=4 timeout 2100 python -u "
       "scripts/champion_robust_eval.py --model-dir app/models/$m --stocks 200 --folds 1 "
       "--dates-per-fold 30 --window-reserve-days 0 --asof-date 2026-11-19 "
       "--train-start 2025-09-24 --train-end 2026-10-01 --oos-start 2026-10-02 "
       "--universe training --universe-seed $s "
       "--dump-preds /app/reports/overnight/cg374_${m}_s${s}.jsonl --dump-tag cg374_${m}_s${s} "
       "--out /app/reports/overnight/cg374_${m}_s${s}.json || exit 1; done; done'")
PAIR = (" && python3 scripts/robust_paired_seeds.py "
        "--arm-a services/xgboost-ml/reports/overnight/cg374_champion_s0.json "
        "services/xgboost-ml/reports/overnight/cg374_champion_s1.json "
        "services/xgboost-ml/reports/overnight/cg374_champion_s2.json "
        "--arm-b services/xgboost-ml/reports/overnight/cg374_retrain_cg346_s0.json "
        "services/xgboost-ml/reports/overnight/cg374_retrain_cg346_s1.json "
        "services/xgboost-ml/reports/overnight/cg374_retrain_cg346_s2.json "
        "--label-a champion --label-b retrain_cg346 "
        "--out data/reports/overnight/cg374_paired.json")
cmd374 = "timeout 12000 " + RUN + PAIR

ACC_CMD = ("docker exec -e UNIVERSE_ASOF_DATE=2026-10-01 stock_xgboost_ml sh -c 'cd /app && "
           "OMP_NUM_THREADS=4 timeout 2700 python -u scripts/champion_robust_eval.py "
           "--model-dir app/models/champion --stocks 200 --folds 1 --dates-per-fold 30 "
           "--window-reserve-days 0 --train-end 2026-10-01 --oos-start 2026-10-02 "
           "--universe training --universe-seed 0 "
           "--dump-preds /app/reports/overnight/cg375_accrual.jsonl --dump-tag cg375_accrual "
           "--out /app/reports/overnight/cg375_accrual.json'")

cg374 = {
    "id": "CG374",
    "status": "pending",
    "priority": 1,
    "kind": "계측기",
    "owner": "엔지니어",
    "affects_model": False,
    "metric": "robust_paired_seeds",
    "summary_out": "/home/jhshi/analyist_dd/data/reports/overnight/cg374_paired.json",
    "title": "[첫 같은 창 짝 비교] 배포 챔피언 vs 재학습 후보(retrain_cg346) — 공통 OOS 2026-10-02~11-19, 3 유니버스 시드",
    "hypothesis": "CG348/CG349 는 창 불일치·창 격자 한계로 '챔피언 vs 후보'를 같은 날짜에서 비교한 적이 없다"
                  "(CG348 은 챔피언 2026-01~05 vs 후보 2026-09~11). 두 arm 에 같은 --train-start/--train-end/"
                  "--oos-start 를 주고 folds=1·dates-per-fold=30 으로 창을 하나로 고정하면 두 모델 모두에게 OOS 인 "
                  "2026-10-02~11-19(가용 23날짜)에서만 채점되어 시드 표집 잡음이 짝 안에서 상쇄된다.",
    "command": cmd374,
    "counterfactual": "CG347(절대 AUC, 같은 모델·다른 유니버스 시드): 0.4887/0.5114/0.5011 = 범위 0.0227 ≥ 사전문턱 0.02 "
                      "→ 절대 비교는 부호조차 불확실. CG348: 창 불일치로 Δ 무의미. CG349: 창 0개로 rc=1.",
    "success": "① rc=0 + cg374_paired.json ② window_mismatch=[] · fold_mismatch=[] (두 arm 같은 창 서명) "
               "③ 짝 Δ SE ≤0.010 이면 '짝 프로토콜로 +0.02 해상 가능' 기록 ④ 부호 3/3 이면 그 방향을 다음 실험 "
               "가설로 등록하되 **승격 판단 금지**(돈 지표 없음)",
    "expected": "21사이클 무개선 판정의 근거가 '프로토콜 혼합'인지 '실재 무신호'인지 가릴 첫 측정. 후보가 같은 창에서 "
                "챔피언을 +0.02 이상 앞서면 재학습 축이 살아 있다는 뜻이고, 아니면 재학습 축도 닫는다.",
    "rationale": "적립 시리즈(28날짜)는 챔피언 단독 절대 AUC 만 잰다 — '무엇으로 바꾸면 나아지는가'는 두 모델을 같은 "
                 "날짜에 놓아야 측정된다. CG350 절단 옵션(패치 준비·오프라인 5/5 PASS)은 그리드형 채점에 필요하지만, "
                 "folds=1 + --oos-start 로 같은 창을 만드는 이 경로는 기본 동작을 바꾸지 않아 승인 없이 실행 가능하다.",
    "est_minutes": 130,
    "not_before": "2026-11-20T20:45",
    "note_asof": "6런 직렬(arm 2 × 시드 3). CG373 실측 19.5분/런 → 약 117분 + 짝 계산. `timeout 2100`(런당) + "
                 "`timeout 12000`(전체) 이라 driver 의 timeout 자기모순 수리(est 130 → 필요 12000)와 충돌하지 않는다. "
                 "--asof-date 2026-11-19 로 창을 고정해 재실행이 비트 비교 가능. 평일 20:00 컨테이너 재생성 회피(not_before).",
    "created_at": "2026-11-20T19:20:00+09:00",
    "needs_approval": False,
}

cg375 = {
    "id": "CG375",
    "status": "pending",
    "priority": 1,
    "kind": "측정정합성",
    "owner": "엔지니어",
    "affects_model": False,
    "metric": "robust_auc_se",
    "title": "[적립 계속] 11-20 일봉 적재 후 29날짜 — 표본이 늘지 않는 재실행을 not_before 로 차단",
    "hypothesis": "CG371(07:27)·CG372(16:24)·CG373(18:26) 이 소수점까지 동일(28날짜·0.5412·SD 0.0368)했다 — "
                  "11-13 라벨이 11-20 종가를 요구하는데 market_data 최신일이 11-19 였다(실측: 최대 2026-11-19·3,832행, "
                  "11-20 행 0). 11-20 일봉이 적재된 뒤 실행하면 11-13 이 채점에 들어와 29날짜가 되고, 3σ(+0.02) 판정에 "
                  "필요한 30날짜까지 1개만 남는다.",
    "command": ACC_CMD,
    "counterfactual": "CG373(28날짜·동결 유니버스·가드 0): 견고 AUC 0.5412 · SD 0.0368 · SE 0.0071 · 3σ 필요 30날짜(남은 2)",
    "success": "① rc=0 · 29날짜 이상 ② SD 가 0.0368 대비 |Δ| ≤0.010 ③ 남은 필요 날짜 ≤1 이면 그 다음 적립 1회로 3σ 판정 단계 진입",
    "expected": "3σ 판정 도달 11/24~11/25 유지. 같은 값을 세 번 재는 낭비(17분×3 = 52분)를 없앤다.",
    "rationale": "날짜는 캘린더로만 늘어난다. 토요일(11-21) 16시 이후면 11-20 종가가 반드시 적재돼 있고 그때 1회만 돌린다.",
    "est_minutes": 25,
    "not_before": "2026-11-21T16:10",
    "note_asof": "구동기에 not_before 가드 추가(2026-11-20 19:1x, scripts/model_engineer_cycle.py next_item) — 이 시각 "
                 "전까지 후보에서 제외된다. 창 가드 0 은 비표준(리뷰보드 승인 대기, CG361).",
    "created_at": "2026-11-20T19:20:00+09:00",
    "needs_approval": False,
}

d["items"].extend([cg374, cg375])
d["updated_at"] = "2026-11-20T19:20:00+09:00"
json.dump(d, open(P, "w"), ensure_ascii=False, indent=2)
print("등록 완료 — items:", len(d["items"]))
for i in d["items"]:
    if i["id"] in ("CG374", "CG375"):
        print(i["id"], i["status"], i.get("not_before"), "cmd_len", len(i["command"]))

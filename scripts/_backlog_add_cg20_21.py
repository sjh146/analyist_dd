#!/usr/bin/env python3
"""CG19 실측 직후 후속 가설 2건을 백로그에 등록한다(멱등: 같은 id 가 있으면 교체).

- CG20: q0.05 라벨 꼬리 레버의 폴드 구조 견고성(5폴드 → 10폴드, 게이트 ON 같은 런 3-arm)
- CG21: 라벨 꼬리 이득의 실질성(precision@k / 기대수익) — 확률 덤프 구현이 선행(needs_setup)
"""
import json
import datetime as dt

PATH = "docs/QUANT_MODEL_BACKLOG.json"
KST = dt.timezone(dt.timedelta(hours=9))
now = dt.datetime.now(KST).replace(microsecond=0).isoformat()

b = json.load(open(PATH, encoding="utf-8"))
ids = {i["id"] for i in b["items"]}

cg20 = {
    "id": "CG20",
    "title": "라벨 꼬리(q0.05) 레버의 폴드 구조 견고성 — 10폴드 확장창에서 게이트 ON 짝 Δ 가 유지되는가",
    "status": "pending",
    "priority": 1,
    "affects_model": True,
    "baseline": {"value": 0.5105,
                 "source": "CG19 같은 런 게이트 ON 대조군 CO_core30_h5(5폴드×10시드, 0.5105±0.0141)"},
    "hypothesis": ("CG19(2026-09-28 19:09)는 게이트 ON 에서 CO_q05_h5 0.5575±0.0329 vs "
                   "CO_core30_h5 0.5105±0.0141 = Δ+0.0470(폴드 5/5 양(+))로, 32사이클 만에 처음으로 "
                   "**같은 런·승격 경로에서** 사전문턱 +0.02 를 넘긴 레버다(라벨 꼬리 두께 q). "
                   "이게 5폴드라는 단일 분할 구조의 우연인지 확인한다."),
    "evidence": ("CG19 실측(panel_150u·게이트 ON·5폴드×10시드·8.1분): q0.30 0.5105±0.0141(행 24,742) · "
                 "q0.10 0.5209±0.0354(행 8,247) · q0.05 0.5575±0.0329(행 4,399, min 0.5078 max 0.6059, "
                 "폴드승률 1.0). 폴드별 q05−q30 = [+0.0255, +0.0631, +0.0678, +0.0011, +0.0775]. "
                 "날짜별 횡단면 AUC 0.5617(pooled 0.5575, gap 0.0042 ≤ 0.01) → 날짜 구성 아티팩트 아님. "
                 "게이트 비용: CG18(게이트 OFF) q05 0.5609 → CG19(게이트 ON) 0.5575 = −0.0034(타 런·타 시드라 약한 비교)."),
    "method": ("같은 패널(panel_150u)·같은 시드에서 folds=10 으로 3-arm 동일 런: CO_core30_h5(대조군) · "
               "CO_q05_h5 · LS_quant_q05_h5(게이트 OFF 대조 — 같은 런에서 게이트 비용을 직접 측정). "
               "판정은 폴드 짝 Δ(q05 − q30) 와 부호검정."),
    "command": ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 timeout 3600 python -u "
                "scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_150u.npz --folds 10 --seeds 5 "
                "--only CO_core30_h5,CO_q05_h5,LS_quant_q05_h5'"),
    "arm": "CO_q05_h5",
    "counterfactual": "CO_core30_h5",
    "check": "python3 scripts/model_engineer_cycle.py --status",
    "check_target": {"op": ">=", "value": 1},
    "metric": "wf_sweep_summary",
    "est_minutes": 12,
    "cost": "약 8~12분 (3 config×5폴드×10시드=150 cell · CG19 150 cell=8.1분 실측). folds=10 이면 3×10×5=150 cell 동일.",
    "success": ("폴드 짝 Δ(q05−q30) ≥ +0.02 이고 양(+) 폴드 ≥ 8/10, 그리고 날짜별 AUC gap ≤ 0.01 → "
                "라벨 꼬리 레버 확정 → 생산 경로 옵션 구현(CG9 확장)+precision@k(CG21)로 승격 검토. "
                "미달(양(+) ≤ 6/10)이면 5폴드 특이로 기록하고 축 종료."),
    "expected": "미지 — 행 수가 q0.05 에서 4,399행(150종목·280일)이라 폴드별 학습표본이 급감해 분산이 크다.",
    "note": ("2026-09-28 19:1x 신설(CG19 직후). pairs(구간 짝) 설계를 쓰지 않는 이유: 유니버스가 동일해 "
             "CG13 류 '유니버스 교체 잡음'이 원리적으로 없고, 이 축의 교란 요인은 폴드/시간 구조다 → "
             "폴드 수를 2배로 늘려 그 구조 의존성을 직접 본다. 같은 런에 게이트 OFF arm 을 넣어 "
             "게이트 비용도 타 런 비교 없이 측정한다."),
}

cg21 = {
    "id": "CG21",
    "title": "라벨 꼬리 이득의 실질성 — 상위 k 정밀도(precision@k)·기대수익 (확률 덤프 신설)",
    "status": "needs_setup",
    "priority": 1,
    "affects_model": True,
    "setup_needed": ("scripts/wf_label_sweep.py 에 --dump-preds <path> 추가(폴드·시드별 (date, code, "
                     "y_true, y_pred, fwd_ret) 를 jsonl 로 저장 — 현재 스윕은 확률을 저장하지 않는다, "
                     "grep 결과 json.dump 는 요약 2곳뿐) + scripts/topk_precision.py 신설(top-k 정밀도·"
                     "바스켓 평균 5일 수익률 집계). 두 파일 모두 이 역할 소유."),
    "baseline": {"value": 0.5105, "source": "CG19 같은 런 CO_core30_h5(게이트 ON·5폴드×10시드)"},
    "hypothesis": ("q 를 좁혀 AUC 가 오르는 것이 '더 극단적인 양성을 맞히는 과제'가 되어서인지, 실제로 "
                   "트레이더가 사는 상위 k 종목의 적중률·기대수익이 올라서인지 분해한다. AUC 는 순위 지표라 "
                   "과제 정의가 바뀌어도 값이 비교되지만, 매매는 상위 k 만 사므로 precision@k 가 최종 KPI 다."),
    "evidence": ("CG19: q0.05 0.5575 vs q0.30 0.5105(Δ+0.0470, 폴드 5/5) · CG18: 게이트 OFF q0.05 0.5609. "
                 "행 수 24,742 → 4,399(양성 밀도 45/일 → 7/일). 트레이더 실계좌 한도: 일 3건·종목 10%·동시 3종목."),
    "method": ("panel_150u 게이트 ON 2-arm(CO_core30_h5·CO_q05_h5)을 --dump-preds 로 돌려 폴드 마지막 시드의 "
               "날짜별 예측을 저장하고, 날짜별 top-5/top-10 바스켓의 정밀도(양성 비율)와 평균 5일 수익률을 집계."),
    "command": ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 timeout 2400 python -u "
                "scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_150u.npz --folds 5 --seeds 3 "
                "--only CO_core30_h5,CO_q05_h5 --dump-preds /tmp/preds_CG21.jsonl' && "
                "python3 scripts/topk_precision.py /tmp/preds_CG21.jsonl --k 5,10"),
    "arm": "CO_q05_h5",
    "counterfactual": "CO_core30_h5",
    "check": "python3 scripts/model_engineer_cycle.py --status",
    "check_target": {"op": ">=", "value": 1},
    "metric": "wf_sweep_summary",
    "est_minutes": 25,
    "cost": "구현 40~60분 + 실행 8분. (setup 선행)",
    "success": ("q0.05 의 날짜별 top-10 정밀도가 q0.30 대비 +5%p 이상이고 top-k 바스켓 평균 5일 수익률도 "
                "개선 → 라벨 꼬리 레버가 '과제 난이도 효과'가 아님을 확인, 승격 검토로 올린다. "
                "정밀도가 비슷하거나 낮으면 AUC 상승은 난이도 효과로 판정하고 라벨 축을 닫는다."),
    "expected": "미지 — q0.05 라벨은 실제 상위 5% 만 양성이라 top-10 정밀도(10건 중 양성)는 최대 ~50% 근처.",
    "note": ("2026-09-28 19:1x 신설(CG19 직후). 북극성 지표(AUC)는 바꾸지 않는다 — 이 항목은 'AUC 상승이 "
             "실매매로 전이되는가'의 보조 검증이다. CG20(폴드 견고성) 통과 후 착수한다."),
}

for it in (cg20, cg21):
    if it["id"] in ids:
        b["items"] = [it if i["id"] == it["id"] else i for i in b["items"]]
        print(f"교체: {it['id']}")
    else:
        b["items"].append(it)
        print(f"추가: {it['id']} (status={it['status']})")

b["updated_at"] = now
json.dump(b, open(PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("총 항목:", len(b["items"]), "| updated_at", now)

#!/usr/bin/env python3
"""CG371 등록 (2026-11-20 07:1x) — 창 보수 가드가 버리는 12거래일을 되찾는다.

실측(2026-11-20 07:05, 컨테이너→postgres 직접 조회):
  · market_data 는 2026-11-19 까지 채워져 있다(11-19 3,722종목 · 11-18 3,839 · 11-06~11-13 모두 3,8xx).
  · 그런데 champion_robust_eval 의 trading_dates 는 `trade_date <= 앵커 − INTERVAL '7 days'`
    + 창 끝 h=5 purge 라서, 앵커 11-19 기준 창이 **2026-11-05 에서 끝난다**(CG370 실측 23날짜).
    즉 11-06·11-09·11-10·11-11·11-12 다섯 날짜가 라벨(5거래일 뒤 종가)까지 다 있는데도 버려진다.
  · 적립 시리즈는 3σ(+0.02) 판정에 31날짜가 필요한데(SD 0.0371), 하루 1날짜씩만 늘어 12/02 도달 예정.
    가드를 0으로 두면 purge 가 미완성 라벨일을 이미 제거하므로 **오늘 바로 28날짜**를 쓸 수 있다
    (검증: trading_dates(reserve=0) → asc 33개 10-02~11-19, usable 28개 10-02~11-12).

이 항목은 코드 기본값을 바꾸지 않는다(--window-reserve-days 기본 7 = 기존 값 비트 동일).
비표준 창으로 잰 값은 protocol 문자열에 window_reserve=0거래일(비표준) 로 남는다.
시리즈 기본값 전환은 리뷰보드 승인 사항(별건 보고).
"""
import json

P = "docs/QUANT_MODEL_BACKLOG.json"
d = json.load(open(P))
TS = "2026-11-20T07:15:00+09:00"

item = {
    "id": "CG371",
    "status": "pending",
    "priority": 1,
    "kind": "측정정합성",
    "owner": "엔지니어",
    "affects_model": False,
    "metric": "robust_auc_se",
    "title": (
        "[적립 가속] 창 보수 가드(−7일)가 버리는 5날짜를 되찾는다 — 23→28날짜로 SE 0.0070, "
        "3σ 판정 도달을 12/02 에서 11/24 로 앞당길 수 있는가"
    ),
    "hypothesis": (
        "CG370 의 창은 2026-11-05 에서 끝났지만 market_data 는 11-19 까지 채워져 있었다 — 손실은 "
        "가드 7일 + purge 5거래일 = 12거래일분이다. purge 가 이미 '라벨 미완성 날짜'를 잘라내므로 "
        "가드를 0으로 낮춰도 미래정보 누수는 생기지 않는다(라벨은 h=5 뒤 종가까지만 쓴다). "
        "따라서 reserve=0 창은 같은 프로토콜의 5날짜 추가일 뿐이고, 그 5날짜의 날짜별 AUC 수준이 "
        "기존 23날짜와 다르지 않다면 적립 목표(31날짜)를 6거래일 앞당길 수 있다."
    ),
    "command": (
        "docker exec -e UNIVERSE_ASOF_DATE=2026-10-01 stock_xgboost_ml sh -c 'cd /app && "
        "OMP_NUM_THREADS=4 timeout 2700 python -u scripts/champion_robust_eval.py "
        "--model-dir app/models/champion --stocks 200 --folds 1 --dates-per-fold 30 "
        "--asof-date 2026-11-19 --window-reserve-days 0 --train-end 2026-10-01 --oos-start 2026-10-02 "
        "--universe training --universe-seed 0 --dump-preds /app/reports/overnight/cg371_narrow200.jsonl "
        "--dump-tag cg371_narrow --out /app/reports/overnight/cg371_narrow200.json'"
    ),
    "counterfactual": (
        "CG370(같은 동결 유니버스·같은 앵커·가드 7일): 23날짜(10-02~11-05) · 견고 AUC 0.5389 · "
        "날짜 SD 0.0371 · SE 0.0077 · 3σ 판정 필요 31날짜(남은 8날짜 = 12/02)"
    ),
    "success": (
        "① rc=0 · 28날짜 채점(11-06~11-12 포함) ② 날짜 SD 가 0.0371 대비 크게 다르지 않다(|ΔSD| ≤ 0.010) "
        "③ 추가 5날짜의 날짜별 AUC 평균이 기존 23날짜와 |Δ| ≤ 0.020(사전문턱) → 가드 손실은 수준 편향이 아니다 "
        "④ SE ≤ 0.0071 · 남은 필요 날짜 3개 → '시리즈 기본값 reserve=2~3 전환' 리뷰보드 안건 상정. "
        "Δ가 문턱을 넘으면 원인(최근일 데이터 신선도·원천 지연)을 분해하고 기본값은 유지"
    ),
    "expected": (
        "3σ 판정 도달을 6거래일(12/02 → 11/24) 앞당긴다. 적립은 지금 21사이클 무개선을 '해상'으로 바꿀 "
        "유일한 계측기인데, 가드 하나 때문에 표본의 1/5(5/28)을 매일 버리고 있다."
    ),
    "rationale": (
        "CG370 이 SE 0.0077 까지 내려왔지만 남은 8날짜를 기다리는 동안 판정은 계속 미해상이다. "
        "가드는 원래 '최근일 적재 지연' 방어용인데 원천 신선도 WARN 은 sns·재무·매크로에만 걸려 있고 "
        "가격(market_data)은 11-19 까지 정상이다 — 즉 방어 대상이 아닌 날짜까지 잘라내고 있다."
    ),
    "est_minutes": 25,
    "created_at": TS,
    "attempts": 0,
    "needs_approval": False,
    "note": (
        "코드 변경 완료(2026-11-20 07:1x): champion_robust_eval 에 --window-reserve-days 추가"
        "(기본 7 = 현행 비트 동일, summary 에 window_reserve_days·protocol 접미 기록). "
        "기본값 OFF 회귀 확인: reserve=7 → asc 28개·usable 23개(10-02~11-05) = CG370 과 동일 창. "
        "스모크 실행 rc=0(40종목 1날짜, reserve=0 → window [10-02, 11-12])."
    ),
}

items = [i for i in d["items"] if i.get("id") != "CG371"] + [item]
d["items"] = items
d["updated_at"] = TS
d["updated"] = TS
json.dump(d, open(P, "w"), ensure_ascii=False, indent=1)
print("wrote CG371 · items:", len(items))

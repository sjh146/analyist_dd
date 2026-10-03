import json, io

P = "docs/QUANT_MODEL_BACKLOG.json"
d = json.load(open(P, encoding="utf-8"))
items = d if isinstance(d, list) else d.get("items", [])
assert not any(i.get("id") == "CG76" for i in items), "CG76 이미 존재"

item = {
    "id": "CG76",
    "title": "조건부(비영) edge 선별 — 부활·희소 피처가 선별에 진입조차 못 하는 문제 (선별 표본 도메인 축)",
    "status": "pending",
    "priority": 2,
    "affects_model": True,
    "metric": "wf_sweep_summary",
    "arm": "CN_00_30 .. CN_120_150 (게이트 OFF + 조건부(비영) edge top30)",
    "counterfactual": "LU_00_30 .. LU_120_150 (같은 런·같은 구간·같은 q · 풀링 edge top30)",
    "pairs": [
        ["CN_00_30", "LU_00_30"],
        ["CN_30_60", "LU_30_60"],
        ["CN_60_90", "LU_60_90"],
        ["CN_90_120", "LU_90_120"],
        ["CN_120_150", "LU_120_150"],
    ],
    "baseline": {"value": None, "source": "LU_* (같은 런·같은 panel_prod200·같은 서로소 30종목 구간)"},
    "hypothesis": (
        "부활 피처가 모델에 들어가지 못하는 원인은 '데이터가 없어서'가 아니라 **선별 규칙**이다. "
        "`edge_of` 는 폴드 학습구간을 풀링한 순위 AUC 라, 0 행이 동점(mid-rank)으로 처리돼 희소 피처의 "
        "신호가 '비영 비율²' 만큼 **희석**된다(자체점검 실측: 비영 1%(216행)·조건부 edge 0.5000 인 피처의 "
        "풀링 edge = 0.0050, 희석계수 0.010 = 1−0.99²). 그래서 커버리지를 살려도 top30 에 못 들어간다"
        "(EV1 이벤트 17종 진입 0 → EV_all == EV_none 비트 동일 · CG67 disclosure_count_5d 부활 후 AUC "
        "비트 불변). 비영 행에서만 edge 를 계산하면 희소 피처가 dense 피처와 경쟁해 진입한다 — "
        "그 진입이 OOS AUC 를 올리는가."
    ),
    "evidence": (
        "① 자체점검 `scripts/_cnd_select_test.py` 전부 PASS(6항목): 비영 1% 피처가 cnd30 진입·top30 미진입 · "
        "dense 만인 패널에서는 cnd25 == top25(교락 없음) · 초희소(<min_nz) 제외 · desc 에 겹침 수 보고. "
        "② panel_prod200 실측(읽기 전용): 213피처 중 전행 0 = 24개(short_interest_ratio·days_to_cover·"
        "institution_ownership_pct·credit/margin/etf·sentiment 7종·news_count 2종·sector 2종·theme·"
        "value_ncav·event_patent/recall·authenticity_avg·positive/negative_ratio) · disclosure_count_5d "
        "비영 33.6% · event_market_liquidity_5d 1.37% · short_selling_ratio 1.51%. "
        "③ 원천 조사(DB 실측): krx_short_selling 16,977행·205종목·89일(2026-05-26~10-02)·short_ratio 비영 97.9% "
        "/ ownership 1,828행·6일·for 96.6% / supply_market_features 108만행이나 short_interest_ratio·"
        "days_to_cover·institution_ownership_pct 전부 0.0%(원천 부재) → 죽은 24개 중 다수는 수집 문제, "
        "나머지는 선별 문제다."
    ),
    "method": (
        "wf_wave.subset 에 `select=\"cnd{k}\"` (비영 행에서만 edge · 비영 < min_nz=max(50,0.5%행) 은 후보 제외) "
        "신설. wf_label_sweep CONFIGS 에 CN_*/LU_* 10 config(5 서로소 30종목 구간 × 조건부/풀링) 추가. "
        "판정은 구동기 judge_per 의 pairs 분기(짝 Δ 평균·양(+) 구간 수). 게이트 OFF 경로 고정"
        "(게이트 ON 은 CORE_FEATURES 48 밖 피처가 후보에 없다)."
    ),
    "command": (
        "docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 3600 python -u "
        "scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_prod200.npz --folds 5 --seeds 5 "
        "--only CN_00_30,CN_30_60,CN_60_90,CN_90_120,CN_120_150,LU_00_30,LU_30_60,LU_60_90,LU_90_120,LU_120_150 "
        "--out /app/reports/overnight/cg76_sweep.jsonl --summary-out /app/reports/overnight/cg76_summary.json'"
    ),
    "check": "docker exec -e PYTHONPATH=/app stock_xgboost_ml python /app/scripts/_cnd_select_test.py",
    "check_target": {"op": ">=", "value": 1},
    "success": (
        "구간 짝 Δ(CN − LU) 평균 ≥ +0.02 그리고 양(+) 구간 ≥ 4/5 → 신호(→ 후속: 부활 피처를 조건부 선별로 "
        "모델에 넣는 것을 승격 후보 축으로 검토 + 승인 항목: 게이트 CORE_FEATURES 재큐레이션). "
        "미달이면 '조건부 선별도 성능 축이 아니다'로 축을 닫는다 — 단 같은 런 로그에서 희소 피처가 "
        "실제로 선별에 진입했는지(desc 의 '겹침 n/30' 과 sel_desc)를 반드시 함께 기록해, "
        "'Δ0 = 효과 없음'과 'Δ0 = 진입 자체가 안 됨'을 구분한다."
    ),
    "expected": (
        "미지 — 희소 피처(disclosure_count_5d 33.6% · event_* ≤1.37%)가 실제로 진입하면 현 피처풀"
        "(무정보 120/213)에 없던 시간가변 후보다. 다만 event_* 는 비영 ≤1.37%(≤750행)라 min_nz(274)를 "
        "겨우 넘겨 추정이 노이즈일 수 있다."
    ),
    "cost": "250 cell · 실측 2.8 s/cell(CG74 11.7분) → 약 12~20분. 컨테이너 timeout 3600.",
    "est_minutes": 25,
    "note": (
        "2026-10-03 17:0x 신설(주말 자율 세션, 규칙 4 = 실행 가능 pending 0). 계기: 모델 측 축은 포화로 "
        "판정됐지만, EV1·CG67 이 '부활 피처가 선별에 진입조차 못 했다'를 실측으로 남겼다 — 그 진입을 "
        "막는 것이 선별 표본 도메인이라는 가설은 미측정이다. CG71(선별 *통계량*: IC vs 풀링 edge)·"
        "CG74(drop_dup)와 달리 이건 **선별 표본 도메인**(전 행 vs 비영 행)을 바꾸는 것이고, dense "
        "피처에는 작용하지 않는다(비영=전 행이면 값 동일 → 교락 없음, [1] 로 증명). "
        "⚠ CG73 과 구분: CG73 은 '원천을 늘려야 한다'(수집·승인), CG76 은 '원천이 있는 것도 선별에 "
        "안 들어간다'(내 소유 코드) — 둘은 직교하며 CG76 이 먼저 닫혀야 CG73 의 기대효과가 성립한다."
    ),
    "result": None,
    "attempts": 0,
}

items.append(item)
if isinstance(d, list):
    d = items
json.dump(d, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("CG76 등록 완료 · items:", len(items))

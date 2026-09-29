#!/usr/bin/env python3
"""백로그에 CG29(이벤트 피처군 강제투입, pending)와 CG30(시드 앙상블 축 종결, done)을 추가한다."""
import json
import sys

P = "/home/jhshi/analyist_dd/docs/QUANT_MODEL_BACKLOG.json"
d = json.load(open(P))
items = d["items"]
have = {i.get("id") for i in items}

cg29 = {
    "id": "CG29",
    "title": "이벤트(공시) 피처군 강제투입 — 낡은 이벤트 컬럼을 백필본으로 교체(evfix 패널) 후 게이트 ON 3-arm",
    "status": "pending",
    "priority": 2,
    "affects_model": True,
    "baseline": {"value": 0.5363,
                 "source": "CG28 같은 런 게이트 ON 대조군 CO_core30_h5 (panel_420_asofpatch·5폴드×5시드)"},
    "arm": "CO_core30_ev10_h5",
    "counterfactual": "CO_core30_h5 (같은 런 게이트 ON 대조군)",
    "hypothesis": "게이트 ON 경로에서 조정 축은 CG27(천장 Δ+0.018)·CG28(기존 피처 재배치 Δ+0.0005)로 닫혔다. "
                  "남은 것은 '패널에 있으나 선별에 못 들어가는 신규 정보'인데 EV1(게이트 OFF A/B)은 event_* 36개가 "
                  "top30 에 0개 들어가 두 arm 이 비트 동일했다 → 효과 없음이 아니라 후보 아님이다. "
                  "게다가 기본 패널의 이벤트 컬럼은 **2026-04 이전이 전량 0** 이라 워크포워드 폴드 1~2 에서는 "
                  "std 필터로 전량 탈락한다(측정 자체가 불가). 백필본으로 교체한 패널에서 강제투입하면 "
                  "이벤트 발생일 조건부 정보가 있는지 처음으로 측정된다.",
    "evidence": "① 기본 패널 event_exec_change_5d 월별 nonzero: 2025-07~2026-03 전부 0.0% → 2026-04~ 1.3~3.2% "
                "(scripts/_cg29_diag.py) → 5폴드 fold1(≤2025-10-24)·fold2(≤2026-01-15) 학습창에서 16/16 컬럼 분산 0. "
                "② 스모크 실측(2026-09-29 16:07): CO_core30_e16_h5(기본 패널) → RuntimeError '파생 피처가 필터를 "
                "통과하지 못했다 — 측정 무효' 로 실패(원인이 위 커버리지 결함). "
                "③ 백필본(ev 패널 뒤 17컬럼) 커버리지는 전 구간(event_exec_change 1.5~8.2%, disclosure_count 2~25%) "
                "(scripts/_cg29_diag2.py). ④ 교체 패널 산출: scripts/patch_panel_events.py — 17컬럼 교체, "
                "교체 외 컬럼 해시 동일(non_patched_identical_hash=true), 행·날짜·종목 정렬 일치. "
                "⑤ 상방 기제: 단변량 pooled AUC 0.496~0.507(무정보)이지만 이벤트 발생일 IC t 는 ±2 이상 "
                "(event_patent −2.71·partnership −2.58·contract −2.49·realized +2.24). "
                "⑥ 스모크(evfix·2폴드×1시드): ev10 0.5168(게이트통과 9~10/10) · pv10 0.5165(8~10/10) — 양 arm 정상 완주.",
    "method": "같은 패널·같은 행·같은 폴드 3-arm(5폴드×5시드): ①CO_core30_h5 대조군 ②CO_core30_ev10_h5 = 백필 이벤트 "
              "10개(전체 nonzero ≥0.5%)를 top30 선별 후 강제 덧붙임 ③CO_core30_pv10_h5 = 비-event 무정보 10개 같은 방식"
              "(용량 교란 통제). 판정 = (ev10−core30) ≥ +0.02 **이고** (ev10−pv10) ≥ +0.02 이며 폴드 짝 부호 ≥4/5.",
    "command": "docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 1800 python -u scripts/wf_label_sweep.py "
               "--panel /app/app/models/wf/panel_420_asofpatch_evfix.npz --folds 5 --seeds 5 "
               "--only CO_core30_h5,CO_core30_ev10_h5,CO_core30_pv10_h5'",
    "check": "python3 scripts/model_engineer_cycle.py --status",
    "check_target": {"op": ">=", "value": 1},
    "success": "(ev10 − core30) ≥ +0.02 이고 (ev10 − pv10) ≥ +0.02, 폴드 짝 부호 ≥4/5 → 데이터 축 후보 유지(승격은 별도). "
               "미달이면 '패널 안 신규 정보' 축 전체를 닫고 남은 레버를 U3(창)·외부 수집으로 확정한다.",
    "expected": "미지 — 단변량 edge 는 0(AUC 0.50), 기제는 이벤트일 조건부 정보뿐이라 문턱 미달 쪽에 무게.",
    "cost": "3 config × 5폴드 × 5시드 = 75 cell (CG27 실측 1.8s/cell → 약 5분, timeout 1800s)",
    "est_minutes": 8,
    "metric": "wf_sweep_summary",
    "caution": "evfix 패널은 scripts/patch_panel_events.py 산출물이다. **대조군 검증**: evfix 패널의 CO_core30_h5 는 "
               "기본 패널과 같은 0.5363 이 나와야 정상(이벤트 컬럼은 top30 선별에 안 들어간다) — 값이 다르면 패치 결함을 "
               "먼저 의심하라. 폴드별 gate_add 통과 개수는 로그에 남는다(예: fold1 ev 9/10 — 이벤트가 희소한 구간).",
    "note": "2026-09-29 16:0x 신설. 계기: 16:00 틱이 '실행 가능한 pending 없음'(U3 은 ETA 가드로 차단)을 내서 규칙 4에 "
            "따라 다음 가설을 설계. 조정 축이 닫힌 뒤 남은 두 축(데이터·창) 중, 창은 U3(밤 런처)가 담당하므로 여기서는 "
            "'패널 안 신규 정보'를 닫는다. 부수 성과: **기본 패널의 이벤트 컬럼이 2026-04 이전 전량 0 인 결함**을 "
            "발견하고 백필본으로 교체한 evfix 패널을 만들었다(다른 컬럼 해시 동일) — 이 결함은 과거 모든 이벤트 관련 "
            "측정(EV1 등)에 영향. 리서처 확인 필요(공시 이벤트 조인의 커버리지 구간).",
}
cg30 = {
    "id": "CG30",
    "title": "시드 앙상블 축 종결 — 판정 지표(폴드별 시드 AUC 평균) vs 시드평균 확률 AUC 갭 실측",
    "status": "done",
    "priority": 9,
    "affects_model": True,
    "hypothesis": "판정 지표는 '폴드별 시드 AUC 의 평균'인데 배포되는 모델은 시드 앙상블 예측이다. 이 갭이 크면 "
                  "지금까지의 '무개선' 판정이 배포 예측 성능을 과소평가한 것이므로 프로토콜을 바꿔야 한다.",
    "method": "추가 학습 없이 기존 런의 per-fold `ens_pooled_auc`(=시드 평균 확률의 AUC, wf_label_sweep 이 이미 기록) "
              "와 `folds[].mean` 을 비교. 스크립트: scripts/seed_ensemble_gap.py (0비용).",
    "result": {"n_runs": 112, "gap_mean": 0.0009, "gap_median": 0.0010, "gap_min": -0.0039, "gap_max": 0.0057,
               "verdict": "축 종결 — 시드 앙상블 이득은 최대 +0.0057 로 사전문턱 +0.02 의 1/3. 지표 과소평가 아님.",
               "artifact": "reports/overnight/seed_ensemble_gap.json"},
    "note": "2026-09-29 16:0x 측정(신설 즉시 종결). 앙상블 축은 기록상 0회 시험이었고 '지표가 배포 예측을 과소평가한다'는 "
            "가설을 0비용으로 반증했다 — 다시 열지 말 것. (모델종류 앙상블 가중은 ens.equal_weights/skip 노브로 이미 "
            "시험 가능하나, 시드 앙상블 갭이 사실상 0 이라는 것은 다양성 기반 이득 자체가 작다는 뜻이다.)",
    "counterfactual": "판정 지표 자체(wf_label_sweep folds[].mean)",
    "metric": "wf_sweep_summary",
}

added = []
for it in (cg29, cg30):
    if it["id"] not in have:
        items.append(it)
        added.append(it["id"])
d["updated_at"] = "2026-09-29T16:20:00+09:00"
json.dump(d, open(P, "w"), ensure_ascii=False, indent=1)
print("added:", added, "| total items:", len(items))
print("pending now:", [i["id"] for i in items if i.get("status") == "pending"])

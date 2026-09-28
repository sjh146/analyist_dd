#!/usr/bin/env python3
"""CG14/CG15 백로그 항목 추가 (2026-09-28, CG13 실측 후속). 일회성 스크립트."""
import json
from pathlib import Path

P = Path(__file__).resolve().parent.parent / "docs" / "QUANT_MODEL_BACKLOG.json"
d = json.loads(P.read_text(encoding="utf-8"))
items = d["items"]
ids = {i.get("id") for i in items}

CMD14 = ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 timeout 3600 "
         "python -u scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_150u.npz "
         "--folds 5 --seeds 5 --only "
         "RSs_00_30,RSs_30_60,RSs_60_90,RSs_90_120,RSs_120_150,"
         "US_00_30,US_30_60,US_60_90,US_90_120,US_120_150'")

CMD15 = ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 timeout 3600 "
         "python -u scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_150u.npz "
         "--folds 5 --seeds 5 --only "
         "REl_00_30,REl_30_60,REl_60_90,REl_90_120,REl_120_150,REl_all150'")

cg14 = {
    "id": "CG14",
    "title": ("우승 후보(게이트 ON+rank+스무딩+depth1)의 구간 짝(paired) 재판정 — "
              "유니버스 교체 잡음 Δ0.0287 을 상쇄하는 프로토콜에서 +0.02 가 살아남는가"),
    "status": "pending",
    "priority": 1,
    "affects_model": True,
    "baseline": {"value": 0.5406,
                 "source": "등록 기준선(49종목·5폴드×3시드·281거래일) — 이 실험은 paired Δ 로 판정"},
    "hypothesis": ("27사이클의 유일한 승격 후보(CO_rank_smooth_d1_h5, CG4 Δ+0.0227 / CG8 Δ+0.0180)는 "
                   "유니버스 교체만으로 만들어지는 잡음 밴드(0.0287) 안에 있다. 같은 30종목 구간 **안에서** "
                   "arm−대조군 짝 Δ 를 5개 구하면 구간 간 교체 효과가 상쇄되어 후보의 실제 효과가 드러난다."),
    "evidence": ("CG13 실측(5폴드×5시드, panel_150u, 게이트 ON 대조군): 같은 크기 30종목 서로소 5구간 "
                 "0.5204 → 0.5112 → 0.5034 → 0.4998 → 0.4917 = Δ0.0287 (사전문턱 +0.02 초과). "
                 "CG11/CG12 에서 같은 후보가 150종목 −0.0109 / 49종목 −0.0287 로 부호 반전."),
    "method": ("panel_150u 를 서로소 5구간([0:30)…[120:150))으로 자르고, 구간마다 arm(RSs_*, "
               "게이트 ON+rank+스무딩+depth1)과 대조군(US_*, 게이트 ON 평범)을 **같은 런**에서 측정한다. "
               "판정은 5개 짝 Δ 의 평균 + 양(+) 구간 수(구동기 judge_per 의 pairs 분기)."),
    "command": CMD14,
    "pairs": [["RSs_00_30", "US_00_30"], ["RSs_30_60", "US_30_60"], ["RSs_60_90", "US_60_90"],
              ["RSs_90_120", "US_90_120"], ["RSs_120_150", "US_120_150"]],
    "arm": "RSs_00_30",
    "counterfactual": "US_00_30",
    "check": "python3 scripts/model_engineer_cycle.py --status",
    "check_target": {"op": ">=", "value": 1},
    "metric": "wf_sweep_summary",
    "est_minutes": 15,
    "cost": "약 12분 (CG13 6 config=5.7분 실측 → 10 config)",
    "success": ("짝 Δ 평균 ≥ +0.02 **그리고** 양(+) 구간 ≥ 4/5 → 후보 생존(CG9 생산 경로 구현 근거). "
                "짝 Δ 평균 < +0.02 → 후보 폐기(27사이클 무개선의 원인 = 유니버스 잡음, 축 종료)."),
    "expected": "미지 — 어느 쪽이든 27사이클 무개선 카운터의 해석이 확정된다",
    "note": ("2026-09-28 17:1x 신설(CG13 후속·규칙 4). 구동기 judge_per 에 pairs 분기 신설"
             "(검증: scripts/_paired_judge_test.py 6/6 PASS)."),
}
cg15 = {
    "id": "CG15",
    "title": "유동성 단조 추세의 출처 분해 — 라벨을 시장상대(중앙값 초과)로 바꿔도 5구간 추세가 남는가",
    "status": "pending",
    "priority": 2,
    "affects_model": True,
    "baseline": {"value": 0.5406, "source": "등록 기준선 — 이 실험의 판정에는 쓰지 않는다"},
    "hypothesis": ("CG13 의 게이트 ON 대조군은 패널 순서(유동성 정렬)대로 0.5204 → 0.4917 로 거의 단조 "
                   "감소했다(Δ0.0287). 종목 수가 30 으로 고정이므로 크기 효과는 아니다. 두 갈래: "
                   "①유동성 상위 종목이 실제로 더 예측 가능하다 ②분위 라벨(q=0.30)이 30종목 부분집합에서 "
                   "날짜별 구성이 달라져 생기는 표본 효과."),
    "evidence": "CG13 실측(2026-09-28, 5.7분): US_00_30 0.5204 · US_30_60 0.5112 · US_60_90 0.5034 · US_90_120 0.4998 · US_120_150 0.4917 (폴드 std 0.020~0.040).",
    "method": ("같은 서로소 5구간·같은 게이트 ON·같은 폴드에서 라벨만 시장상대(relative: 당일 횡단면 "
               "중앙값 초과=1)로 바꿔 재측정한다(REl_*). 구간 간 총폭(max−min)과 단조성을 본다."),
    "command": CMD15,
    "pairs": [["REl_00_30", "REl_30_60"], ["REl_30_60", "REl_60_90"], ["REl_60_90", "REl_90_120"],
              ["REl_90_120", "REl_120_150"]],
    "arm": "REl_00_30",
    "counterfactual": "REl_120_150",
    "check": "python3 scripts/model_engineer_cycle.py --status",
    "check_target": {"op": ">=", "value": 1},
    "metric": "wf_sweep_summary",
    "est_minutes": 10,
    "cost": "약 6분 (6 config)",
    "success": ("구간 간 총폭 ≥ 0.02 → 분위 라벨 구성 효과가 아니다(유동성 예측성·①) → 유니버스 선택이 "
                "성능 레버가 된다. 총폭 < 0.02 → ①은 기각, CG13 의 추세는 라벨 구성 효과(②) → "
                "유동성 축 종료."),
    "expected": "미지",
    "note": ("2026-09-28 17:1x 신설. 구동기 verdict(끝점 짝 Δ)는 참고값이고, 실제 판정은 5구간 평균의 "
             "총폭·단조성으로 한다(구간 간 비교는 CG13 이 측정한 잡음 밴드 안에 있다는 점을 명시)."),
}
for it in (cg14, cg15):
    if it["id"] in ids:
        print("이미 존재:", it["id"])
    else:
        items.append(it)
        print("추가:", it["id"])
d["updated_at"] = "2026-09-28T17:20:00+09:00"
P.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
print("총 항목:", len(items))

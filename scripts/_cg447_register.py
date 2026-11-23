#!/usr/bin/env python3
"""CG447 등록 — 전방 성적표 판본 혼합 수리(2026-11-23 야간 자율 틱)."""
import json, datetime

P = "docs/QUANT_MODEL_BACKLOG.json"
b = json.load(open(P, encoding="utf-8"))
items = b["items"]
ids = {i.get("id") for i in items}
if "CG447" in ids:
    print("already present")
    raise SystemExit(0)

item = {
    "id": "CG447",
    "kind": "experiment",
    "owner": "engineer",
    "affects_model": False,
    "priority": 2,
    "status": "done",
    "metric": "measurement_integrity",
    "title": ("[측정정합성·배포경로] 전방 성적표 헤드라인이 판본혼합(v1.0 9날짜 + champ 8날짜)이라 "
              "배포 챔피언 성과를 **반대 부호**로 보고한다 — champ 단독 슬라이스를 요약에 상시 병기"),
    "hypothesis": ("틱이 매시간 출력하는 '전방 성적표 top10 −0.36%(h1) / −3.99%(h5)' 는 배포 모델의 성과가 아니라 "
                   "구판본 v1.0(2026-09-22~10-08)과 champ(2026-10-09~) 를 한 풀에 넣은 값이다. "
                   "판본별로 자르면 부호가 뒤집힐 수 있다 → 판정·보고 전에 판본 청정 슬라이스를 의무화한다."),
    "counterfactual": "혼합 헤드라인(현행 요약) — 구판본 9날짜가 값을 지배",
    "command": "python3 scripts/_forward_scorecard_money_test.py",
    "evidence": ("실측 2026-11-23 06:36 forward_scorecard.json (ml_predictions 124,738행 · champ 61,302행 16날짜 · v1.0 63,436행 15날짜): "
                 "h1 혼합 top10 −0.3599% vs 전체 +0.0779% 인데, by_version 으로 자르면 "
                 "champ(8날짜) topk +0.597% vs 풀 −0.272% = Δ+0.869%p(t 0.90 · 분할 [+0.98/+0.75] · 양수세션 4/8) 이고 "
                 "v1.0(9날짜) topk −0.883% vs 풀 −0.252% = Δ−0.632%p(t −0.81) → **혼합 헤드라인의 음수는 전부 구판본 몫**이다. "
                 "h5 는 champ 4날짜 Δ−0.770%p(t −0.49 · 분할 [+1.86/−3.40]) = 판정불가."),
    "success": ("① 틱 요약이 판본이 2개 이상이면 '현행[판본] topk vs 풀 Δ(t, 분할, 날짜수)' 를 항상 병기한다 "
                "② date_versions 가 없으면 추정하지 않고 생략한다 ③ 자체점검 32/32 PASS(기존 28 + 신규 4)."),
    "result": {
        "verdict": "측정수리 완료(판정 아님)",
        "champ_h1": {"delta_pct": 0.869, "t": 0.90, "split": [0.98, 0.75], "n_dates": 8,
                     "topk_net_pct": 0.597, "pool_net_pct": -0.272},
        "v1_0_h1": {"delta_pct": -0.632, "t": -0.81, "split": [-0.16, -1.01], "n_dates": 9},
        "champ_h5": {"delta_pct": -0.770, "t": -0.49, "split": [1.86, -3.40], "n_dates": 4},
        "note": ("champ h1 Δ+0.87%p 는 t 0.90·8날짜 → 사전문턱(t≥2, n_dates≥20) 미달. "
                 "이 값은 승격 근거가 아니며, 표본이 쌓이면(현 속도 +1날짜/거래일 → 약 2026-12-04) 재판정한다(CG379 표본 축)."),
        "code": "scripts/model_engineer_cycle.py::_current_version + _forward_summary_line",
        "tests": "scripts/_forward_scorecard_money_test.py 32/32 PASS · _forward_refresh_test.py 16/16 · _forward_scorecard_metric_test.py ALL PASS",
    },
    "est_minutes": 20,
    "created": "2026-11-23T17:20:00+09:00",
    "reported": True,
}

items.append(item)

# CG379 는 '전방 표본 부족' 항목 — 새 판본 청정 수치를 note 에 반영(중복 항목 신설 금지)
for i in items:
    if i.get("id") == "CG379":
        i["note"] = (i.get("note", "") +
                     " | 2026-11-23 17:2x CG447 실측 반영: 판본 청정으로 자르면 h1 champ 8날짜 Δ+0.869%p(t 0.90·분할 both+) · "
                     "h5 champ 4날짜 Δ−0.770%p(t −0.49) → **h1 은 표본만 늘면 판정 가능**(+1날짜/거래일, n_dates≥20 은 약 12-04), "
                     "h5 는 아직 판정불가. 틱 요약에 현행 판본 슬라이스가 상시 병기되므로 혼합 헤드라인을 성과로 읽지 말 것.")
        break

b["updated_at"] = datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()
json.dump(b, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("added CG447, items", len(items), "updated_at", b["updated_at"])

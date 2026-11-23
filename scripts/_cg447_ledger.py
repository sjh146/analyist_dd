#!/usr/bin/env python3
"""CG447 원장 기록 — 전방 성적표 판본 청정 슬라이스 수리(2026-11-23)."""
import json, datetime

P = "data/reports/model_engineer_ledger.jsonl"
ts = datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()
entry = {
    "ts": ts,
    "id": "CG447",
    "title": ("[측정정합성·배포경로] 전방 성적표 판본혼합이 배포 챔피언 성과를 반대 부호로 보고 — "
              "champ 단독 슬라이스를 틱 요약에 상시 병기"),
    "rc": 0,
    "elapsed_min": 18.0,
    "log": "scripts/_forward_scorecard_money_test.py (32/32 PASS)",
    "metric": "measurement_integrity",
    "parsed": {
        "champ_h1": {"topk_net_pct": 0.597, "pool_net_pct": -0.272, "delta_pct": 0.869,
                     "t": 0.90, "split": [0.98, 0.75], "n_dates": 8},
        "v1_0_h1": {"topk_net_pct": -0.883, "pool_net_pct": -0.252, "delta_pct": -0.632,
                    "t": -0.81, "split": [-0.16, -1.01], "n_dates": 9},
        "champ_h5": {"delta_pct": -0.770, "t": -0.49, "n_dates": 4},
        "mixed_h1_top10_pct": -0.3599,
        "code": "scripts/model_engineer_cycle.py::_current_version/_forward_summary_line",
    },
    "verdict": "측정수리 완료 — 혼합 헤드라인(h1 top10 −0.36%)의 음수는 전부 구판본 v1.0 몫, champ 단독은 Δ+0.87%p(t 0.90·8날짜)",
    "detail": ("실제 판정 아님: champ h1 Δ+0.869%p 는 t 0.90·n_dates 8 로 사전문턱(t≥2·n_dates≥20) 미달. "
               "h5 champ 는 4날짜 Δ−0.770%p = 판정불가. 표본 +1날짜/거래일 → 약 2026-12-04 재판정(CG379)."),
    "reported": True,
    "reported_at": ts,
}

with open(P, "a", encoding="utf-8") as f:
    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
print("ledger appended", ts)

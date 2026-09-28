#!/usr/bin/env python3
"""CG16 백로그 추가 + CG13/CG14/CG15 원장 reported 처리 (2026-09-28, 일회성)."""
import json
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ / "scripts"))

P = PROJ / "docs" / "QUANT_MODEL_BACKLOG.json"
d = json.loads(P.read_text(encoding="utf-8"))
ids = {i.get("id") for i in d["items"]}

CMD = ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 timeout 3600 "
       "python -u scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_150u.npz "
       "--folds 5 --seeds 5 --only "
       "DSs_00_30,DSs_30_60,DSs_60_90,DSs_90_120,DSs_120_150,"
       "US_00_30,US_30_60,US_60_90,US_90_120,US_120_150'")

cg16 = {
    "id": "CG16",
    "title": "HP 축 마지막 후보(depth1·lr0.05)의 구간 짝 재판정 — CG8 의 Δ+0.0180 도 유니버스 잡음인가",
    "status": "pending",
    "priority": 1,
    "affects_model": True,
    "baseline": {"value": 0.5406,
                 "source": "등록 기준선(49종목) — 이 실험은 구간 짝 Δ 로 판정"},
    "hypothesis": ("CG14 에서 결합 후보(rank+스무딩+depth1)가 구간 짝 Δ −0.0057(1/5 구간 양(+)) 로 "
                   "소멸했으므로, 그 유일한 성분인 depth1·lr0.05 단독(CG8 Δ+0.0180, 문턱 미달)도 "
                   "유니버스 교체 잡음일 가능성이 크다. 짝 프로토콜로 검정하면 남은 레버가 무엇인지 "
                   "확정된다(HP 축 종료 vs 생존)."),
    "evidence": ("CG8 실측(게이트 ON): CO_d1_h5 0.5545±0.0458(Δ+0.0180) · CO_rank_smooth_d1_h5 "
                 "0.5592±0.0075(Δ+0.0227). CG14 실측(2026-09-28 17:19, 4.3분): 구간 짝 Δ 평균 −0.0057 "
                 "· 양(+) 1/5 ([-0.0431,-0.0086,-0.0097,-0.0060,+0.0387]) · 폴드 짝 10/25. "
                 "CG13: 같은 크기 서로소 5구간 총폭 0.0287 > 사전문턱 0.02."),
    "method": ("panel_150u 서로소 5구간에서 arm(DSs_*: 게이트 ON + depth1·lr0.05)과 대조군(US_*: "
               "게이트 ON 평범)을 같은 런에서 측정하고 구간별 짝 Δ 를 낸다(구동기 pairs 분기)."),
    "command": CMD,
    "pairs": [["DSs_00_30", "US_00_30"], ["DSs_30_60", "US_30_60"], ["DSs_60_90", "US_60_90"],
              ["DSs_90_120", "US_90_120"], ["DSs_120_150", "US_120_150"]],
    "arm": "DSs_00_30",
    "counterfactual": "US_00_30",
    "check": "python3 scripts/model_engineer_cycle.py --status",
    "check_target": {"op": ">=", "value": 1},
    "metric": "wf_sweep_summary",
    "est_minutes": 12,
    "cost": "약 6~8분 (10 config · CG14 10 config=4.3분 실측)",
    "success": ("짝 Δ 평균 ≥ +0.02 및 양(+) ≥ 4/5 → depth1 은 실제 레버(CG9 생산 경로 구현 근거). "
                "미달 → HP 축 종료 → 남는 레버는 데이터 축(U3 창·신규 피처)뿐임을 기록하고 "
                "스코어보드 '새 레버 필요'(28사이클) 승인 요청으로 올린다."),
    "expected": "미지",
    "note": ("2026-09-28 17:3x 신설(CG14/CG15 후속). US_* 대조군은 같은 런에 포함시켜 짝을 만든다"
             "(CG13 의 구간 효과를 상쇄). CG15 실측으로 유동성 추세는 분위 라벨 구성 효과로 판정"
             "(시장상대 라벨 총폭 0.0192·비단조) → 유동성 축 종료."),
}
if cg16["id"] in ids:
    print("이미 존재: CG16")
else:
    d["items"].append(cg16)
    d["updated_at"] = "2026-09-28T17:35:00+09:00"
    P.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("추가: CG16 · 총", len(d["items"]))

# ── 원장 reported 표시: 이번 보고에서 다루는 CG13/CG14/CG15 를 '보고됨'으로 ──
import model_engineer_cycle as m  # noqa: E402
led = m.load_ledger()
seen = []
for r in led:
    if r.get("id") in ("CG13", "CG14", "CG15") and not r.get("reported"):
        r["reported"] = True
        seen.append(f"{r['id']}({r['verdict']})")
m._rewrite_ledger(led)
print("reported 처리:", ", ".join(seen) or "없음")
print("미보고 잔여:", [r["id"] for r in m.load_ledger() if not r.get("reported")])

#!/usr/bin/env python3
"""백로그에 SEL1 결론 + HP4(depth1 안정성 재검증) 추가 (2026-09-26 틱 2 마무리).

SEL1(5폴드×5시드): top10 0.5364 · top15 0.5343 · top20 0.5345 vs top30 0.5414
  → 피처 축소는 해롭다(Δ−0.005~−0.007). 선별 크기 축 닫음.
HP4: 유일하게 재현되는 방향(depth1 ≤2 우위, Δ+0.0086)이 시드를 2배로 늘려도 유지되는지 확인.
  유지되면 '챔피언 레시피 후보'로 승격 논의(별도 승인) 근거가 된다.
"""
import json
import os
import shutil
import time

P = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "..", "docs/QUANT_MODEL_BACKLOG.json"))
with open(P, encoding="utf-8") as f:
    b = json.load(f)
by = {i["id"]: i for i in b["items"]}

if "SEL1" in by:
    by["SEL1"]["status"] = "done"
    by["SEL1"]["note"] = ((by["SEL1"].get("note") or "") +
                          " | 2026-09-26 판정: 노이즈/악화(Δ−0.0071). 5폴드×5시드 — "
                          "top10 0.5364±0.0233 · top15 0.5343±0.0257 · top20 0.5345±0.0245 vs "
                          "top30 0.5414±0.0308 → 피처를 줄이면 나빠진다. 축 닫음.").strip()
    print("SEL1 결론 기록")

item = {
    "id": "HP4",
    "title": "depth1·lr0.05 안정성 재검증(시드 10) — 유일하게 재현되는 방향의 확정",
    "status": "pending",
    "priority": 3,
    "arm": "HP_d1_lr05_h5",
    "counterfactual": "LS_quant_q30_h5",
    "hypothesis": ("depth1(스텀프)·lr0.05 가 5폴드 기준선 대비 +0.0086 을 3회 독립 런에서 재현했다"
                   "(HP1 d2·HP2 d1·HP3 d1+dropcat). 시드를 3→5→10 으로 늘려도 이 방향이 유지되는가? "
                   "유지되면 챔피언 레시피 후보로 승격 논의에 올린다(+0.02 문턱은 여전히 미달임을 명시)."),
    "evidence": ("HP2(5폴드×5시드): d1 0.5500±0.0515 vs 기준 0.5414±0.0308. "
                 "HP3: d1+catboost제외 0.5499, d2+균등가중 0.5475. "
                 "폴드 std 가 0.04~0.05 로 커서 시드 수에 민감할 수 있다(이 항목이 그걸 확인한다)."),
    "command": ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 3600 "
                "python -u scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_420_asofpatch.npz "
                "--folds 5 --seeds 10 --only LS_quant_q30_h5,HP_d1_lr05_h5'"),
    "metric": "wf_sweep_summary",
    "success": "arm 이 대조군 대비 +0.02 이상(승격 문턱) — 미달이면 '방향은 재현되나 폭이 작다'로 기록",
    "expected": "Δ+0.005~+0.010 (문턱 미달 예상)",
    "cost": "약 6분 (2 config × 5폴드 × 10시드 = 100 학습)",
    "est_minutes": 25,
    "risk": "시드 수를 바꾸면 기록 기준선과 직접 비교가 아니라 같은 런 대조군과 비교해야 한다.",
    "note": "이 항목이 끝나면 내부 축(라벨·피처풀·변환·유니버스·HP·앙상블·프로토콜·선별크기)은 모두 소진이다.",
}
if "HP4" in by:
    by["HP4"].update(item)
    print("HP4 갱신")
else:
    b["items"].append(item)
    print("HP4 추가")

shutil.copy(P, P + ".bak_" + time.strftime("%Y%m%d-%H%M%S"))
b["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S+09:00")
with open(P, "w", encoding="utf-8") as f:
    json.dump(b, f, ensure_ascii=False, indent=1)
print("pending:", [i["id"] for i in b["items"] if i.get("status") == "pending"])

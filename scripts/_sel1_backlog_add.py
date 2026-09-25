#!/usr/bin/env python3
"""백로그에 F3 결론 + SEL1(선별 크기 축) 추가 (2026-09-26 틱 2 마무리).

F3(5폴드×5시드): 기준 5폴드 0.5414 vs 3폴드 0.5256(Δ−0.0158) · 3폴드+depth1 0.5390
  → 최소 학습창을 늘려도 오르지 않는다. '소표본 페널티'가 주 원인이 아님을 실측으로 확정.
다음 축: 선별 크기(top10/15/20) — 정렬 버그 수정 후 미측정.
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

if "F3" in by:
    by["F3"]["status"] = "done"
    by["F3"]["note"] = ((by["F3"].get("note") or "") +
                        " | 2026-09-26 판정: 악화(Δ−0.0158). 3폴드 0.5256±0.0239 · "
                        "3폴드+depth1 0.5390±0.0228 vs 5폴드 대조군 0.5414±0.0308 "
                        "→ 최소 학습창 확대는 이득이 아니다. 소표본 페널티 가설 기각(프로토콜 축 닫음).").strip()
    print("F3 결론 기록")

item = {
    "id": "SEL1",
    "title": "선별 크기 축: top10/15/20 vs top30 (정렬 수정 후 미측정)",
    "status": "pending",
    "priority": 2,
    "arm": "SEL_top15_h5",
    "counterfactual": "LS_quant_q30_h5",
    "hypothesis": ("학습행이 폴드당 1,230행인데 피처는 30개를 쓴다 — 피처를 줄이면 "
                   "(top10/15/20) 일반화가 좋아져 폴드 평균이 +0.02 이상 오르는가?"),
    "evidence": ("RB1 정렬 버그 수정 후 선별 크기 스윕은 미실측(기존 top40/top60 계열은 변환 실험에 "
                 "묶여 있었다). 참고: 3종 모델 앙상블의 val AUC 가 학습 AUC(0.79~0.90)와 크게 벌어져 "
                 "과적합 신호가 일관되게 나온다(HP1~HP3 15개 config 에서 depth 단조)."),
    "command": ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 3600 "
                "python -u scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_420_asofpatch.npz "
                "--folds 5 --seeds 5 --only LS_quant_q30_h5,SEL_top10_h5,SEL_top15_h5,SEL_top20_h5'"),
    "metric": "wf_sweep_summary",
    "success": "arm(SEL_top15_h5) 폴드 평균이 대조군(top30, 같은 런) 대비 +0.02 이상",
    "expected": "미지 — 과적합 신호가 맞다면 top10~15 가 유리할 가능성",
    "cost": "약 5분 (4 config × 5폴드 × 5시드 = 100 학습)",
    "est_minutes": 25,
    "risk": "topN 은 학습 구간에서만 edge 를 계산하므로(폴드별 재선별) 룩어헤드 없음.",
    "note": ("이 축까지 실패하면 내부 축은 소진이다 → 남은 것은 ① 프로덕션 게이트 완화(CO_* 실측 "
             "−0.015, 승인 필요) ② 판정 지표(pooled → 날짜별 횡단면 AUC) 전환 ③ 리서처 백필"
             "(주가 이력 2025-06-16 부터 → 표본 확대 불가)."),
}
if "SEL1" in by:
    by["SEL1"].update(item)
    print("SEL1 갱신")
else:
    b["items"].append(item)
    print("SEL1 추가")

shutil.copy(P, P + ".bak_" + time.strftime("%Y%m%d-%H%M%S"))
b["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S+09:00")
with open(P, "w", encoding="utf-8") as f:
    json.dump(b, f, ensure_ascii=False, indent=1)
print("pending:", [i["id"] for i in b["items"] if i.get("status") == "pending"])

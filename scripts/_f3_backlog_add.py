#!/usr/bin/env python3
"""백로그에 F3(프로토콜 민감도) 추가 + HP2/HP3 결론 기록 (2026-09-26 틱 2).

HP2(5폴드×5시드): d1 0.5500 · d2+강정규화 0.5491 · d2 0.5470 · d3+강정규화 0.5467 ·
  d2·lr0.08 0.5460 vs 대조군 0.5414 → Δ+0.0046~+0.0086 (문턱 미달, 노이즈).
HP3(5폴드×5시드): EN_equal 0.5418(Δ+0.0004) · EN_drop_cat 0.5379(Δ−0.0035) ·
  EN_d2_equal 0.5475 · EN_d1_dropcat 0.5499 → 앙상블 축도 닫음.
→ 남은 내부 축: 최소 학습창(프로토콜 민감도). 소표본 페널티가 실재하는지 측정한다.
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

for iid, note in [
    ("HP2", " | 2026-09-26 판정: 노이즈(Δ+0.0077). 5폴드×5시드 — d1 0.5500±0.0515 · "
            "d2+강정규화 0.5491±0.0505 · d2 0.5470±0.0450 · d3+강정규화 0.5467±0.0387 · "
            "d2·lr0.08 0.5460±0.0439 vs 대조군 0.5414±0.0308. 모든 HP 변형이 기준선보다 "
            "+0.005~+0.009 높지만 문턱 +0.02 미달 → 축 닫음(방향만 기록)."),
    ("HP3", " | 2026-09-26 판정: 노이즈(Δ+0.0004). 5폴드×5시드 — EN_equal 0.5418±0.0323 · "
            "EN_drop_cat 0.5379±0.0274(악화: catboost 제거는 해롭다) · EN_d2_equal 0.5475 · "
            "EN_d1_dropcat 0.5499(HP 축 효과만 재현) → 앙상블 구성 축 닫음."),
]:
    if iid in by:
        by[iid]["status"] = "done"
        by[iid]["note"] = ((by[iid].get("note") or "") + note).strip()
        print(f"{iid} 결론 기록")

PANEL = "/app/app/models/wf/panel_420_asofpatch.npz"
ONLY = "LS_quant_q30_h5,F3_base_h5,F3_d1_h5"
item = {
    "id": "F3",
    "title": "프로토콜 민감도: 5폴드(최소 학습창 1,230행) vs 3폴드(약 3,400행) — 소표본 페널티 측정",
    "status": "pending",
    "priority": 2,
    "arm": "F3_base_h5",
    "counterfactual": "LS_quant_q30_h5 (5폴드, 같은 런)",
    "hypothesis": (
        "depth 를 낮출수록 AUC 가 단조로 올랐다(d1 0.5500 > d2 0.5491 > d3 0.5467 > d4 0.5414 > "
        "d6 0.5344) — 전형적 소표본 과적합 증상이다. 폴드 수를 3으로 줄여 최소 학습창을 약 3배로 "
        "키우면(같은 데이터·같은 시드) 폴드 평균이 유의하게 오르는가? 오른다면 '표본 부족'이 "
        "이 스택의 실질 병목이라는 증거이고, 아니라면 AUC 상한은 표본 크기 문제가 아니다."),
    "evidence": (
        "fold1 학습행 1,230행(49종목×약25일)·테스트 1,378행. HP1·HP2·HP3 3회 실측 모두 "
        "'depth 낮을수록 좋다'가 재현(총 15개 config). DB 실측: 주가 이력이 2025-06-16~ "
        "2026-09-23 뿐이라(3,934종목·1,078,479행) --days 확대로 행을 늘릴 수는 없다 → "
        "표본을 늘리는 유일한 내부 수단이 '폴드당 학습창 확대'다."),
    "command": (f"docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 3600 "
                f"python -u scripts/wf_label_sweep.py --panel {PANEL} --folds 5 --seeds 5 "
                f"--only {ONLY}'"),
    "metric": "wf_sweep_summary",
    "success": ("F3_base_h5 폴드 평균이 같은 런 5폴드 대조군 대비 +0.02 이상 (단, 프로토콜이 "
                "다르므로 이 값은 '소표본 페널티 크기'의 추정이지 승격 근거가 아니다)"),
    "expected": "미지 — 페널티가 실재하면 +0.01~+0.03, 아니면 Δ≈0",
    "cost": "약 3~5분 (5폴드 1개 + 3폴드 2개 × 5시드 = 55 학습)",
    "est_minutes": 25,
    "risk": ("프로토콜이 다르면 기록 기준선과 직접 비교가 무효다(구동기는 같은 런의 cf 와만 비교). "
             "3폴드 결과를 '기준선 갱신'에 쓰려면 별도 승인이 필요하다 — 이 항목은 페널티 측정만 한다."),
    "note": ("fold 수 override 는 wf_label_sweep 의 cfg[\"folds\"] 로만 동작(스크립트 밖 무변경). "
             "시드 5·패널 동일·라벨 동일이라 '학습창 크기'만 바뀐다."),
}
if "F3" in by:
    by["F3"].update(item)
    print("F3 갱신")
else:
    b["items"].append(item)
    print("F3 추가")

shutil.copy(P, P + ".bak_" + time.strftime("%Y%m%d-%H%M%S"))
b["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S+09:00")
with open(P, "w", encoding="utf-8") as f:
    json.dump(b, f, ensure_ascii=False, indent=1)
print("pending:", [i["id"] for i in b["items"] if i.get("status") == "pending"])

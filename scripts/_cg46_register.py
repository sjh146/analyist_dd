#!/usr/bin/env python3
"""CG46 등록 — 유니버스 결정성 수리 후 첫 재측정(같은 모델 2회 반복 + 챌린저 1회).

왜: 2026-10-01 03:0x 실측으로 select_training_universe 의 **비결정성**이 확인됐다
(같은 커넥션 4회 실행에서 limit=60 교집합 [60,52,57,54] · limit=200 [200,184,181,171] ·
eligible 2,655종목 중 2,543종목이 latest 동률이라 top 컷이 동률 내부를 자름).
그 결과 같은 모델·같은 창·같은 프로토콜의 견고 AUC 가 0.5163(CG45 02:15) → 0.5336(CG33 03:10)
으로 +0.0173 흔들렸다 → **사전문턱 +0.02 가 프로토콜 잡음 안에 있었다**.
수리(app/training/universe.py: ORDER BY + 2단 정렬) 완료, `_universe_determinism_test.py` ALL PASS.

이 항목이 재는 것 2가지:
  ① 프로토콜 잡음 바닥 — 같은 모델을 같은 커맨드에서 두 번 채점한 |Δ|. 수리 전 0.0173.
  ② 수리된 결정적 유니버스에서의 챌린저 vs 챔피언 짝 Δ(승격 근거의 재확립).
판정은 첫 --out(챔피언 A)만 잡히므로, 세 JSON 을 직접 파싱해 짝 Δ 를 원장에 실어야 한다(CG40 교훈).
"""
import json
import os

BACKLOG = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "..", "docs", "QUANT_MODEL_BACKLOG.json"))

BASE = "docker exec stock_xgboost_ml sh -c 'cd /app && "
COMMON = ("--universe training --label-kind rel --horizon 5 --folds 5 --dates-per-fold 10 --stocks 60 ")
CHAMP_ARGS = "--model-dir app/models/champion --train-start 2026-06-25 --train-end 2026-09-23 "
CAND_ARGS = "--model-dir app/models/champion_cand --train-start 2026-07-02 --train-end 2026-09-30 "


def run(model_args, out):
    return (f"OMP_NUM_THREADS=4 timeout 3600 python -u scripts/champion_robust_eval.py "
            f"{model_args}{COMMON}--out app/reports/{out} && ")


CMD = (BASE
       + run(CHAMP_ARGS, "cg46_champ_a.json")
       + run(CHAMP_ARGS, "cg46_champ_b.json")
       + run(CAND_ARGS, "cg46_challenger.json").rstrip(" && ")
       + "'")

ITEM = {
    "id": "CG46",
    "title": "유니버스 결정성 수리 검증 — 같은 모델 2회 반복으로 프로토콜 잡음 바닥 재측정 + 챌린저 짝 Δ (수리 후 첫 결정적 측정)",
    "status": "pending",
    "priority": 1,
    "affects_model": True,
    "depends_on": ["CG33"],
    "baseline": {
        "value": 0.5336,
        "source": ("CG33 같은 런 챔피언 재측정(수리 **전** 비결정 유니버스) — 수리 후 값은 이 항목이 새로 정한다"),
    },
    "hypothesis": (
        "지금까지의 모든 AUC 비교(48사이클)는 **실행마다 다른 종목 집합**을 채점해 왔다. "
        "select_training_universe 가 latest 동률(2,543/2,655종목)에서 SQL 반환 순서에 의존했기 때문이다. "
        "수리 후에는 같은 모델·같은 창의 두 번 채점이 사실상 동일해야 하며(|Δ| ≤ 0.005), "
        "그러면 비로소 +0.02 문턱이 잡음 위에 선다."
    ),
    "evidence": (
        "① 결정성 프로브(수리 전): 같은 커넥션 4회 limit=60 교집합 [60,52,57,54] · limit=200 [200,184,181,171] · "
        "새 커넥션 3회 [48,53,48] · eligible 두 번 조회 순서 상이. ② 같은 모델 재측정 산포 +0.0173 "
        "(CG45 0.5163 [0.4691,0.5320,0.5479] → CG33 0.5336 [0.5199,0.5212,0.5597], 창·프로토콜 동일, "
        "표본 교집합 22 vs 25). ③ 수리 후 프로브 ALL PASS(4회·3회 전부 교집합 100%). "
        "④ 이 산포는 사전문턱 +0.02 와 같은 크기다 — 즉 지금까지의 '노이즈' 판정 상당수가 저전력 설계였다."
    ),
    "method": (
        "같은 커맨드에서 3-arm 연속 실행(챔피언 A · 챔피언 B · 챌린저). 하나의 프로세스 시퀀스로 돌려 "
        "창·유니버스·DB 스냅샷을 고정한다. ① |챔피언A − 챔피언B| 로 잡음 바닥을 실측 "
        "② 챌린저 − 챔피언A 짝 Δ 로 승격 방향 재판정 ③ 세 JSON 의 universe.overlap_train200 이 "
        "동일한지(= 수리 효과) 확인."
    ),
    "arm": "champion_cand (생산 경로 재학습 후보 · 200종목·90일·2026-07-02~09-30)",
    "counterfactual": "배포 챔피언(app/models/champion) 같은 런 · 같은 결정적 유니버스",
    "command": CMD,
    "check": "python3 scripts/model_engineer_cycle.py --status",
    "check_target": {"op": ">=", "value": 1},
    "metric": "champion_robust_eval",
    "est_minutes": 20,
    "cost": "3 arm × 약 4.6분 ≈ 14분 (컨테이너 timeout 3600s/arm)",
    "success": (
        "|챔피언A − 챔피언B| ≤ 0.005 (수리 전 0.0173) → 프로토콜 잡음 바닥이 문턱 아래로 내려갔다. "
        "그 위에서 챌린저 짝 Δ 를 확정한다. 0.005 초과면 비결정 요인이 다른 곳(날짜 샘플링·DB 스냅샷)에도 남아 있다."
    ),
    "expected": "잡음 바닥 ≤ 0.005 · 챌린저 Δ 는 0 근처(수리 전 −0.0066 과 같은 방향 예상)",
    "note": (
        "판정은 첫 --out(챔피언 A)만 잡힌다 → 세 JSON 을 직접 파싱해 `parsed.paired`·"
        "`parsed.counterfactual_arm` 에 실어라(per_exp 금지 — 스코어보드 오독). "
        "수리 대상 파일: services/xgboost-ml/app/training/universe.py(이 역할 소유). "
        "부작용: 생산 재학습의 200종목 선택이 이제 **재현 가능**해진다 — 과거 학습분과는 종목이 달라질 수 있으므로 "
        "수리 이전 AUC 수치와 직접 비교하지 말 것."
    ),
}


def main():
    with open(BACKLOG, encoding="utf-8") as f:
        doc = json.load(f)
    items = doc["items"]
    if any(i.get("id") == "CG46" for i in items):
        print("CG46 이미 존재 — 중단")
        return
    items.append(ITEM)
    with open(BACKLOG, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("CG46 추가. pending 목록:", [i["id"] for i in items if i.get("status") == "pending"])


if __name__ == "__main__":
    main()

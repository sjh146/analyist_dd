#!/usr/bin/env python3
"""CG47 등록 — 시드 3개 × (챔피언, 챌린저) 짝 설계로 '유니버스 정체 잡음 위에서 부호가 유지되는가'.

배경(실측 2026-10-01):
  · CG46: 결정적 유니버스(seed 0)에서 챔피언 0.4935 를 **두 번 비트 동일**하게 재현(수리 전 산포 0.0173)
    → 프로토콜 잡음 제거 성공. 그 위에서 챌린저(champion_cand) 0.5161 → 짝 Δ **+0.0226**(3/3 창 양(+)).
  · 그런데 CG13 실측: 유니버스 정체만 바꿔도 폴드 평균이 **Δ0.0287** 움직인다(서로소 30종목 5구간).
    즉 단일 유니버스의 +0.0226 은 유니버스 교체 잡음보다 작아 그대로는 승격 근거가 못 된다.
  · 다행히 `--universe-seed` 실측(방금): seed0 vs seed1 교집합 1종목 · seed0 vs seed2 0종목
    → 시드만 바꾸면 **사실상 서로소 유니버스**가 결정적으로 나온다 = 짝 설계에 그대로 쓸 수 있다.

이 항목이 정하는 것: 3개 유니버스에서 (챌린저 − 챔피언) 짝 Δ 의 평균과 부호 일관성.
  · 3/3 양(+)이고 평균 ≥ +0.02  → 챌린저 우위가 유니버스에 견고 → 승격 검토(별도 승인, dry-run 부터)
  · 부호 뒤섞임 또는 평균 < +0.02 → '유니버스 정체 잡음'으로 닫고 잡음 바닥을 사전문턱 **위로** 올린다
    (즉 +0.02 는 이 스택에서 검출 가능한 최소 효과가 아니라는 결론이 승격 게이트 개편의 근거가 된다)
"""
import json
import os

BACKLOG = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "..", "docs", "QUANT_MODEL_BACKLOG.json"))

BASE = "docker exec stock_xgboost_ml sh -c 'cd /app && "
COMMON = "--universe training --label-kind rel --horizon 5 --folds 5 --dates-per-fold 10 --stocks 60 "
CHAMP = "--model-dir app/models/champion --train-start 2026-06-25 --train-end 2026-09-23 "
CAND = "--model-dir app/models/champion_cand --train-start 2026-07-02 --train-end 2026-09-30 "
SEEDS = (0, 1, 2)


def run(model_args, seed):
    return (f"OMP_NUM_THREADS=4 timeout 3600 python -u scripts/champion_robust_eval.py "
            f"{model_args}{COMMON}--universe-seed {seed} "
            f"--out app/reports/cg47_s{seed}_{'cand' if model_args is CAND else 'champ'}.json && ")


parts = []
for s in SEEDS:
    parts.append(run(CHAMP, s))
    parts.append(run(CAND, s))
CMD = BASE + "".join(parts).rstrip(" && ") + "'"

ITEM = {
    "id": "CG47",
    "title": "시드 3개 × (챔피언·챌린저) 짝 설계 — 챌린저 우위(+0.0226)가 유니버스 정체를 넘어 견고한가",
    "status": "pending",
    "priority": 1,
    "affects_model": True,
    "depends_on": ["CG46", "CG13"],
    "baseline": {
        "value": 0.4935,
        "source": ("CG46 실측 — 결정적 유니버스(seed0)에서 챔피언 견고 AUC 2회 비트 동일(0.4935). "
                   "**수리 후 첫 기준선**이며 수리 전 값(0.5163/0.5336)과는 종목 집합이 달라 비교 금지"),
    },
    "hypothesis": (
        "생산 경로 재학습 후보(champion_cand)가 챔피언보다 낫다는 CG46 의 짝 Δ +0.0226 은 단일 유니버스 "
        "관측이다. 유니버스 정체만 바꿔도 Δ0.0287 이 움직이므로(CG13), 시드 3개(사실상 서로소 유니버스)에서 "
        "부호와 크기가 유지되어야만 '진짜 우위'다."
    ),
    "evidence": (
        "① CG46: 챔피언 0.4935±0.0268 [0.4732,0.5314,0.4758] 2회 비트 동일 · 챌린저 0.5161±0.0182 "
        "[0.5057,0.5417,0.5008] → 짝 Δ +0.0226(창별 [+0.0325,+0.0103,+0.0250], 3/3 양(+)) · "
        "3창이라 t=3.47·df=2 로 p≈0.07(문턱은 넘지만 통계적 확정은 아님). "
        "② CG33(수리 전·비결정 유니버스): 같은 두 모델이 Δ−0.0066 — 부호가 뒤집혔다 = 유니버스 선택이 지배. "
        "③ --universe-seed 프로브: seed0 vs seed1 교집합 1종목 · seed0 vs seed2 0종목 · 같은 seed 재현. "
        "④ CG13: 서로소 30종목 5구간 폴드 평균 0.5204→0.4917(총폭 0.0287, 거의 단조) = 유니버스 정체 잡음."
    ),
    "method": (
        "한 커맨드에서 6-arm 연속 실행 — seed 0/1/2 × (챔피언, 챌린저). 모델·창·라벨·종목수를 고정하고 "
        "유니버스만 시드로 바꾼다. 판정 = 시드별 짝 Δ=(챌린저−챔피언) 의 평균·부호 일관성·최악값."
    ),
    "arm": "champion_cand (생산 경로 200종목·90일·2026-07-02~09-30)",
    "counterfactual": "배포 챔피언(app/models/champion) — 같은 시드·같은 창",
    "command": CMD,
    "check": "python3 scripts/cg47_pairs_report.py",
    "check_target": {"op": ">=", "value": 1},
    "metric": "champion_robust_eval",
    "est_minutes": 35,
    "cost": "6 arm × 약 4.6분 ≈ 28분 (컨테이너 timeout 3600s/arm)",
    "success": (
        "시드 3개 짝 Δ 평균 ≥ +0.02 **그리고** 양(+) 시드 ≥ 3/3 → 챌린저 우위가 유니버스에 견고 → "
        "승격 검토(별도 승인·dry-run 부터). 부호 뒤섞임이면 '유니버스 정체 잡음'으로 닫고, +0.02 문턱이 "
        "이 스택의 검출 한계 이하라는 결론을 게이트 개편 승인 근거로 쓴다."
    ),
    "expected": "미지 — CG46 +0.0226 vs CG33 −0.0066 이 갈렸으므로 어느 쪽이든 정보가 크다.",
    "note": (
        "판정은 첫 --out(챔피언 seed0)만 잡힌다 → `python3 scripts/cg47_pairs_report.py` 로 6개 JSON 을 "
        "파싱해 짝 Δ 를 계산하고 원장 `parsed.paired` 에 실어라(per_exp 금지 — 스코어보드 오독). "
        "6창(3시드×3창)이라 짝 Δ 의 SE 를 함께 보고하라 — 3창만으로는 p≈0.07 로 확정 불가(CG38 교훈)."
    ),
}


def main():
    with open(BACKLOG, encoding="utf-8") as f:
        doc = json.load(f)
    items = doc["items"]
    if any(i.get("id") == "CG47" for i in items):
        print("CG47 이미 존재 — 중단")
        return
    items.append(ITEM)
    with open(BACKLOG, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("CG47 추가. pending:", [i["id"] for i in items if i.get("status") == "pending"])


if __name__ == "__main__":
    main()

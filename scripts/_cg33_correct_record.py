#!/usr/bin/env python3
"""CG33 기록 교정 — 구동기 자동 판정은 대조값(CG45 0.5163)을 썼지만 그 값은 **다른 유니버스**에서
측정된 것이었다(수리 전 select_training_universe 는 비결정). 같은 런에서 5분 뒤 재측정한 챔피언은
0.5336 이므로, 정직한 같은 런 짝 Δ = 0.5270 − 0.5336 = −0.0066 이다.

추가로 기록할 실측: **같은 모델·같은 창·같은 프로토콜의 재측정 산포 +0.0173**
(CG45 02:15 0.5163 [0.4691,0.5320,0.5479] → CG33 03:10 0.5336 [0.5199,0.5212,0.5597]).
원인은 유니버스 비결정(표본 교집합 22 → 25).

per_exp 는 만들지 않는다(스코어보드가 arm 최고값으로 오독 — CG31 사고).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_engineer_cycle as m  # noqa: E402

CHAL, CHAMP = 0.5270, 0.5336
CG45 = 0.5163
DELTA_SAME_RUN = round(CHAL - CHAMP, 4)      # -0.0066
SPREAD = round(CHAMP - CG45, 4)              # +0.0173

DETAIL = (
    f"챌린저(champion_cand·생산 경로 200종목·90일) 견고 AUC {CHAL:.4f}±0.0257 "
    "(시간창 3개 [0.5074, 0.5633, 0.5103] · 풀링 0.5342 · 날짜별평균 0.5270) · "
    f"같은 런 챔피언 재측정 {CHAMP:.4f}±0.0185 [0.5199, 0.5212, 0.5597] → Δ{DELTA_SAME_RUN:+.4f} "
    "(문턱 +0.02 미달 = 노이즈, 챌린저 우위 없음). "
    f"⚠ 같은 모델 재측정 산포 {SPREAD:+.4f}(CG45 {CG45:.4f} → 본 런 {CHAMP:.4f}): "
    "수리 전 유니버스 비결정(표본 교집합 22 vs 25)이 원인 — 이 산포가 사전문턱 +0.02 안에 있었다."
)

PAIRED = {
    "delta_same_run": DELTA_SAME_RUN,
    "challenger_auc": CHAL,
    "champion_same_run_auc": CHAMP,
    "delta_vs_cg45_value": round(CHAL - CG45, 4),
    "same_model_rerun_spread": SPREAD,
    "cg45_auc": CG45,
    "note": ("CG45 0.5163 과 본 런 0.5336 은 같은 모델·같은 창·같은 프로토콜인데도 +0.0173 달랐다 — "
             "원인은 select_training_universe 비결정(수리 완료, _universe_determinism_test ALL PASS). "
             "따라서 'CG45 대비 Δ' 는 유니버스가 다른 값과의 비교라 무효이고, "
             "판정은 같은 런의 챌린저 vs 챔피언(Δ−0.0066)만 쓴다."),
}
CF_ARM = {
    "model_dir": "app/models/champion", "robust_auc": CHAMP, "auc_std_across_folds": 0.0185,
    "fold_means": [0.5199, 0.5212, 0.5597], "auc_pooled": 0.5335, "auc_per_date_mean": 0.5336,
    "rows_scored": 1800, "overlap_train200": 25,
}

VERDICT = "짝 노이즈(챌린저 우위 없음)"


def main():
    led = m.load_ledger()
    n = 0
    for r in led:
        if r.get("id") == "CG33" and r.get("rc") == 0:
            p = r.setdefault("parsed", {})
            p["counterfactual_arm"] = CF_ARM
            p["paired"] = PAIRED
            r["verdict"] = VERDICT
            r["detail"] = DETAIL
            r["reported"] = True
            r["reported_at"] = m.datetime.now().astimezone().isoformat(timespec="seconds")
            n += 1
    if n:
        m._rewrite_ledger(led)
    print(f"원장 CG33 교정 완료: {n}건 · reported=True")

    bl = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                                      "docs", "QUANT_MODEL_BACKLOG.json"))
    with open(bl, encoding="utf-8") as f:
        doc = json.load(f)
    it = next(i for i in doc["items"] if i["id"] == "CG33")
    it["result"] = {"verdict": VERDICT, "detail": DETAIL, "delta": DELTA_SAME_RUN,
                    "paired": PAIRED, "counterfactual_arm": CF_ARM, "per_exp": None, "rc": 0}
    it["note"] = (it.get("note") or "") + (
        " | 2026-10-01 03:15 실측 결과: 챌린저 0.5270 vs 같은 런 챔피언 0.5336 = Δ−0.0066 → 챌린저 우위 없음"
        "(게이트가 챔피언을 유지한 결론 자체는 OOS 로도 뒤집히지 않는다 — 다만 게이트의 근거(인샘플 비교)는 "
        "여전히 무효). 부수 발견: 같은 모델 재측정 산포 +0.0173 = 유니버스 비결정 → CG46 으로 수리 후 재측정.")
    with open(bl, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("백로그 CG33 result/note 갱신 완료")


if __name__ == "__main__":
    main()

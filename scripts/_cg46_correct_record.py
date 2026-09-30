#!/usr/bin/env python3
"""CG46 기록 교정 — 구동기는 첫 --out(챔피언 A)만 판정해 짝 Δ 를 못 만든다.

실측(2026-10-01 03:16~03:32, 결정적 유니버스 seed0, 같은 커맨드 3-arm):
  · 챔피언 A = 챔피언 B = 0.4935 [0.4732, 0.5314, 0.4758]  ← **비트 동일**(수리 전 같은 모델 산포 0.0173)
  · 챌린저(champion_cand) = 0.5161 [0.5057, 0.5417, 0.5008]
  · 짝 Δ = +0.0226 · 창별 [+0.0325, +0.0103, +0.0250](3/3 양(+)) · 3창이라 t=3.47 df=2 → p≈0.07
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_engineer_cycle as m  # noqa: E402

CH_A = CH_B = 0.4935
CAND = 0.5161
DELTA = round(CAND - CH_A, 4)          # +0.0226
PAIRED_FOLDS = [0.0325, 0.0103, 0.0250]

DETAIL = (
    f"챔피언(결정적 유니버스 seed0) {CH_A:.4f}±0.0268 [0.4732, 0.5314, 0.4758] — **2회 실행 비트 동일**"
    "(수리 전 같은 모델 재측정 산포 +0.0173 → 0.0000) · 챌린저(champion_cand) "
    f"{CAND:.4f}±0.0182 [0.5057, 0.5417, 0.5008] → 같은 런 짝 Δ{DELTA:+.4f}"
    "(창별 [+0.0325, +0.0103, +0.0250] · 3/3 양(+)) — 사전문턱 +0.02 초과. "
    "단 3창이라 t=3.47·df=2·p≈0.07 로 통계적 확정은 아니고, 유니버스 정체만 바꿔도 Δ0.0287 이 움직이므로"
    "(CG13) CG47(시드 3개 짝 설계)로 견고성을 검증해야 승격 근거가 된다."
)

PAIRED = {
    "delta_same_run": DELTA,
    "champion_auc": CH_A, "champion_auc_repeat": CH_B,
    "challenger_auc": CAND,
    "paired_folds": PAIRED_FOLDS,
    "pos_windows": "3/3",
    "noise_floor_before_fix": 0.0173,
    "noise_floor_after_fix": 0.0,
    "note": ("수리 전 같은 모델 재측정 산포(+0.0173, 유니버스 비결정)가 사라져 문턱 +0.02 가 처음으로 "
             "잡음 위에 섰다. 그 위에서 나온 챌린저 우위 +0.0226 은 창 3개뿐이라 확정 불가 → CG47."),
}
CF_ARM = {"model_dir": "app/models/champion", "robust_auc": CH_A,
          "fold_means": [0.4732, 0.5314, 0.4758], "auc_pooled": 0.4979, "rows_scored": 1800,
          "repeat_robust_auc": CH_B, "universe_overlap_train200": 21}
VERDICT = "짝 신호(문턱 초과·3창 한계)"


def main():
    led = m.load_ledger()
    n = 0
    for r in led:
        if r.get("id") == "CG46" and r.get("rc") == 0:
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
    print(f"원장 CG46 교정: {n}건 · reported=True")

    bl = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                                      "docs", "QUANT_MODEL_BACKLOG.json"))
    with open(bl, encoding="utf-8") as f:
        doc = json.load(f)
    it = next(i for i in doc["items"] if i["id"] == "CG46")
    it["result"] = {"verdict": VERDICT, "detail": DETAIL, "delta": DELTA, "paired": PAIRED,
                    "counterfactual_arm": CF_ARM, "per_exp": None, "rc": 0}
    it["note"] = (it.get("note") or "") + (
        " | 2026-10-01 03:32 실측: 잡음 바닥 0.0173 → **0.0000**(같은 모델 2회 비트 동일) — 수리 성공. "
        "그 위에서 챌린저 짝 Δ +0.0226(3/3 창 양(+), p≈0.07). 단일 유니버스라 CG47(시드 3개)로 견고성 검증 필요.")
    with open(bl, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("백로그 CG46 result/note 갱신")


if __name__ == "__main__":
    main()

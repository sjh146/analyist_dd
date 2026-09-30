#!/usr/bin/env python3
"""CG33 을 pending 으로 승격 — CG45 가 만든 '정직한 OOS 기준선 0.5163' 위에서 챌린저를 잰다.

배경(실측):
  · CG43(2026-09-30 23:15): 생산 경로 재학습 후보 champion_cand 생성 성공 —
    학습 2026-07-02~09-30 · 12,100행 · 인샘플 ensemble_auc 0.5434. 게이트는 인샘플 0.5434 를
    단일분할 기준선 0.5513 과 비교해 kept_incumbent(거부).
  · CG45(2026-10-01 02:15): 같은 챔피언을 **프로토콜 정합**(학습도메인 유니버스·겹침 창 자동
    제외·rel h5·5폴드×10일)으로 재면 0.5163±0.0340 · 3창 [0.4691, 0.5320, 0.5479].
  즉 게이트는 '인샘플 vs 인샘플'로 비교하는데, 승격 판단에 필요한 것은 '같은 OOS 프로토콜의
  챌린저 vs 챔피언'이다. 그 값이 승격 근거의 마지막 공백이다(CG33 setup_needed 의 잔여 항목).

같은 런 2-arm 으로 돌린다(창 고정 — CG40/CG41 교훈: 시각이 밀리면 창 경계가 움직여 짝이 깨진다).
챌린저를 먼저 두어 구동기가 챌린저 JSON 을 판정하게 한다(구동기 `_out_arg` 는 첫 --out 만 읽는다).
챔피언 재측정이 0.5163 과 다르면 창이 움직였다는 뜻이므로 그 사실을 원장·보고에 남긴다.
"""
import json
import os

BACKLOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "QUANT_MODEL_BACKLOG.json")
BACKLOG = os.path.abspath(BACKLOG)

CMD = (
    "docker exec stock_xgboost_ml sh -c 'cd /app && "
    "OMP_NUM_THREADS=4 timeout 3600 python -u scripts/champion_robust_eval.py "
    "--model-dir app/models/champion_cand --universe training --label-kind rel --horizon 5 "
    "--folds 5 --dates-per-fold 10 --stocks 60 "
    "--train-start 2026-07-02 --train-end 2026-09-30 "
    "--out app/reports/cg33_challenger_oos.json && "
    "OMP_NUM_THREADS=4 timeout 3600 python -u scripts/champion_robust_eval.py "
    "--model-dir app/models/champion --universe training --label-kind rel --horizon 5 "
    "--folds 5 --dates-per-fold 10 --stocks 60 "
    "--train-start 2026-06-25 --train-end 2026-09-23 "
    "--out app/reports/cg33_champion_recheck.json'"
)

NOTE = (
    "2026-10-01 03:0x 갱신 — CG33 을 backlog → **pending** 으로 승격(CG45 가 기준선을 만들었으므로 "
    "'챌린저가 정의되기 전에는 pending 으로 올리지 말라'는 setup_needed 조건이 해소됐다). "
    "챌린저 = CG43 이 만든 생산 경로 후보 champion_cand(200종목·90일·2026-07-02~09-30·인샘플 0.5434). "
    "같은 런 2-arm(챌린저 → 챔피언 재측정)으로 창을 고정한다. "
    "두 arm 의 OOS 창은 w1 2025-12-01~2026-01-21 · w2 2026-01-29~2026-03-24 · w3 2026-04-01~2026-05-21 "
    "3개로 동일해야 한다(각 모델의 학습구간과 겹치는 w4·w5 는 자동 제외). "
    "⚠ 판정은 첫 --out(챌린저)만 잡히므로 챔피언 재측정 파일 "
    "services/xgboost-ml/app/reports/cg33_champion_recheck.json 을 열어 **창이 같고 값이 0.5163 과 "
    "일치하는지** 직접 확인하라 — 다르면 창 드리프트이므로 짝 Δ 를 재측정 값 기준으로 다시 계산한다. "
    "한계: 3창이라 SE 가 0.08 대 → 이 짝 Δ 는 승격 '확정'용이 아니라 게이트 방향 판정용이다(CG38 교훈). "
    "또 모든 창이 두 모델의 학습구간 **이전**(2025-12~2026-05)이다 — 학습이 최신 데이터까지라 "
    "학습구간 이후의 진짜 전방 창이 DB 에 아직 없다. 그 한계를 보고에 명시할 것."
)


def main():
    with open(BACKLOG, encoding="utf-8") as f:
        doc = json.load(f)
    items = doc["items"] if isinstance(doc, dict) and "items" in doc else doc
    it = next((i for i in items if i.get("id") == "CG33"), None)
    if it is None:
        raise SystemExit("CG33 not found")

    it["status"] = "pending"
    it["priority"] = 1
    it["metric"] = "champion_robust_eval"
    it["est_minutes"] = 15
    it["arm"] = "champion_cand (생산 경로 재학습 후보 · 200종목·90일·2026-07-02~09-30 · 인샘플 ensemble_auc 0.5434)"
    it["counterfactual"] = "배포 챔피언(app/models/champion) — 같은 런 재측정, CG45 실측 0.5163"
    it["counterfactual_value"] = 0.5163
    it["baseline"] = {
        "value": 0.5163,
        "source": ("CG45 실측 — 같은 프로토콜(학습도메인 정합 유니버스·겹침 창 제외·rel h5·"
                   "5폴드×10일·60종목)의 배포 챔피언 OOS 견고 AUC"),
    }
    it["command"] = CMD
    it["check"] = "python3 scripts/model_engineer_cycle.py --status"
    it["check_target"] = {"op": ">=", "value": 1}
    it["success"] = (
        "챌린저 견고 AUC − 챔피언 0.5163 ≥ +0.02 (같은 프로토콜·같은 3창) → 승격 검토 대상. "
        "미달이면 '현 생산 경로 재학습은 챔피언보다 낫지 않다'로 기록하고 기준선 교체 승인 근거에만 쓴다."
    )
    it["cost"] = "약 10분 (CG45 실측 4.6분/arm × 2 arm) — 컨테이너 timeout 3600s/arm"
    it["note"] = NOTE
    it["setup_needed"] = None
    it["setup_cleared"] = "2026-10-01 03:0x — 챌린저(champion_cand)·프로토콜(CG45)·판정기(짝 경로 신설, _judge_baseline_paired_test 14/14 PASS) 모두 준비됨"

    with open(BACKLOG, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")

    pend = [i["id"] for i in items if i.get("status") == "pending"]
    print("CG33 → pending. pending 목록:", pend)


if __name__ == "__main__":
    main()

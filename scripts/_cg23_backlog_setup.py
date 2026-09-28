#!/usr/bin/env python3
"""_cg23_backlog_setup.py — CG23(표본 가중 축) 를 needs_setup → pending 으로 승격한다.

배경(2026-09-29 04:2x): 라벨 꼬리 축이 종료(CG21/22/24)되고 실행 가능한 pending 이 없어
표본 가중 축(CG23)의 셋업을 실제로 구현했다:
  ① services/xgboost-ml/app/models/{xgboost,lightgbm,catboost,ensemble}_model.py —
     sample_weight 인자 추가(기본 None = 기존 동작 무변경)
  ② scripts/wf_label_sweep.py — make_weights() + train_seed_weighted() (w=None 이면 원본 호출)
  ③ config 16종(WDn/WDu/WDw_h60 × 5 서로소 구간 + 150종목 기준점)
  ④ 회귀 테스트 scripts/_sample_weight_test.py — 11/11 PASS (컨테이너 실측):
     · w=None 경로가 원본 ml.train_seed 와 AUC 비트 동일
     · 균등 가중 == 무가중 (폴드별 max|Δ| = 0.000000000000) → 가중 배관이 AUC 를 바꾸지 않음
     · 시간 감쇠 != 균등 (max|Δ| 0.0108, 가중 min 0.690/max 1.412/유니크 63) → 가중이 모델에 도달

사용: python3 scripts/_cg23_backlog_setup.py <BACKLOG_JSON>
"""
import json
import sys

SLICES = [(0, 30), (30, 60), (60, 90), (90, 120), (120, 150)]
IDS = []
for a, b in SLICES:
    IDS += [f"WDn_{a:02d}_{b}", f"WDu_{a:02d}_{b}", f"WDw_h60_{a:02d}_{b}"]
IDS.append("CO_core30_150")

CMD = ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 timeout 3600 "
       "python -u scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_150u.npz "
       "--folds 5 --seeds 5 --only " + ",".join(IDS) + "'")

PAIRS = [[f"WDw_h60_{a:02d}_{b}", f"WDn_{a:02d}_{b}"] for a, b in SLICES]
PAIRS_U = [[f"WDw_h60_{a:02d}_{b}", f"WDu_{a:02d}_{b}"] for a, b in SLICES]
PAIRS_PLUMB = [[f"WDu_{a:02d}_{b}", f"WDn_{a:02d}_{b}"] for a, b in SLICES]

NOTE = (
    "준비 완료(2026-09-29 04:2x, 이 역할 직접 구현·검증): ① sample_weight 인자를 모델 3종 + "
    "EnsembleModel 에 추가(기본 None → 프로덕션 경로 무변경) ② wf_label_sweep 에 make_weights()"
    "(uniform/time_decay hl/absret) + train_seed_weighted()(w=None 이면 원본 ml.train_seed 그대로 호출) "
    "③ config 16종을 CONFIGS 에 직접 등록(별도 _add_*.py 생성기 대신 — 기존 config 관례와 동일) "
    "④ 회귀 테스트 `docker exec stock_xgboost_ml python /app/scripts/_sample_weight_test.py` → **11/11 PASS**: "
    "w=None 이 원본과 AUC 비트 동일 · **균등 가중(WDu) == 무가중(WDn) 폴드별 max|Δ|=0.000000000000** "
    "(가중 배관이 AUC 를 흔들지 않음 = WDw Δ 귀속 가능) · 시간감쇠 != 균등(max|Δ| 0.0108, 가중 min 0.690 "
    "max 1.412 유니크 63 = 가중이 실제로 모델에 도달). 판정은 구동기 `pairs`(WDw_h60_slice − WDn_slice, "
    "5구간 짝 Δ 평균 ≥ +0.02 그리고 양(+) ≥ 4/5)로만 한다. WDu 는 배관 통제(Δ≈0 기대)이고, "
    "CO_core30_150 은 150종목 같은 런 기준점이다. ⚠ 서로소 구간 짝 설계인 이유: CG13 실측에서 "
    "유니버스 교체만으로 폴드 평균이 Δ0.0287 움직였다 — 단일 arm vs 단일 대조군 비교는 금지."
)

def main(path):
    doc = json.load(open(path))
    items = doc["items"] if isinstance(doc, dict) else doc
    hit = None
    for it in items:
        if it.get("id") == "CG23":
            hit = it
            break
    if hit is None:
        raise SystemExit("CG23 을 찾지 못했다")
    hit["status"] = "pending"
    hit["priority"] = 1
    hit["command"] = CMD
    hit["est_minutes"] = 40
    hit["cost"] = ("16 config×5폴드×5시드=400 cell. 회귀 테스트 실측 2초/cell(CG24 250 cell 8.5분) "
                   "→ 유휴 약 15분·경쟁 시 약 40분. 컨테이너 timeout 3600.")
    hit["setup_needed"] = None
    hit["depends_on"] = ["CG21", "CG22", "CG24 (라벨 꼬리 축 종료)"]
    hit["success"] = ("구간 짝 Δ(WDw_h60 − WDn) 평균 ≥ +0.02 그리고 양(+) 구간 ≥ 4/5 → 신호. "
                      "미달이면 '표본 가중 축 소진'으로 기록하고 다음 레버로 이동한다.")
    hit["pairs"] = PAIRS
    hit["pairs_secondary"] = {"plumbing_vs_weight": PAIRS_U, "uniform_vs_none": PAIRS_PLUMB,
                              "note": "배관 통제 — uniform_vs_none 의 Δ 가 0 이 아니면 귀속 불가"}
    hit["note"] = (hit.get("note", "") + " | " + NOTE) if hit.get("note") else NOTE
    hit.setdefault("metric", "wf_sweep_summary")
    hit.setdefault("arm", "WDw_h60_00_30")
    hit.setdefault("counterfactual", "WDn_00_30")
    hit["updated"] = "2026-09-29T04:30:00+09:00"
    json.dump(doc, open(path, "w"), ensure_ascii=False, indent=2)
    print("CG23 →", hit["status"], "| priority", hit["priority"],
          "| est_minutes", hit["est_minutes"], "| config", len(IDS))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "docs/QUANT_MODEL_BACKLOG.json")

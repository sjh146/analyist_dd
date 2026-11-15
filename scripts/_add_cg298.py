#!/usr/bin/env python3
"""CG298 등록 — 서빙정합 3자 비교의 마지막 arm(champion_cand_cg166, rank_* 제거 학습).

CG297 실측: 배포 챔피언을 서빙 입력 그대로(rank_* 6개 0.0) 채점하면 0.5121±0.0214,
오프라인(rank_* 실값) 0.5321±0.0272 대비 Δ−0.0200(폴드 8/10 하회).
→ 서빙 조건에서 실제로 이기는 모델이 있는지가 남은 질문이다. cg166 은 rank_* 를
   아예 학습에서 제거(193피처)해 서빙과 학습 입력계가 일치하는 유일한 arm 이다.
"""
import json, sys

PATH = "/home/jhshi/analyist_dd/docs/QUANT_MODEL_BACKLOG.json"
ITEM = {
    "id": "CG298",
    "status": "pending",
    "priority": 1,
    "kind": "promotion",
    "owner": "engineer",
    "affects_model": True,
    "metric": "champion_robust_eval",
    "arm": "cg166_served",
    "title": "[서빙정합·3자] rank_* 제거 학습 모델(champion_cand_cg166, 193피처)을 서빙 조건 기준선과 같은 6폴드에서 채점 — 스큐 없는 모델이 배포 모델을 이기는가",
    "hypothesis": (
        "CG297 실측: 배포 챔피언은 서빙 입력(rank_* 6개 0.0 고정)에서 0.5121±0.0214 로, "
        "오프라인 0.5321±0.0272 대비 Δ−0.0200(10폴드 중 8폴드 하회) — 이 0.5121 이 트레이더가 실제로 받는 성능이다. "
        "CG166 은 서빙 불가 6개를 학습에서 제거한(193피처) 유일한 arm 이고, 서로 다른 두 프로토콜에서 "
        "0.5351±0.0100(3폴드)·0.5284(5폴드 감사) 로 측정됐다 — 둘 다 배포 챔피언의 서빙값 0.5121 보다 높다. "
        "그러나 같은 프로토콜·같은 폴드에서 직접 짝 비교한 적이 없으므로, 이 arm 이 승격 후보인지 여부는 아직 미결이다."
    ),
    "method": (
        "champion_robust_eval 로 champion_cand_cg166(193피처)을 CG295/CG297 과 동일 인자(rel·h5·10폴드×8일·200종목 "
        "training·seed0·asof 2026-11-13)로 채점하되 학습창 2026-07-10~2026-10-08 을 넘겨 겹치는 창(7~10월)을 제외한다 "
        "→ 2026-01-13~07-02 6폴드. 이어 fillable_topk_expectancy(stride 1·80세션·0.21%p)로 "
        "① 서빙정합 챔피언(cg297_norank_preds, rank 0.0) ② 오프라인 챔피언(cg295_champ_preds, rank 실값) 양쪽과 짝 비교."
    ),
    "counterfactual": (
        "서빙정합 배포 챔피언(cg297_norank, 같은 6폴드 부분집합) · 오프라인 배포 챔피언(cg295_champ, 같은 6폴드 0.5321 계열)"
    ),
    "command": (
        "docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 5400 python -u scripts/champion_robust_eval.py "
        "--model-dir app/models/champion_cand_cg166 --label-kind rel --horizon 5 --folds 10 --dates-per-fold 8 --stocks 200 "
        "--universe training --universe-seed 0 --train-start 2026-07-10 --train-end 2026-10-08 --asof-date 2026-11-13 "
        "--dump-preds /app/reports/overnight/cg298_cg166_preds.jsonl --dump-tag cg298_cg166 --dump-all "
        "--out /app/reports/overnight/cg298_cg166_served.json' && "
        "docker exec stock_xgboost_ml sh -c 'cd /app && python scripts/fillable_topk_expectancy.py "
        "--arm-jsonl /app/reports/overnight/cg298_cg166_preds.jsonl --arm-tag cg298_cg166 "
        "--control-jsonl /app/reports/overnight/cg297_norank_preds.jsonl --control-tag cg297_norank "
        "--k 3,5,10 --exit close_h --horizon 5 --stride 1 --json-out /app/reports/overnight/cg298_money_vs_served.json'"
    ),
    "summary_out": "/app/reports/overnight/cg298_cg166_served.json",
    "success": (
        "① 공통 6폴드 짝 ΔAUC(cg166 − 서빙정합 챔피언) ≥ +0.02 이고 폴드 승률 ≥ 5/6 이면 승격 후보로 등록 "
        "(champion_promote --dry-run --require-expectancy 는 별도 판단 · 최종 승격은 사람). "
        "② ΔAUC < +0.02 면 '스큐 제거만으로는 이득 없음'으로 배포정합 축을 닫고 데이터 축으로 이동. "
        "돈은 k3/k5 Δ ≥ +0.1%p & t ≥ 2 & 분할 both_positive 일 때만 신호로 인정 — 미달이면 '노이즈'로 명기."
    ),
    "expected": (
        "ΔAUC +0.01~+0.03 (cg166 은 학습 입력계가 서빙과 같아 CG297 의 −0.0200 페널티를 받지 않는 반면, "
        "학습창이 2026-07~10 이라 1~6월 폴드는 시장 국면이 달라 불리하다) — 어느 쪽이든 승격 판단의 근거가 남는다."
    ),
    "cost": "채점 1회(6폴드×8일×200종목, 학습 0) + 돈 비교 ≈ 30~40분",
    "est_minutes": 45,
    "depends_on": ["CG297", "CG166"],
    "panel": "champion_robust_eval 200종목 training 유니버스(seed0) · 학습창 2026-07-10~2026-10-08 · asof 2026-11-13",
    "created_at": "2026-11-16T03:05:00+09:00",
    "updated_at": "2026-11-16T03:05:00+09:00",
    "note": (
        "2026-11-16 03:0x 자율(장외 틱) 신설. 계기: CG297 이 '배포 모델의 서빙 성능 = 0.5121' 을 확정했고, "
        "CG166 arm 은 서빙 스큐가 구조적으로 0 인 유일한 모델인데 아직 같은 프로토콜로 채점된 적이 없다. "
        "⚠ 승격 루프가 아니다 — 결과가 좋아도 champion_promote --dry-run + 사람 승인이 별도다. "
        "⚠ 학습창 겹침 제외를 빼면 7~10월 폴드가 in-sample 이 되어 cg166 AUC 가 부풀려진다(CG296 과 같은 이유)."
    ),
    "attempts": [],
}

doc = json.load(open(PATH))
ids = [i["id"] for i in doc["items"]]
if "CG298" in ids:
    print("already registered")
    sys.exit(0)
doc["items"].append(ITEM)
doc["updated_at"] = "2026-11-16T03:05:00+09:00"
if isinstance(doc.get("updated"), str):
    doc["updated"] = "2026-11-16T03:05:00+09:00"
json.dump(doc, open(PATH, "w"), ensure_ascii=False, indent=2)
print("registered CG298; total items", len(doc["items"]))

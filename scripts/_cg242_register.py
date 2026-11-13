#!/usr/bin/env python3
"""CG242 신설 — CG240(초과수익 라벨) arm 의 **돈 지표** 측정(최우선 규칙: AUC 만으로 판정하지 않는다)."""
import json
import sys

P = "docs/QUANT_MODEL_BACKLOG.json"
RUN = "/app/reports/overnight/cg240_money_run.jsonl"
PRED = "/app/reports/overnight/cg240_preds.jsonl"
MONEY = "/app/reports/overnight/cg240_money.json"
CMD = (
    "docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 1500 "
    "python -u scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_200liq_fresh.npz "
    "--asof-date 2026-11-02 --folds 5 --seeds 5 --only CO_smooth_d1_h5,CG240_ex_h5 "
    f"--out {RUN} --dump-preds {PRED} && OMP_NUM_THREADS=2 timeout 900 "
    f"python -u scripts/fillable_topk_expectancy.py --arm-jsonl {PRED} --arm-tag CG240_ex_h5 "
    f"--control-jsonl {PRED} --control-tag CO_smooth_d1_h5 --k 3,5,10 --exit close_h "
    f"--horizon 5 --json-out {MONEY}'"
)

doc = json.load(open(P, encoding="utf-8"))
items = doc["items"] if isinstance(doc, dict) else doc
if any(i.get("id") == "CG242" for i in items):
    sys.exit("CG242 이미 있음")

item = {
    "id": "CG242",
    "status": "pending",
    "priority": 1,
    "kind": "experiment",
    "owner": "engineer",
    "affects_model": True,
    "title": "[돈 지표·라벨 축] CG240 초과수익 라벨 arm 의 체결성 순기대 — AUC +0.0072 가 돈이 되는가",
    "metric": "fillable_topk_expectancy",
    "arm": "CG240_ex_h5",
    "baseline": "CO_smooth_d1_h5 (같은 런·같은 앵커·같은 시드, 절대수익 smooth 분위 라벨)",
    "command": CMD,
    "summary_out": MONEY,
    "counterfactual": ("CO_smooth_d1_h5 — 같은 패널·앵커·폴드·시드, 라벨만 절대수익 분위(q0.30). "
                       "체결성 필터(상한가 제외·거래대금 10억·일변동 25%) ON, 수수료 왕복 0.21%p 차감."),
    "hypothesis": ("CG240 실측(2026-11-13 17:16): 초과수익 라벨 arm 이 앵커 3개 전부 양(+) "
                   "Δ +0.0049/+0.0069/+0.0098 = 평균 +0.0072±0.0020, 폴드승률 11/15 로 "
                   "사전문턱(+0.02) 미달이지만 **부호일관·앵커std 초과**했다. 라벨을 돈 지표와 같은 "
                   "초과 축으로 옮긴 것이므로, AUC 문턱 미달이어도 top-k 체결 순기대가 대조군보다 "
                   "높은지가 최우선 규칙(실험은 돈 지표로 보고)의 판정이다."),
    "success": ("k=3/5/10 중 주 지표(top-k, close_h)에서 arm 순기대 − 대조군 순기대 ≥ +0.1%p & "
                "분할표본 앞/뒤 절반 both_positive & t ≥ 2 → 돈 신호. "
                "Δ < +0.1%p 또는 분할 불안정 → '초과 라벨은 AUC 소폭·돈 무개선'으로 축 종결. "
                "rc≠0 → 실행실패(노이즈로 쓰지 않는다)."),
    "expected": ("효과 기대 낮음 — AUC Δ+0.0072 는 문턱의 36%. 다만 방향이 3/3 일관이므로 "
                 "돈 지표로 교차확인할 가치가 있다(대조군 top-k 순기대는 과거 실측에서 음수)."),
    "cost": "낮음(2 arm × 5폴드 × 5시드 = 50 cell ≈ 4분 + 체결성 계산 수십 초)",
    "est_minutes": 8,
    "from_experiment": "CG240",
    "created_at": "2026-11-13T17:22:00+09:00",
    "note": ("2026-11-13 17:2x 엔지니어 자율(장외 틱). CG240 은 AUC 계측기(protocol_pair_anchor)라 "
             "판정 delta=None 으로 스코어보드 무개선 카운터에 영향이 없다 — 돈 지표가 별도로 필요하다. "
             "⚠ 승격 아님: 배포 판단은 champion_promote --require-expectancy 통과 후 별건."),
    "attempts": [],
}
items.append(item)
json.dump(doc, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("CG242 pending 등록 · est_minutes", item["est_minutes"])

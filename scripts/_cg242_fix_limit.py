#!/usr/bin/env python3
"""CG242 재실행 준비 — panel meta(limit 200)와 요청 limit 을 일치시켜 '측정무효' 판정을 없앤다."""
import json

P = "docs/QUANT_MODEL_BACKLOG.json"
RUN = "/app/reports/overnight/cg240_money_run.jsonl"
PRED = "/app/reports/overnight/cg240_preds.jsonl"
MONEY = "/app/reports/overnight/cg240_money.json"
CMD = (
    "docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 1500 "
    "python -u scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_200liq_fresh.npz "
    "--limit 200 --asof-date 2026-11-02 --folds 5 --seeds 5 --only CO_smooth_d1_h5,CG240_ex_h5 "
    f"--out {RUN} --dump-preds {PRED} && OMP_NUM_THREADS=2 timeout 900 "
    f"python -u scripts/fillable_topk_expectancy.py --arm-jsonl {PRED} --arm-tag CG240_ex_h5 "
    f"--control-jsonl {PRED} --control-tag CO_smooth_d1_h5 --k 3,5,10 --exit close_h "
    f"--horizon 5 --json-out {MONEY}'"
)

doc = json.load(open(P, encoding="utf-8"))
items = doc["items"] if isinstance(doc, dict) else doc
it = [i for i in items if i.get("id") == "CG242"][0]
it["status"] = "pending"
it["command"] = CMD
it["est_minutes"] = 8
it["note"] += (" | 2026-11-13 17:2x 1차 실행 rc=0 이었으나 구동기 판정 **측정무효** — "
               "패널 meta(limit 200)와 요청 limit(기본 50) 불일치 경고. 같은 200종목 패널에서 두 arm 이 "
               "같은 조건으로 돌아 짝 비교 자체는 성립하나, 규칙상 측정무효를 그대로 남기지 않는다: "
               "`--limit 200` 을 붙여 재실행해 유효 판정을 받는다. 참고(1차 수치, fillable close_h h5 · "
               "225세션): k=3 arm +0.3046%p vs 대조 +0.8344%p (짝 Δ -0.5298, t -1.0) · k=5 +0.0993 vs "
               "+0.7806 (Δ -0.6812) · k=10 +0.3984 vs +1.2017 (Δ -0.8033) · 널(풀평균) +0.501%p — "
               "arm 이 세 k 모두 널·대조보다 낮다.")
json.dump(doc, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("CG242 command 갱신(--limit 200) · status", it["status"])

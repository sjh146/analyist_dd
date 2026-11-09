"""CG180 metric kind 배선 자체검증 — 합성 요약으로 경로·파서·판정기를 확인한다.

왜 필요한가: 새 metric kind 를 배선할 때 오타 하나로 사이클이 '기록 없이 죽고',
그 실패가 원장에 실행실패로만 남으면 원인 규명에 한 사이클을 더 쓴다(2026-10-30 사례).
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, "/home/jhshi/analyist_dd/scripts")
import model_engineer_cycle as m  # noqa: E402

tmp = tempfile.mkdtemp()
out = os.path.join(tmp, "cg180_audit.json")
json.dump({
    "meta": {"generated_at": "2026-11-10T00:30:00"},
    "b_robust_auc": {"champ": {
        "robust_auc": 0.5234,
        "folds": [0.5101, 0.5367, 0.4989, 0.5402, 0.5311],
        "fold_detail": [{"window": ["2026-10-02", "2026-10-15"], "auc_mean": 0.5101, "n_dates": 10}],
        "auc_pooled": 0.5211, "train_start": "2026-07-03", "train_end": "2026-10-01",
        "windows_excluded": [["2026-04-01", "2026-10-01"]]}},
    "c_net_expectancy": {"champ": {"avg_pct": -0.31, "t_stat": -1.2, "n_sessions": 40,
                                   "n_trades": 120, "halves": {"first": -0.25, "second": -0.37}}},
}, open(out, "w", encoding="utf-8"))

item = {"id": "CG180", "metric": "robust_oos_audit", "model_label": "champ",
        "command": ("docker exec stock_xgboost_ml sh -c 'cd /app && timeout 5400 "
                    "sh scripts/cg180_rebaseline.sh --out /app/reports/overnight/cg180_audit.json'"),
        "counterfactual": "2026-10-04 3폴드 기준선 fold_mean 0.5148±0.0344"}

sp = m.summary_path("robust_oos_audit", item["command"])
assert sp.endswith("services/xgboost-ml/reports/overnight/cg180_audit.json"), sp
print("경로 OK:", sp)

parsed = m.parse_robust_oos_audit(out, 0.0, label="champ")
assert parsed["robust_auc"] == 0.5234, parsed
assert parsed["fold_means"] == [0.5101, 0.5367, 0.4989, 0.5402, 0.5311]
assert parsed["auc_std_across_folds"] is not None
print("파서 OK:", parsed["robust_auc"], "±", parsed["auc_std_across_folds"], "folds", len(parsed["fold_means"]))

verdict, detail, delta = m.judge_by_metric(item, parsed)
print("판정:", verdict)
print("상세:", detail)
assert verdict == "기준선 실측", verdict
assert "순기대" in detail
print("PASS")

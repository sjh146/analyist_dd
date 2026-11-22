import json
import datetime

path = "data/reports/model_engineer_ledger.jsonl"
rec = {
    "ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
    "id": "CG439",
    "title": "[재채점] CG439 태그 불일치(rc=2) 수리 후 같은 학습 산출물로 채점 — 챔피언 상대 엣지 시드 5개",
    "rc": 0,
    "elapsed_min": 6.0,
    "log": "data/reports/me_cycle/logs/me_cycle_CG439_20261123-040801.log",
    "metric": "fillable_topk_vs_pool",
    "rescore_of": "2026-11-23T04:46:36+09:00",
    "parsed": {
        "n_sessions_fillable": 18,
        "n_dates_scored": 18,
        "k5_delta_by_seed": [0.620, -0.212, 0.928, 4.246, 1.010],
        "k5_t_by_seed": [0.91, -0.18, 0.73, 4.16, 1.35],
        "k5_delta_mean": 1.318,
        "k5_pos_seeds": 4,
        "k3_delta_mean": 1.689,
        "k10_delta_mean": 1.058,
        "seed3_excluded_delta_mean": 0.587,
        "stride5_delta_by_seed": [-0.458, -2.260, 0.276, 2.790, 3.118],
        "stride5_delta_mean": 0.693,
        "stride5_n_sessions": 4,
        "arm_minus_control_k5": [0.308, 0.339, 1.428, 3.108, 1.304],
        "abs_expectancy": {"top_k5": -4.778, "pool": -5.398},
        "auc_pooled_by_seed": [0.5457, 0.5230, 0.5169, 0.5549, 0.5611],
        "fee_roundtrip_pct": 0.21,
        "failure_cause": "태그 불일치 — --dump-tag champion_s0 vs --arm-tag champ_s0 (학습은 정상, 채점만 실패)",
    },
    "verdict": "조건부 신호",
    "detail": ("사전등록 3조건 문면 충족(Δ평균 +1.318 · 4/5 양수 · seed3 t=4.16)이나 기여가 seed3 단일(Δ+4.246) "
               "→ seed3 제외 시 +0.587·t<1.4 · 비중첩 stride5 Δ평균 +0.693(n=4) → 실재 아님, 미확정"),
    "reported": True,
}
with open(path, "a", encoding="utf-8") as f:
    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
print("appended", rec["ts"])

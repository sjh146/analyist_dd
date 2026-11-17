#!/usr/bin/env python3
"""CG324/CG325 원장 기록(2026-11-18 06:2x) — 수동 측정(구동기 밖에서 내가 직접 실행)이므로 직접 남긴다.

값은 요약 JSON(cg324_summary · cg324_money · cg325_summary)에서 읽은 실측이다.
"""
import json

LEDGER = "data/reports/model_engineer_ledger.jsonl"
TS = "2026-11-18T06:26:00+09:00"

rec_cg324 = {
    "ts": TS, "id": "CG324", "rc": 0, "elapsed_min": 4.0,
    "log": "data/reports/me_cycle/logs/cg324_run.log",
    "metric": "wf_sweep_summary",
    "parsed": {
        "metric_name": "wf_sweep_summary",
        "summary_out": "/app/reports/overnight/cg324_summary.json",
        "panel": "panel_420_tv.npz (49종목·281거래일·214피처)",
        "folds": {"LS_quant_q30_h5": [0.5171, 0.5201, 0.5757, 0.5926, 0.5081],
                  "CG324_maskq_h5": [0.4912, 0.5451, 0.5222, 0.6798, 0.4616]},
        "means": {"LS_quant_q30_h5": 0.5427, "CG324_maskq_h5": 0.5400},
        "paired_delta": -0.0027, "paired_delta_std": 0.0555, "fold_wins": "2/5",
        "money": {"file": "/app/reports/overnight/cg324_money.json",
                  "k3": {"arm": 0.934, "ctl": 1.503, "delta": -0.569, "t": -1.07},
                  "k5": {"arm": 0.681, "ctl": 0.869, "delta": -0.188, "t": -0.50},
                  "null_pool_pct": 0.675, "sessions": 230},
    },
    "verdict": "노이즈",
    "detail": ("라벨 기준 모집단(체결가능 행) 축 종결: 짝 Δ −0.0027(승 2/5 · Δstd 0.0555) · "
               "돈 k3 −0.569%p(t −1.07) · k5 −0.188(t −0.50) · 널 풀평균 +0.675%/세션. "
               "마스크는 실제로 물었다(keep 27.4% · 문턱 바뀐 날짜 276/276 · 라벨 뒤집힘 1.1%) — "
               "다만 액체 200종목 패널에서는 keep 97.6%로 no-op(라벨 뒤집힘 0.0%)."),
    "reported": True,
}

rec_cg325 = {
    "ts": TS, "id": "CG325", "rc": 0, "elapsed_min": 4.5,
    "log": "data/reports/me_cycle/logs/cg325_run.log",
    "metric": "wf_sweep_summary",
    "parsed": {
        "metric_name": "wf_sweep_summary",
        "summary_out": "/app/reports/overnight/cg325_summary.json",
        "folds": {"LS_quant_q30_h5": [0.5171, 0.5201, 0.5757, 0.5926, 0.5081],
                  "CG325_seedA_h5": [0.5191, 0.5183, 0.5683, 0.5974, 0.5059],
                  "CG325_seedB_h5": [0.5104, 0.5193, 0.5689, 0.5812, 0.5059]},
        "means": {"LS_quant_q30_h5": 0.5427, "CG325_seedA_h5": 0.5418, "CG325_seedB_h5": 0.5371},
        "seed_noise_delta": {"A": -0.0009, "B": -0.0056, "delta_std": [0.0046, 0.0042], "se": 0.002},
    },
    "verdict": "문턱 유효(시드 잡음 |Δ| ≤ 0.0056)",
    "detail": ("같은 config·같은 패널·같은 폴드에서 시드만 100~104/200~204 로 바꾼 Δ = −0.0009(승 2/5) · "
               "−0.0056(승 0/5), Δstd 0.0045 · SE 0.002 → 사전문턱 +0.02 는 시드 잡음의 3.6배로 유효. "
               "단 라벨·피처가 바뀌는 arm 은 폴드 Δstd 0.0555(SE 0.0248) → 5폴드 단일 런의 +0.02~0.03 은 "
               "1 SE 안쪽(=신호로 읽을 수 없음). 프로토콜(문턱·폴드 수) 변경은 ② 승인 대상으로 올림."),
    "reported": True,
}

with open(LEDGER, "a") as f:
    for r in (rec_cg324, rec_cg325):
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

print("appended 2 ledger entries")
n = sum(1 for l in open(LEDGER) if l.strip())
print("ledger lines:", n)

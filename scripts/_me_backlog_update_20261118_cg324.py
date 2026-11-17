#!/usr/bin/env python3
"""CG324/CG325 결과 기록(2026-11-18 06:2x 엔지니어 자율 틱).

- CG324(라벨 기준모집단 마스크) 를 backlog → done 으로 닫고 실측·판정·구현 메모를 남긴다.
- CG325(시드 잡음 바닥) 를 신규 done 항목으로 추가한다(같은 틱에서 측정 완료).

수치는 /app/reports/overnight/cg324_summary.json · cg324_money.json · cg325_summary.json 에서
직접 읽은 값이다(자기신고 로그 문구 사용 금지).
"""
import json
import os

P = "docs/QUANT_MODEL_BACKLOG.json"
d = json.load(open(P))
items = d["items"]
by = {i["id"]: i for i in items}

TS = "2026-11-18T06:25:00+09:00"

# ── CG324 ──────────────────────────────────────────────────────────────────────
cg324 = by["CG324"]
cg324.update({
    "status": "done",
    "priority": 1,
    "kind": "라벨 축",
    "owner": "엔지니어",
    "affects_model": True,
    "metric": "wf_sweep_summary",
    "est_minutes": 25,
    "command": (
        "docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=3 timeout 3300 python -u "
        "scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_420_tv.npz --folds 5 --seeds 5 "
        "--only LS_quant_q30_h5,CG324_maskq_h5 --dump-preds /app/reports/overnight/cg324_preds.jsonl "
        "--out /app/reports/overnight/cg324_sweep.jsonl "
        "--summary-out /app/reports/overnight/cg324_summary.json'"
    ),
    "counterfactual": (
        "LS_quant_q30_h5 (같은 패널·같은 폴드·같은 시드·게이트 OFF top30) — 차이는 **라벨 기준 모집단 "
        "하나**(전행 분위 vs 체결가능 행 분위)"
    ),
    "success": (
        "사전등록: 같은 런 짝 Δ(마스크분위 − 전행분위) 폴드 평균 ≥ +0.02 AND 폴드승률 ≥ 4/5 AND "
        "체결가능 순기대(수수료 0.21%p·close_h h5)가 대조군보다 높을 때만 신호. 미달이면 라벨 축 종결."
    ),
    "result": {
        "verdict": "노이즈",
        "detail": (
            "5폴드×5시드 · panel_420_tv(49종목·281거래일·225→214피처) · 행 8,184. "
            "대조군 LS_quant_q30_h5 0.5427±0.0385 [0.5171,0.5201,0.5757,0.5926,0.5081] vs "
            "CG324_maskq_h5 0.5400±0.0843 [0.4912,0.5451,0.5222,0.6798,0.4616] → "
            "짝 Δ 평균 **−0.0027** (폴드별 [−0.0258,+0.0250,−0.0535,+0.0872,−0.0465] · 승 2/5 · "
            "Δstd 0.0555 · SE 0.0248 → t≈−0.11). 사전문턱 +0.02 대비 방향도 음(−)."
        ),
        "money": {
            "k3": {"arm": 0.934, "ctl": 1.503, "delta": -0.569, "t": -1.07},
            "k5": {"arm": 0.681, "ctl": 0.869, "delta": -0.188, "t": -0.50},
            "null_pool": {"pct_per_session": 0.675, "t": 1.44, "sessions": 230},
            "note": "필터 ON(당일등락<+25%·거래대금 10억↑) · close_h h5 · 수수료 왕복 0.21%p — 두 k 모두 음(−)"
        },
        "mask_measured": {
            "keep_frac_panel_420_tv": 0.2735,
            "keep": 3722,
            "rows": 13609,
            "daychg_dropped": 66,
            "tv_below_1e9_dropped": 9838,
            "dates_threshold_changed": "276/276 (100%)",
            "hi_shift": 0.01203,
            "lo_shift": -0.01599,
            "label_flip_rate": 0.011,
            "selftest": "5/5 PASS (scripts/_cg324_label_mask_test.py)",
        },
        "liquid200_noop": (
            "panel_200liq_ffill_2605_ss_tv(200종목·112거래일) 에서는 keep_frac **0.9756**"
            "(거래대금 10억 미만이 2.4%뿐) → 문턱 이동 hi −0.00031 · lo +0.00068, 라벨 뒤집힘 **0.0%** "
            "= 사실상 no-op. 즉 이 레버는 '체결성 필터가 실제로 무는' 소형·비유동 유니버스에서만 존재한다."
        ),
        "rc": 0,
        "artifacts": [
            "/app/reports/overnight/cg324_summary.json",
            "/app/reports/overnight/cg324_money.json",
            "/app/reports/overnight/cg324_preds.jsonl",
        ],
    },
    "finished_at": TS,
})
cg324["note"] = (
    (cg324.get("note") or "").rstrip()
    + " | 결론(2026-11-18 06:2x): 라벨 기준 모집단 축 **종결** — 짝 Δ −0.0027(승 2/5) · 돈 k3 −0.57%p(t −1.07)/"
    "k5 −0.19(t −0.50). 구현: wf_wave.make_labels(label_mask=) 신설(quantile 경로 전용, None 이면 비트 동일) + "
    "wf_label_sweep.build_label_mask()·cfg 'label_mask' 배선 + config CG324_maskq_h5. 자체점검 "
    "scripts/_cg324_label_mask_test.py 5/5 PASS. 패널은 tv_log 가 있는 panel_420_tv 를 썼다"
    "(액체 200종목 패널에서는 마스크가 2.4%만 제외 = no-op 이라 검정력 0). "
    "⚠ 부수 실측: 라벨이 바뀌는 arm 은 폴드 Δstd 가 0.0555(SE 0.0248)로 커진다 → 5폴드 단일 런의 "
    "평균 Δ ±0.02 는 1 SE 안쪽(CG325 참조)."
)

# ── CG325 (신규, 같은 틱에서 측정 완료) ────────────────────────────────────────
cg325 = {
    "id": "CG325",
    "status": "done",
    "priority": 1,
    "kind": "측정정합성",
    "owner": "엔지니어",
    "affects_model": False,
    "metric": "wf_sweep_summary",
    "title": (
        "[측정정합성] 시드 잡음 바닥 — 같은 config·같은 패널·같은 폴드에서 **시드만** 바꾼 두 arm 의 "
        "짝 Δ 분포는 얼마인가 (사전문턱 +0.02 는 잡음 위에 서 있는가)"
    ),
    "hypothesis": (
        "이 프로토콜의 판정 문턱 Δ ≥ +0.02 는 폴드 std ±0.03 을 전제로 정해졌는데, '같은 설정을 다시 "
        "돌리면 Δ 가 얼마나 흔들리는가'는 한 번도 측정되지 않았다. CG319/320/322 는 절대 AUC 바닥(+0.011)만 "
        "쟀고 Δ 바닥은 안 쟀다 → 문턱이 잡음 안쪽이면 지난 19사이클의 Δ 판정도 재해석해야 한다."
    ),
    "counterfactual": "LS_quant_q30_h5 (시드 0~4) — 코드·데이터·폴드·HP 동일, 시드만 다름",
    "command": (
        "docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=3 timeout 1500 python -u "
        "scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_420_tv.npz --folds 5 --seeds 5 "
        "--only LS_quant_q30_h5,CG325_seedA_h5,CG325_seedB_h5 "
        "--out /app/reports/overnight/cg325_sweep.jsonl "
        "--summary-out /app/reports/overnight/cg325_summary.json'"
    ),
    "success": (
        "사전등록: |Δ| 최대값이 +0.02 미만이면 '문턱은 시드 잡음 위에 있다'(Δ 판정 유효). 0.02 이상이면 "
        "'문턱이 잡음 안쪽'으로 기록하고 프로토콜(폴드/시드 수·문턱) 변경을 ② 승인 항목으로 올린다."
    ),
    "expected": "시드 잡음은 폴드 std 보다 훨씬 작다(같은 데이터·같은 폴드이므로) → |Δ|<0.01 예상",
    "cost": "3 arm × 5폴드 × 5시드 ≈ 5분",
    "est_minutes": 5,
    "result": {
        "verdict": "문턱 유효(시드 잡음 |Δ| ≤ 0.0056)",
        "detail": (
            "같은 config·같은 패널·같은 폴드: 기준(시드 0~4) 0.5427±0.0385. "
            "A(시드 100~104) 0.5418±0.0351 → 짝 Δ 평균 **−0.0009** (폴드별 [+0.0020,−0.0018,−0.0074,"
            "+0.0048,−0.0022] · 승 2/5 · Δstd 0.0046 · SE 0.0021). "
            "B(시드 200~204) 0.5371±0.0315 → **−0.0056** (폴드별 [−0.0067,−0.0008,−0.0068,−0.0114,−0.0023] · "
            "승 0/5 · Δstd 0.0042 · SE 0.0019). → 시드만 바꾼 Δ 는 |Δ| ≤ 0.006 으로 사전문턱 +0.02 의 "
            "약 30% — 문턱은 시드 잡음 **위에** 있다(Δ 판정 유효)."
        ),
        "caveat": (
            "단 B 가 0/5(전 폴드 음)로 방향이 일관 — 5시드 평균으로도 시드집합 선택이 −0.006 수준의 "
            "계통 오차를 만든다(무작위 잡음이 아니라 '시드집합 효과'). 그래서 Δ 판정은 반드시 같은 시드 "
            "집합에서 짝으로 재야 하고, +0.02 문턱은 이 계통 오차의 3.6배라 안전하다."
        ),
        "delta_fold_noise": (
            "라벨·피처가 바뀌는 arm 은 폴드 Δstd 가 커진다(CG324 실측 0.0555 · SE 0.0248). 즉 5폴드 단일 런의 "
            "평균 Δ 는 ±0.025 수준의 표준오차를 갖는다 → 관측 Δ 가 +0.02~+0.03 이면 **1 SE 안쪽**이라 "
            "그 자체로는 신호로 읽을 수 없다(시드 잡음이 아니라 폴드 잡음이 지배)."
        ),
        "rc": 0,
        "artifacts": ["/app/reports/overnight/cg325_summary.json", "/app/reports/overnight/cg325_sweep.jsonl"],
    },
    "note": (
        "2026-11-18 06:2x 엔지니어 자율(장외 틱 · pending 0건). 코드 변경은 seed_offset 배선 1곳"
        "(wf_label_sweep, 기본값 0 → 기존 경로 무변경) + config 2개. 왜 지금: CG324 결과가 노이즈였고, "
        "그 판정 자체가 '문턱 vs 잡음' 문제에 의존하기 때문. 다음 후속(승인 필요): 문턱·폴드 수 규칙 변경은 "
        "스코어보드 프로토콜 변경이라 ② 승인 대상."
    ),
    "created_at": TS,
    "finished_at": TS,
    "attempts": 1,
    "needs_approval": False,
}

items.append(cg325)
d["updated_at"] = TS
d["updated"] = TS

json.dump(d, open(P, "w"), ensure_ascii=False, indent=1)
print("updated", P, "items:", len(items))
print("CG324 status:", by["CG324"]["status"], "| CG325:", cg325["status"])

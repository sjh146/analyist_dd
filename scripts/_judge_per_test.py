#!/usr/bin/env python3
"""judge_per() 회귀 테스트 — 판정이 '자기신고/자기대조'로 오염되지 않는지 검증.

실측 근거(모두 실제 원장·백로그 수치):
  ① L1: h8 0.5068 vs h5 0.5406 → Δ−0.0338 악화 (winner 기준으로는 Δ0.0000 으로 오독됐던 사례)
  ② U1: 150종목 0.5140 vs 기록 기준선 0.5406(49종목) → Δ−0.0266.
     대조군 문자열의 첫 토큰이 arm 과 **같은 이름**(LS_quant_q30_h5)이라 자기대조 Δ0.0000
     '노이즈' 로 기록됐다 — 이 테스트가 그 회귀를 막는다.
  ③ L2: rel 0.5245 vs q30 0.5406 → Δ−0.0161 노이즈(문턱 미달은 노이즈로 명시)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_engineer_cycle as m  # noqa: E402

FAIL = 0


def per(name, mean, std=0.02, folds=None):
    return {name: {"mean": mean, "std": std,
                   "folds": folds or [mean - 0.01, mean, mean + 0.01],
                   "min": mean - 0.01, "max": mean + 0.01, "fold_win_rate": 0.6}}


def check(label, item, per_exp, want_verdict, want_delta):
    global FAIL
    v, d, delta = m.judge_per(item, per_exp)
    ok = (v == want_verdict) and (delta is not None and abs(delta - want_delta) < 5e-5)
    if want_delta is None:
        ok = (v == want_verdict) and delta is None
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: verdict={v} delta={delta}  ({d[:110]})")
    if not ok:
        FAIL += 1
        print(f"        기대: verdict={want_verdict} delta={want_delta}")


# ① U1 — 자기대조 금지, 기록 기준선 대조 (핵심 회귀)
check("U1 자기대조→기준선 비교",
      {"id": "U1", "arm": "LS_quant_q30_h5",
       "counterfactual": "LS_quant_q30_h5 (49종목 · panel_420_asofpatch.npz)",
       "baseline": {"value": 0.5406, "source": "L1 실측 · 49종목"}},
      per("LS_quant_q30_h5", 0.5140, 0.0201), "악화", -0.0266)

# ② L1 — 같은 런 안 대조군이 있으면 그 값과 비교(기준선 아님)
check("L1 동일 런 A/B (h8 vs h5)",
      {"id": "L1", "arm": "LS_quant_q30_h8", "counterfactual": "LS_quant_q30_h5",
       "baseline": {"value": 0.5406, "source": "기록"}},
      {**per("LS_quant_q30_h8", 0.5068), **per("LS_quant_q30_h5", 0.5406)},
      "악화", -0.0338)

# ③ L2 — 문턱 미달은 '노이즈' 로 명시
check("L2 노이즈 판정 (rel vs q30)",
      {"id": "L2", "arm": "LS_rel_h5", "counterfactual": "LS_quant_q30_h5"},
      {**per("LS_rel_h5", 0.5245), **per("LS_quant_q30_h5", 0.5406)},
      "노이즈", -0.0161)

# ④ arm 미지정 — 대조군을 제외한 최고 vs 대조군 (대조군이 최고면 Δ0 함정)
check("arm 미지정 → 대조군 제외 최고 vs 대조군",
      {"id": "RB1", "counterfactual": "LS_quant_q30_h5"},
      {**per("PO_timevary_h5", 0.5600), **per("LS_quant_q30_h5", 0.5400)},
      "신호있음", 0.0200)

# ④b arm 미지정이고 대조군이 최고 → Δ0 위장 금지
check("arm 미지정·대조군이 최고",
      {"id": "RB1b", "counterfactual": "LS_quant_q30_h5"},
      {**per("PO_timevary_h5", 0.5300), **per("LS_quant_q30_h5", 0.5400)},
      "노이즈", -0.0100)

# ④c 대조군만 측정 → 비교할 가설군 없음
check("대조군만 측정",
      {"id": "RB1c", "counterfactual": "LS_quant_q30_h5"},
      per("LS_quant_q30_h5", 0.5400), "기준선없음", None)

# ⑤ 자기대조인데 기준선도 없으면 '기준선없음' (Δ0 위장 금지)
check("자기대조 + 기준선 없음",
      {"id": "X", "arm": "A", "counterfactual": "A"},
      per("A", 0.51), "기준선없음", None)

# ⑥ 대조군이 이 런에 없고 기준선만 있으면 기준선 비교
check("대조군 미측정 + 기준선 존재",
      {"id": "X2", "arm": "A", "counterfactual": "B panel2", "baseline": {"value": 0.55}},
      per("A", 0.58), "신호있음", 0.0300)

# ⑦ 측정값 없음(빈 per) → 판정불가, Δ 없음
check("per_exp 비어 있음", {"id": "X3", "arm": "A"}, {}, "판정불가", None)

print(f"\n{'ALL PASS' if FAIL == 0 else f'{FAIL} FAILED'}")
sys.exit(1 if FAIL else 0)

#!/usr/bin/env python3
"""CG240 라벨 축 종결 기록 — AUC 소폭(+)·돈 음(−) → 축 종결(CG242 후속)."""
import json

P = "docs/QUANT_MODEL_BACKLOG.json"
doc = json.load(open(P, encoding="utf-8"))
items = doc["items"] if isinstance(doc, dict) else doc
by = {i["id"]: i for i in items}

c240 = by["CG240"]
c240["status"] = "done"
c240["note"] = c240.get("note", "") + (
    " | **2026-11-13 17:1x~17:29 종결(엔지니어 자율)** — 라벨 축 닫음. "
    "① 구현·자기검정 16/16 PASS(`scripts/_cg240_label_excess_test.py`: 기존 kind 5종 합성·실패널 비트동일, "
    "NaN 규약, 마지막날 전부 NaN). ② 설계 교훈: 'demean + 분위'는 **no-op**(날짜별 상수 이동 = 순서 보존 → "
    "excess_smooth 라벨 == smooth 라벨, 실패널 32,166행 비트동일) → arm 은 '당일 풀평균 초과(>=0)' 절대문턱으로 전환. "
    "③ AUC 실측(panel_200liq_fresh·5폴드×5시드·앵커 3): 앵커별 짝 Δ +0.0049/+0.0069/+0.0098 = "
    "**평균 +0.0072±0.0020, 부호일관 3/3, 폴드승률 11/15, 문턱통과 0/3 → 노이즈**(+0.02 미달). "
    "④ 돈 실측(CG242, 체결성 필터·수수료 0.21%p·225세션): k=3 Δ−0.530%p(t −1.0) · k=5 Δ−0.681 · k=10 Δ−0.803, "
    "arm 세 k 모두 **널(풀평균 +0.501%p) 미달** → '초과 라벨은 AUC 소폭이나 돈은 음(−)' = 축 종결. "
    "⑤ 부수: 라벨 행도메인이 53,497 vs 대조 32,166(양성률 0.4168 vs 0.30)로 넓어졌는데도 돈이 안 늘었다."
)
c240["result"] = {
    "verdict": "축 종결 — AUC 급내 +0.0072(문턱 +0.02 미달) · 돈 지표는 세 k 모두 대조·널보다 낮음",
    "auc": {"anchors": {"2026-11-02": 0.0049, "2026-10-27": 0.0069, "2026-10-20": 0.0098},
            "mean": 0.0072, "std": 0.0020, "sign_consistent": True, "fold_win": "11/15"},
    "money": {"protocol": "fillable close_h h5 · fee 0.21%p · 225세션 · 풀중앙값 193",
              "k3": {"arm": 0.3046, "ctl": 0.8344, "delta": -0.5298, "t": -1.0},
              "k5": {"arm": 0.0993, "ctl": 0.7806, "delta": -0.6812, "t": -1.36},
              "k10": {"arm": 0.3984, "ctl": 1.2017, "delta": -0.8033, "t": -1.9},
              "null_pool": 0.501},
}

c242 = by["CG242"]
c242["note"] = c242.get("note", "") + (
    " | 2026-11-13 17:29 2차 실행(`--limit 200`) rc=0 → **노이즈(돈)** 유효 판정: "
    "k=3 arm +0.305%p/세션 vs 대조 +0.834 (Δ −0.530, t −1.0, 양세션 52.0%, 분할 unstable [+3.201/−2.566]) · "
    "k=5 +0.099 vs +0.781 (Δ −0.681, t −1.36) · k=10 +0.398 vs +1.202 (Δ −0.803, t −1.9) · "
    "널 풀평균 +0.501%p. 필터 OFF 참고: k=3 +0.234%p (Δ −0.423). "
    "→ 사전문턱(Δ≥+0.1%p·t≥2·분할 both_positive) 미충족 + 부호 음(−) 이므로 '라벨 초과 축'은 종결."
)
json.dump(doc, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("CG240 done·축 종결 기록 · CG242 note 갱신 · CG242 status=", c242.get("status"))

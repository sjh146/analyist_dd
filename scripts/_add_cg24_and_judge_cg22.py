#!/usr/bin/env python3
"""CG21·CG22 판정 기록 + 라벨 축 결론 + 다음 가설(CG24) 등록 (2026-09-29 03:2x, 1회성).

실측 요약:
  CG21(1.8분, panel_150u·게이트 ON·5폴드×3시드) — AUC CO_q05_h5 0.5586±0.0359 vs CO_core30_h5
    0.5104±0.0141 = Δ+0.0482 (CG19·CG20 재현). 공통 후보집합(양쪽 꼬리 q0.05) precision@k:
    k=3 Δprec +0.0800 (동점제외 128일 중 88 양수, 부호검정 p=2.7e-5) · Δ실현선행수익 +0.0693
    k=5 Δprec +0.0178 (p=0.228) · Δ수익 +0.0254  → k=3 만 통과, k=5 미달(포화 k=10 은 해석 제외).
  CG22(4.1분, panel_420_asofpatch = 생산 유니버스 49종목·5폴드×10시드) — CO_q05_h5 0.5454 vs
    CO_core30_h5 0.5365 = Δ+0.0089 · 폴드 짝 [+0.0379, −0.0110, −0.0312, +0.0042, +0.0443]
    → 양(+) 3/5 · 평균 +0.0088 < 사전문턱 +0.02 → **재현 실패(150종목 패널 특이)**.

결론: 라벨 꼬리(q0.05) 축은 승격 후보 요건(실질성 k=3·k=5 모두 + 유니버스 재현)을 충족하지
못한다 → 축을 닫는다. 남은 기제 확인은 CG24(유동성 구간 의존성)로 일원화한다.
"""
import json
import os

PROJ = "/home/jhshi/analyist_dd"
BACKLOG = os.path.join(PROJ, "docs/QUANT_MODEL_BACKLOG.json")
LEDGER = os.path.join(PROJ, "data/reports/model_engineer_ledger.jsonl")

NOTE22 = (" | 2026-09-29 03:15 판정: 노이즈 — CO_q05_h5 0.5454±0.0292 vs CO_core30_h5 0.5365±0.0193 "
          "= Δ+0.0089 · 폴드 짝 [+0.0379, −0.0110, −0.0312, +0.0042, +0.0443] (양(+) 3/5, 평균 +0.0088). "
          "사전문턱 +0.02 미달 → **150종목 패널 특이**. 같은 런 q0.10 은 0.5234(Δ−0.0131)로 더 나빴다 "
          "(밀도 교정 arm 도 실패) → 승격 후보 아님. 라벨 꼬리 축은 CG24(유동성 구간 의존성)로 기제만 "
          "확인한 뒤 닫는다.")
NOTE21 = (" | 2026-09-29 03:08 판정: 부분 통과 — AUC Δ+0.0482(CG19·CG20 재현) · 공통 후보집합 "
          "precision@k: k=3 Δprec +0.0800(p=2.7e-5, 동점제외 88/128) · Δ수익 +0.0693 / k=5 Δprec +0.0178"
          "(p=0.228) · Δ수익 +0.0254 / k=10 포화(해석 제외). 사전등록은 k=3·5 둘 다 ≥+0.05 였으므로 "
          "**k=5 미달 = 요건 미충족**(k=3 만 실질성 확인). CG22 재현 실패와 합쳐 라벨 축 종료.")

b = json.load(open(BACKLOG, encoding="utf-8"))
ids = {i["id"] for i in b["items"]}
assert "CG24" not in ids, "CG24 이미 존재"

for it in b["items"]:
    if it["id"] == "CG21":
        it["note"] = (it.get("note") or "") + NOTE21
        it["conclusion"] = "부분 통과(k=3 실질성 O, k=5 미달) → 단독으로는 승격 후보 아님"
    if it["id"] == "CG22":
        it["note"] = (it.get("note") or "") + NOTE22
        it["conclusion"] = "재현 실패(Δ+0.0089) → 150종목 패널 특이 · 라벨 축 종료"

b["items"].append({
    "id": "CG24",
    "title": "라벨 꼬리 이득의 유동성 구간 의존성 — 같은 서로소 구간 안에서 q0.30↔q0.05 짝 비교 (5구간)",
    "status": "pending",
    "priority": 1,
    "est_minutes": 20,
    "hypothesis": ("CG22(49종목)에서 q0.05 Δ=+0.0089 로 재현 실패한 꼬리 이득이 '유동성 상위 구간 특이'인가. "
                   "CG13/CG16 실측에서 q0.30 대조군 AUC 는 유동성 상위 구간이 +0.0287 우세했다 → 꼬리 이득도 "
                   "구간에 따라 소멸한다면 CG22 실패의 기제가 설명되고 라벨 축을 증거와 함께 닫을 수 있다."),
    "command": ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 3600 python -u "
                "scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_150u.npz --folds 5 --seeds 5 "
                "--only Q5s_00_30,Q5s_30_60,Q5s_60_90,Q5s_90_120,Q5s_120_150,"
                "US_00_30,US_30_60,US_60_90,US_90_120,US_120_150'"),
    "counterfactual": "US_00_30",
    "pairs": [["Q5s_00_30", "US_00_30"], ["Q5s_30_60", "US_30_60"], ["Q5s_60_90", "US_60_90"],
              ["Q5s_90_120", "US_90_120"], ["Q5s_120_150", "US_120_150"]],
    "arm": "Q5s_00_30",
    "success": ("구간 짝 Δ(q0.05−q0.30) 평균 ≥ +0.02 이고 양(+) 구간 ≥ 4/5 → 꼬리 이득이 유동성 구간 "
                "전반에서 유지(라벨 축 생존 → 유니버스 확장 실험으로 이관). 짝 Δ 평균 < +0.02 또는 부호 "
                "뒤섞임 → '상위 유동성 구간 특이'로 닫고 라벨 축 종료(다음 레버 = 데이터 축)."),
    "expected": ("미지 — 30종목 구간에서 q0.05 는 양성 ≈1.5/일로 매우 얇아 AUC 분산이 크다. "
                 "구간별 Δ 의 분포(상위→하위 단조성)를 함께 읽어야 한다(사전등록: 단조 감소면 특이)."),
    "metric": "wf_sweep_summary",
    "desc": "CG24 — 라벨 꼬리 이득의 유동성 구간 의존성(짝 설계)",
    "note": ("2026-09-29 03:2x 신설(CG22 판정 직후). 같은 런에 게이트 ON 대조군 US_* 5구간이 이미 정의돼 "
             "있어 신규 arm 만 등록하면 된다(wf_label_sweep CONFIGS 에 Q5s_* 5개 추가). 행 집합은 구간별로 "
             "동일하고 라벨 꼬리만 다르므로 q0.30↔q0.05 차이는 '과제 난이도' 차이다 — 절대 AUC 를 등록 "
             "기준선과 비교하지 말고 짝 Δ 만 해석한다."),
})

json.dump(b, open(BACKLOG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("백로그: CG21·CG22 결론 기록 + CG24 등록 완료")

# ── 원장: 내가 지금 보고하는 CG21·CG22 를 reported 로 표시(다음 틱 중복 보고 방지) ──
rows = []
with open(LEDGER, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line:
            rows.append(json.loads(line))
n = 0
for r in rows:
    if r.get("id") in ("CG21", "CG22") and not r.get("reported"):
        r["reported"] = True
        n += 1
if n:
    tmp = LEDGER + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, LEDGER)
print(f"원장 reported 표시: {n}건")

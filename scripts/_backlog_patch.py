#!/usr/bin/env python3
"""백로그 항목 정정(2026-09-26 틱).

근거(실측):
  · U1(150종목) 0.5140±0.0201 vs 기록 기준선 0.5406(49종목) = Δ−0.0266 — 구동기가
    '자기대조'(대조군 문자열 첫 토큰 == arm)로 Δ+0.0000 '노이즈' 로 기록했다(수정 완료).
  · 유니버스 축(150·500종목)은 이 실측으로 종료 → U1B·U2 는 대기 해제.
  · RB1 은 arm 미지정이라 winner 기준 판정(Δ0 함정)에 걸린다 → arm 을 채운다.
  · L4 는 metric 키와 산출물이 없어 그대로 pending 이면 '판정불가' 로 끝난다 → needs_setup.
"""
import json
import os
import shutil
import time

P = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs/QUANT_MODEL_BACKLOG.json")
P = os.path.abspath(P)

with open(P, encoding="utf-8") as f:
    b = json.load(f)
by = {i["id"]: i for i in b["items"]}

# ① U1 — 자기대조 아티팩트 정정(원장의 Δ+0.0000 은 자기비교였다)
u1 = by["U1"]
u1["result_corrected"] = {
    "ts": "2026-09-26T04:20:00+09:00",
    "honest": "150종목 0.5140±0.0201(폴드승률 0.8, min 0.4824 max 0.5402) vs 기록 기준선 0.5406(49종목) → Δ−0.0266",
    "why": ("구동기 판정 버그: counterfactual 문자열 첫 토큰이 arm 과 같아(둘 다 LS_quant_q30_h5) "
            "같은 런 안 자기대조가 되어 Δ+0.0000 '노이즈' 로 기록됐다. judge_per() 로 수정·검증"
            "(scripts/_judge_per_test.py ALL PASS)."),
    "note": "타 패널(다른 데이터 스냅샷) 대조라 증거 강도는 약하지만, 방향은 악화이며 150종목은 채택 근거가 없다.",
}

# ② U1B — 유니버스 축 종료(150종목이 더 낫지 않으면 그 패널에서 라벨 분위를 다시 볼 이유가 없다)
u1b = by["U1B"]
u1b["status"] = "backlog"
u1b["note"] = ((u1b.get("note") or "") + " | 2026-09-26 보류: U1 실측 150종목 0.5140 vs 49종목 0.5406 "
               "= Δ−0.0266(악화). 150종목 패널이 더 낫지 않으므로 그 패널의 분위 재실험은 무의미. "
               "재개 조건: RB1 재기준선(정렬 수정 후)에서 유니버스 신호가 다시 보일 때.").strip()

# ③ U2 — 같은 축(표본 확대)이므로 함께 보류 유지, 근거 명시
u2 = by["U2"]
u2["note"] = ((u2.get("note") or "") + " | 2026-09-26: U1(150종목, 5h44m)이 Δ−0.0266 로 무개선 → "
              "표본 확대 축 종료. 500종목(약 14h)은 같은 축 반복이라 우선순위 최하 유지.").strip()

# ④ RB1 — arm 지정(사전등록 가설 = 시간가변 풀) + 실제 소요 반영
rb1 = by["RB1"]
rb1["arm"] = "PO_timevary_h5"
rb1["est_minutes"] = 75
rb1["note"] = ((rb1.get("note") or "") + " | 2026-09-26: arm=PO_timevary_h5 를 명시(미지정이면 "
               "'대조군 제외 최고' 로 판정돼 사전등록 가설과 어긋난다). 첫 3분간 모델 8회 학습 = "
               "약 2.7회/분 → 4 config×5폴드×3시드×3알고(180회) ≈ 65~75분이라 est_minutes 를 30→75 로 올렸다.").strip()

# ⑤ L4 — command 가 산출물을 만들지 않고 metric 키도 없다 → needs_setup
l4 = by["L4"]
l4["status"] = "needs_setup"
l4["metric"] = "wf_sweep_summary"
l4["arm"] = "FACTOR_multi_h5"
l4["counterfactual"] = "LS_quant_q30_h5"
l4["setup_needed"] = [
    "scripts/factor_vs_ml.py 작성 — 부활 피처(supply_market/financial_ratio/macro)로 가치·퀄리티·모멘텀·"
    "저변동·멀티팩터 분위를 만들고, 동일 유니버스·기간에서 forward return IC·분위 스프레드를 ML 챔피언과 비교해 "
    "reports/factor_vs_ml_<stamp>.json 으로 저장(rcept_dt as-of 준수).",
    "command 를 그 스크립트로 교체(현재 command 는 ls 만이라 아무 산출물도 만들지 않는다).",
]
l4["note"] = ((l4.get("note") or "") + " | 2026-09-26: metric 키 부재 + command 가 ls 뿐이라 그대로 "
              "pending 이면 사이클이 '판정불가' 로 끝난다(구동기 KeyError 위험도 함께 수정). needs_setup 으로 내렸다.").strip()

with open(P, "w", encoding="utf-8") as f:
    json.dump(b, f, ensure_ascii=False, indent=2)
shutil.copy2(P, P + f".bak-{time.strftime('%Y%m%d-%H%M%S')}")

print("수정 완료:", {i["id"]: i["status"] for i in b["items"] if i["id"] in ("U1", "U1B", "U2", "RB1", "L4")})
print("pending:", [i["id"] for i in b["items"] if i["status"] == "pending"])

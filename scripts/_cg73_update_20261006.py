#!/usr/bin/env python3
"""CG73 갱신 — 국면 조건화 축까지 닫힌 뒤 '데이터 축이 유일한 레버'임을 기록(우선순위 1)."""
import json
import os

P = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "docs", "QUANT_MODEL_BACKLOG.json")
d = json.load(open(P, encoding="utf-8"))
n = 0
for it in d["items"]:
    if it.get("id") == "CG73":
        it["priority"] = 1
        it["note"] = (it.get("note") or "") + (
            " | 2026-10-06 02:1x 갱신(장외 자율): 남아 있던 마지막 모델측 '재개 조건'인 **국면 조건화**도 "
            "실측으로 닫혔다(CG126 추세 국면 ΔIC −0.0761·t −1.70 미달 · CG127 저변동 국면 우위는 서로소 "
            "2표본에서 미재현/부호반전 −0.0036·t −0.16 / −0.0886·t −2.64). CG125(앙상블 가중 A/B Δ−0.0005) "
            "까지 포함해 **모델측 레버(변환·HP·앙상블·선별·가중·목적함수·라벨·유니버스·창·정규화·국면) = 0** 이고 "
            "돈 축(top-k vs 풀평균·팩터 랭킹·하위꼬리)도 종결 → 남은 레버는 **데이터 축(수집 확장 + 이력 축적)** "
            "뿐이다. 착수는 사람/수집기 결정: ① 인트라데이 = XR26 수리(수집기 1줄+재수집, 오프라인 증명 5/5) → "
            "CG101 즉시 착수 가능 ② SNS/뉴스 = 커버리지(패널 유니버스) + 이력(≥420일) 동시 필요. "
            "패널 창 단축은 std 0.0556 으로 판정 불가(U3b)라 대안이 아니다.")
        n += 1
assert n == 1, "CG73 미발견"
with open(P, "w", encoding="utf-8") as f:
    json.dump(d, f, ensure_ascii=False, indent=2)
d2 = json.load(open(P, encoding="utf-8"))
it = next(i for i in d2["items"] if i.get("id") == "CG73")
print("CG73 priority", it["priority"], "note_len", len(it["note"]))

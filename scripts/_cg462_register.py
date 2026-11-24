#!/usr/bin/env python3
"""CG462 결과 + CG463(프로토콜 강화, 승인 대기) 를 백로그에 등록한다."""
import json
from datetime import datetime, timezone, timedelta

P = "/home/jhshi/analyist_dd/docs/QUANT_MODEL_BACKLOG.json"
KST = timezone(timedelta(hours=9))
now = datetime.now(KST).replace(microsecond=0).isoformat()

d = json.load(open(P, encoding="utf-8"))
items = d["items"]
ids = {i.get("id") for i in items}
assert "CG462" not in ids and "CG463" not in ids

cg462 = {
    "id": "CG462",
    "title": "[측정정합성] 판정 문턱 +0.02 는 검출 가능한가 — 폴드 짝 Δ 잡음바닥 실측 (MDE 0.014 · 중앙 |Δ| 0.005)",
    "status": "done",
    "priority": 1,
    "kind": "measurement_integrity",
    "owner": "engineer",
    "affects_model": False,
    "created_at": now,
    "metric": "paired_fold_delta_noise",
    "command": "python3 scripts/cg462_paired_delta_noise.py",
    "counterfactual": "스킬 가정 '폴드 std ±0.03 → 폴드 평균 +0.02 만 신호'(arm 레벨 std 로 검정력을 추정)",
    "hypothesis": "판정 통계량은 arm 레벨 평균이 아니라 **같은 런·같은 폴드의 짝 Δ** 다. 짝 Δ 의 SE 를 실측하면 (a) +0.02 문턱의 검출력과 (b) 22사이클 연속 미달이 '검출력 부족'인지 '실효 0'인지를 가를 수 있다.",
    "success": "같은 런·5폴드 짝의 Δ sd·SE·MDE 를 기록한다. MDE ≤ 0.02 면 '문턱 검출가능' / > 0.02 면 '검출 불가 → 프로토콜 재정의'로 판정.",
    "result": {
        "verdict": "문턱은 검출 가능 — 미달의 원인은 잡음이 아니라 실효 0",
        "detail": "다arm 런 72개 중 대조군 있는 같은 런 짝 18쌍(전부 5폴드). 폴드 짝 Δ sd 중앙 0.0112(p75 0.0283·max 0.0415) · SE 중앙 0.0050(max 0.0185) · MDE(80% 검정력, 2.8×SE) 0.0140 → 5폴드 짝 비교로 +0.02 는 t≈4 로 검출된다(검출력 부족 아님). 반면 실측 짝의 |Δ| 중앙 0.0053 · Δ 중앙 0.0000 이고 |Δ|≥0.02 는 4/18 인데 그중 3쌍은 유의하게 **음수**(CG286 liq3 -0.0599 t -4.72 · liq1 -0.0549 t -3.32 · CG287 wfill4 -0.0306 t -2.42). 즉 '22사이클 미달'은 잡음 때문에 못 본 것이 아니라 시도한 조정이 실제로 0(또는 음수)이라는 뜻이다. 단서: 고잡음 런은 SE 0.0185(MDE 0.052)까지 올라가므로 그런 런은 폴드를 늘려야 판정 가능.",
        "evidence": "data/reports/overnight/cg462_paired_delta_noise.json",
        "rc": 0,
    },
    "note": "2026-11-24 18:2x 엔지니어 자율(장외 틱·pending 0건·규칙 4). 함의: ① 조정 축(변환·HP·라벨·유니버스)이 실효 0 임을 잡음과 분리해 확정 → 남은 레버는 데이터(인트라데이 CG388 11-26 · CG450 12-07)뿐 ② 판정 프로토콜은 '레벨 std ±0.03' 대신 폴드 짝 Δ SE(≈0.005)를 인용해야 한다 → CG463(승인 대기).",
}

cg463 = {
    "id": "CG463",
    "title": "[머니 프로토콜·승인] 판정에 폴드 짝 t 를 병기 — 레벨 잡음이 만든 '신호' 차단 (CG462 후속)",
    "status": "needs_setup",
    "priority": 2,
    "kind": "measurement_integrity",
    "owner": "engineer",
    "affects_model": False,
    "created_at": now,
    "command": None,
    "counterfactual": "현행: 폴드 평균 Δ ≥ +0.02 단일 조건(CG462 실측상 고잡음 런에서 |Δ|≥0.02·|t|<2 사례 존재 — CG193_h20 Δ-0.0228 t-1.23)",
    "hypothesis": "CG462 실측: 같은 런 폴드 짝 Δ SE 중앙 0.0050 이지만 p75 0.0129·max 0.0185 로 런마다 4배 차이가 난다. 단일 조건(+0.02)만 쓰면 고잡음 런에서 우연한 |Δ|≥0.02 를 '신호'로, 저잡음 런에서 진짜 +0.015 를 '노이즈'로 오판한다.",
    "success": "판정기에 '폴드 짝 t ≥ 2' 를 병기(문턱 +0.02 는 불변, 조건 추가만) + 회귀 테스트 1개(기존 판정 비트 동일) + 스코어보드 문서에 짝 t 병기 명시.",
    "setup_needed": "프로토콜(판정 조건) 변경이라 리뷰보드 승인 대상 — 승인 후 내가 할 것: ① model_engineer_cycle 판정기에 paired_t 계산·병기 ② 회귀 테스트(기존 요약 JSON 재판정 결과 불변 확인) ③ 스코어보드 stanza 문서 갱신. 문턱값 자체는 바꾸지 않는다(낮추지 않음).",
    "est_minutes": 40,
    "note": "2026-11-24 18:2x 엔지니어 자율 신설(CG462 후속). 근거 수치: 짝 SE 중앙 0.0050 · p75 0.0129 · max 0.0185 · MDE 0.0140.",
}

items.append(cg462)
items.append(cg463)
d["items"] = items
d["updated_at"] = now
json.dump(d, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("added CG462(done) CG463(needs_setup) · items", len(items), "· updated_at", now)

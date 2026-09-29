#!/usr/bin/env python3
"""CG29 결과 후처리(엔지니어 수동 정리):
 ① 원장 reported=True (직접 보고했으므로 다음 정시 틱의 중복 보고를 막는다)
 ② 백로그 CG29 result 에 placebo 대조·폴드 짝 Δ·evfix 패널 검증을 덧붙인다
 ③ XR9(이벤트 공시 피처 연결) note 에 '기본 패널 이벤트 컬럼 낡음' 결함을 남긴다
"""
import json

LED = "/home/jhshi/analyist_dd/data/reports/model_engineer_ledger.jsonl"
BL = "/home/jhshi/analyist_dd/docs/QUANT_MODEL_BACKLOG.json"

led = [json.loads(l) for l in open(LED) if l.strip()]
n_marked = 0
for r in led:
    if r.get("id") == "CG29" and not r.get("reported"):
        r["reported"] = True
        n_marked += 1
with open(LED, "w") as f:
    for r in led:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

rec = [r for r in led if r.get("id") == "CG29"][-1]
pe = (rec.get("parsed") or {}).get("per_exp") or {}
ctrl = pe.get("CO_core30_h5")
arm = pe.get("CO_core30_ev10_h5")
pla = pe.get("CO_core30_pv10_h5")
pairs = None
if ctrl and arm:
    pairs = [round(a - c, 4) for a, c in zip(arm["folds"], ctrl["folds"])]
pairs_p = [round(p - c, 4) for p, c in zip(pla["folds"], ctrl["folds"])] if (ctrl and pla) else None
summary = {
    "verdict": "노이즈 — Δ(arm−대조군) = +0.0005 < 사전문턱 +0.02 (폴드 짝 2/5 양수, 동점 1)",
    "control": {k: ctrl[k] for k in ("mean", "std", "min", "max")},
    "arm_ev10": {k: arm[k] for k in ("mean", "std", "min", "max")},
    "placebo_pv10": {k: pla[k] for k in ("mean", "std", "min", "max")},
    "fold_pairs_arm_minus_control": pairs,
    "fold_pairs_placebo_minus_control": pairs_p,
    "delta_arm_minus_placebo": round(arm["mean"] - pla["mean"], 4),
    "control_reproduced": abs(ctrl["mean"] - 0.5363) < 1e-9,
    "conclusion": (
        "백필 이벤트(공시) 10개를 게이트 ON 경로에 강제투입해도 대조군 대비 +0.0005 다. placebo(무정보 10개)는 "
        "용량 추가로 −0.0044 로 오히려 해롭다 → '패널 안 신규 정보' 축은 닫는다. 남은 레버는 U3(창 확장)와 "
        "외부 데이터 수집뿐이다. 부수: 대조군이 기본 패널과 비트 동일(0.5363, 폴드까지 일치)해 evfix 패치가 "
        "이벤트 컬럼만 건드렸음이 교차 검증됐다."),
}

bl = json.load(open(BL))
for it in bl["items"]:
    if it.get("id") == "CG29":
        it["result"] = summary
        it["status"] = "done"
        it["note"] = (it.get("note", "") +
                      " | 2026-09-29 16:15 실측(5.1분, rc=0): 대조군 0.5363±0.0188(기본 패널과 폴드까지 동일 — "
                      "evfix 패치가 이벤트 컬럼만 변경했음이 교차 검증됨) · ev10 0.5368±0.0179(Δ+0.0005, 폴드 짝 2/5, "
                      "동점 1) · placebo 0.5319±0.0143(Δ−0.0044) → 노이즈. 폴드별 gate_add 통과는 9~10/10(이벤트 희소 구간).")
    if it.get("id") == "XR9":
        it["note"] = (it.get("note", "") +
                      " | 2026-09-29 실측(엔지니어): **기본 패널(panel_420_asofpatch)의 event_* 컬럼은 2026-04 이전이 "
                      "전량 0** 이다(event_exec_change_5d 월별 nonzero 2025-07~2026-03 = 0.0% → 2026-04~ 1.3~3.2%). "
                      "따라서 워크포워드 폴드 1~2 학습창에서는 이 피처군이 분산 0 으로 전량 탈락하고, EV1 등 과거 이벤트 "
                      "측정은 '이벤트가 없는 구간'을 잰 셈이다. 백필본(ev 패널 뒤 17컬럼)은 전 구간 커버(1.5~8%)로 정상 → "
                      "패널 빌드의 이벤트 조인이 낡은 원천을 쓴 것으로 보인다(리서처 확인 필요). 교체 패널은 "
                      "scripts/patch_panel_events.py 산출물 panel_420_asofpatch_evfix.npz(교체 외 컬럼 해시 동일). "
                      "단 교체 후에도 강제투입 Δ+0.0005 로 노이즈였다(CG29) — 이벤트 피처 '연결'은 정보량 문제로 남는다.")
bl["updated_at"] = "2026-09-29T16:16:00+09:00"
json.dump(bl, open(BL, "w"), ensure_ascii=False, indent=1)

print("ledger reported 마킹:", n_marked)
print(json.dumps(summary, ensure_ascii=False, indent=1))

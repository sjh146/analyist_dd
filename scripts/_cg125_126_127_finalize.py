#!/usr/bin/env python3
"""CG125 짝 Δ 기록 + CG125/126/127 reported 플래그 + CG127b 결과 기록.

2026-10-06 장외 자율 세션. 규율: ① 원장은 _rewrite_ledger ② 백로그는 in-place mutate + indent=2
③ reported_at 을 남겨 미전달 감지기가 이 실행의 실패와 짝지을 수 있게 한다.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import model_engineer_cycle as m  # noqa: E402

now = m.now_kst().isoformat(timespec="seconds")

# ── 원장 ───────────────────────────────────────────────────────────────────
led = m.load_ledger()
n_upd = 0
for r in led:
    rid = r.get("id")
    if rid == "CG125":
        # 짝 Δ = 가중 arm − 균등 arm (같은 런·같은 창·같은 유니버스)
        w = [0.5122, 0.5345]
        e = [0.5125, 0.5352]
        paired = [round(a - b, 4) for a, b in zip(w, e)]
        mean_d = round(sum(paired) / len(paired), 4)
        p = r.get("parsed")
        if isinstance(p, dict):
            p["counterfactual_arm"] = {
                "arm": "champion_wtest 플래그 OFF(균등 평균 = 현행 배포 동작)",
                "json": "services/xgboost-ml/reports/overnight/cg125_equal.json",
                "robust_auc": 0.5238, "folds": e, "auc_std": 0.0114,
                "auc_pooled": 0.5215, "rows": 8370,
            }
            p["paired"] = {"metric": "robust_auc", "per_window": paired,
                           "mean_delta": mean_d, "positive_windows": sum(1 for x in paired if x > 0),
                           "n_windows": len(paired),
                           "note": "가중 − 균등 · 같은 런·같은 창·같은 유니버스 시드 (배포 경로 짝)"}
            p["delta"] = mean_d
        r["verdict"] = "노이즈"
        r["delta"] = mean_d
        r["detail"] = ((r.get("detail") or "") +
                       " | 배포 경로 짝 A/B: 가중(flag ON) 0.5233±0.0111 [0.5122, 0.5345] vs "
                       "균등(현행 배포) 0.5238±0.0114 [0.5125, 0.5352] → 짝 Δ −0.0005 "
                       "(창별 [−0.0003, −0.0007] · 양(+) 0/2) = 사전문턱 +0.02 미달 → 앙상블 가중 방식 축 종결. "
                       "남은 것은 보고·게이트 지표를 배포 실체(균등)와 통일하는 정합성 수리뿐.")
        r["reported"] = True
        r["reported_at"] = now
        n_upd += 1
    elif rid in ("CG126", "CG127"):
        r["reported"] = True
        r["reported_at"] = now
        n_upd += 1
m._rewrite_ledger(led)
print("ledger updated:", n_upd)

# ── 백로그 ─────────────────────────────────────────────────────────────────
b = m.load_backlog()
for it in b["items"]:
    i = it.get("id")
    if i == "CG125":
        it["status"] = "done"
        res = it.get("result") or {}
        res["delta"] = -0.0005
        res["counterfactual_arm"] = {"robust_auc": 0.5238, "folds": [0.5125, 0.5352],
                                     "json": "services/xgboost-ml/reports/overnight/cg125_equal.json"}
        res["paired"] = {"per_window": [-0.0003, -0.0007], "mean_delta": -0.0005,
                         "positive_windows": 0, "n_windows": 2}
        res["judgment"] = ("가중(flag ON) 0.5233±0.0111 vs 균등(현행 배포) 0.5238±0.0114 → 짝 Δ −0.0005 "
                           "(0/2 창 양(+)) = 노이즈. 사전등록대로 축을 닫는다: 가중 방식은 배포 성능 "
                           "레버가 아니다. 남은 조치 = 보고·게이트 지표(가중)를 배포 실체(균등)로 통일"
                           "(발행 계약 변경 → 승인 대상).")
        it["result"] = res
    elif i == "CG126":
        res = it.get("result") or {}
        res.update({
            "verdict": "노이즈",
            "detail": ("ΔIC(up−down) −0.0761 · t −1.70 (n up/down 115/96) · all IC 0.0171(t 0.77, n 211) "
                       "— |t|≥2 미달 = 국면 의존 IC 미검출. 탐색적 관찰: cg108 vol low IC 0.0972(t 3.16, "
                       "n 70) vs mid −0.0437 · high −0.0018 → ΔIC(low−rest) +0.1197(t 2.82) = CG127 로 넘김."),
            "delta": -0.0761,
        })
        it["result"] = res
        it["status"] = "done"
        it["finding"] = ("추세 국면 조건화(up/down)는 정보 없음(|t| 1.70 < 2). 변동성 국면은 탐색적으로 "
                         "저변동 우위(+0.1197 t 2.82)가 보였으나 결과를 본 뒤의 관찰 → CG127 에서 사전등록 재현.")
    elif i == "CG127":
        res = it.get("result") or {}
        res.update({
            "verdict": "노이즈",
            "detail": ("정본 cg120_q05_all(98세션·seed7): ΔIC(low−rest) −0.0036 · t −0.16 (n 32/66) → 미재현. "
                       "제2표본 cg100_q05_all(80세션·seed0·exp cg92_q05): ΔIC(low−rest) −0.0886 · t −2.64 "
                       "(n 26/54) → **부호 반전**(저변동이 더 낮다). 두 표본 모두 단측문턱 Δ≥+0.03 미달."),
            "delta": -0.0036,
            "delta_second_sample": -0.0886,
        })
        it["result"] = res
        it["status"] = "done"
        it["finding"] = ("CG126 의 저변동 국면 IC 우위는 서로소 2표본에서 재현되지 않았다(하나는 부호 반전) "
                         "→ 국면 조건화 축 종결. 이로써 XR11 재개 조건까지 닫혀, 모델측 레버는 0 이다(잔여=데이터 축).")
m.save_backlog(b)
print("backlog updated")

#!/usr/bin/env python3
"""CG13 신설 + CG12 해석 기록 (2026-09-28 16:2x 엔지니어 틱)."""
import json
import os
import shutil

PROJ = "/home/jhshi/analyist_dd"
p = os.path.join(PROJ, "docs/QUANT_MODEL_BACKLOG.json")
shutil.copy(p, p + ".bak-cg13")
b = json.load(open(p, encoding="utf-8"))
items = b["items"]
byid = {i["id"]: i for i in items}

CG13 = {
    "id": "CG13",
    "title": "유니버스 교체 노이즈 밴드 — 같은 크기(30종목) 서로소 5구간의 AUC 산포가 사전문턱 +0.02 를 넘는가",
    "status": "pending",
    "priority": 2,
    "affects_model": True,
    "baseline": {"value": 0.5406, "source": "등록 기준선(49종목) — 이 실험의 판정에는 쓰지 않는다"},
    "hypothesis": ("이 스택에서 문턱을 넘은 '신호'는 전부 유니버스를 바꾸면 뒤집혔다(U1 교차패널 "
                   "Δ−0.0266 ↔ UN1 같은 패널 +0.0071 · CG5 150종목 부호 반전 · CG12 49종목 크기에서도 "
                   "집합이 다르면 우승 config −0.0287). 그런데 **같은 크기의 다른 종목 집합**이 만드는 "
                   "AUC 분산은 측정된 적이 없다. 그 산포가 사전문턱 +0.02 급이면 과거 Δ≤0.03 은 "
                   "해석 불가이고, 순열검정 없이는 어떤 arm 도 판정할 수 없다."),
    "evidence": ("CG12 실측(같은 패널·게이트 ON·5폴드×5시드): 49종목 집합이 panel_420_asofpatch "
                 "(CO_core30_h5 0.5365) vs panel_150u 첫 49종목(UNg_49 0.5243) = 같은 크기·다른 "
                 "집합에서 −0.0122. CG12 의 우승 config 승계도 49종목에서 0.4956(대조군 0.5243 대비 "
                 "−0.0287)로 뒤집혔다."),
    "method": ("panel_150u(150종목·24,742행)를 패널 순서로 30종목씩 **서로소** 5구간([0:30)·[30:60)·"
               "[60:90)·[90:120)·[120:150))으로 자르고 같은 config·같은 폴드·같은 피처로 측정한다 "
               "(codes_slice 신설). 전 구간의 mean/min/max/std 를 보고한다."),
    "command": ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 timeout 3600 "
                "python -u scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_150u.npz "
                "--folds 5 --seeds 5 --only "
                "US_00_30,US_30_60,US_60_90,US_90_120,US_120_150,US_all150'"),
    "check": "python3 scripts/model_engineer_cycle.py --status",
    "check_target": {"op": ">=", "value": 1},
    "metric": "wf_sweep_summary",
    "arm": "US_00_30",
    "counterfactual": "US_120_150",
    "est_minutes": 20,
    "success": ("Δ(US_00_30 − US_120_150) ≥ +0.02 → 유니버스 교체만으로 사전문턱이 만들어진다 → "
                "과거 Δ≤0.03 은 무효, 이후 모든 arm 판정에 구간 순열검정 필수. |Δ| < 0.02 → 교체 "
                "노이즈는 문턱 미만이므로 기존 프로토콜 유지 가능(패널 특이성은 다른 원인)."),
    "expected": "미지 — 어느 쪽이든 다음 프로토콜 결정의 근거가 된다",
    "cost": "약 5~8분 (30종목 부분집합 6 config × 5폴드 × 5시드 — CG12 9arm=10.3분 실측 기준)",
    "note": ("2026-09-28 16:2x 신설(CG12 후속·규칙 4). codes_slice 옵션을 wf_label_sweep 에 신설해 "
             "검증 완료(스모크: [0:30) 5,125행 · [120:150) 4,762행, 서로소 확인). est 20분이라 "
             "20:00 재생성 창과 U3 런처(20:35) 전에 반드시 끝난다."),
}
if "CG13" not in byid:
    items.append(CG13)

CG12_NOTE = (" | 2026-09-28 16:14 실측(rc=0·10.3분·panel_150u·게이트 ON·5폴드×5시드): "
             "UNg_25 0.5276±0.0441 > UNg_49 0.5243±0.0398 > UNg_150 0.5101±0.0145 > UNg_75 0.5087±0.0240 "
             "> UNg_100 0.5034±0.0249 → 판정 **노이즈**(Δ+0.0175 < +0.02), 단조 아님(150 에서 반등). "
             "→ '49종목 등록 기준선의 소유니버스 낙관 편향'은 **기각**(25종목 이득은 폴드 std 0.044 안). "
             "부수 판정 ①같은 런에서 우승 config(rank+스무딩+depth1)를 크기별로 걸면 RSg_25 0.4913·"
             "RSg_49 0.4956·RSg_75 0.4945·RSg_150 0.4992 로 **모든 크기에서 같은 크기 대조군을 하회**"
             "(25 −0.0363 · 49 −0.0287 · 75 −0.0142 · 150 −0.0109) → CG4 의 49종목 +0.0227 은 "
             "**패널 특이**(같은 49종목 크기에서도 집합이 다르면 부호 반전)로 확정, 승격 후보에서 제외. "
             "②RSg_150 0.4992 / UNg_150 0.5101 은 CG11 수치와 동일 — 하니스 재현성 확인. "
             "③같은 49종목 크기·다른 집합: 0.5365(panel_420_asofpatch) vs 0.5243(panel_150u 첫 49) = −0.0122.")
if "CG12" in byid:
    byid["CG12"]["note"] = (byid["CG12"].get("note") or "") + CG12_NOTE

json.dump(b, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("CG13 added:", "CG13" in byid)
print("pending:", [(i["id"], i.get("priority"), i.get("est_minutes"))
                   for i in items if i.get("status") == "pending"])

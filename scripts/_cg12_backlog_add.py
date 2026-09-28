#!/usr/bin/env python3
"""CG12 백로그 신설 + L5b/L5c 상태 정리 (2026-09-28 엔지니어 틱)."""
import json
import os
import shutil

PROJ = "/home/jhshi/analyist_dd"
p = os.path.join(PROJ, "docs/QUANT_MODEL_BACKLOG.json")
shutil.copy(p, p + ".bak-cg12")
b = json.load(open(p, encoding="utf-8"))
items = b["items"]
byid = {i["id"]: i for i in items}

CG12 = {
    "id": "CG12",
    "title": "유니버스 크기 단조 추세 — 게이트 ON 대조군 AUC 가 종목 수에 따라 하락하는가 (등록 기준선의 소유유니버스 낙관 편향 검정)",
    "status": "pending",
    "priority": 1,
    "affects_model": True,
    "baseline": {"value": 0.5406,
                 "source": "등록 기준선 (panel_420_asofpatch · 49종목 · 281거래일)"},
    "hypothesis": ("같은 프로토콜·게이트 ON 대조군 AUC 가 패널에 따라 0.5365(49종목, CG4) → "
                   "0.5101(150종목, CG11) 로 0.026 벌어졌다. 유니버스가 커질수록 AUC 가 단조 "
                   "하락한다면 등록 기준선 0.5406 자체가 소유유니버스 낙관 편향이고, 프로덕션 "
                   "챔피언 유니버스(200종목)에서는 +0.02 문턱이 애초에 도달 불가다 — 26사이클 "
                   "무개선의 구조적 원인 후보."),
    "evidence": ("CG4(49종목) CO_core30_h5 0.5365±0.0193 vs CG11(150종목) 같은 config 0.5101±0.0145 "
                 "— 다만 교차패널 비교다. 같은 패널(panel_150u) 안에서 codes_limit 중첩 "
                 "부분집합(25⊂49⊂75⊂100⊂150)으로 종목 수만 바꾼 단조 추세를 잰다."),
    "method": ("① 같은 패널·같은 런에서 게이트 ON 대조군 5수준(25/49/75/100/150종목) "
               "② 우승 config(rank+스무딩+depth1)를 4수준에 같이 걸어 부호 반전 지점 확인 "
               "③ 판정 = UNg_25 − UNg_150"),
    "command": ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 timeout 3600 "
                "python -u scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_150u.npz "
                "--folds 5 --seeds 5 --only "
                "UNg_25,UNg_49,UNg_75,UNg_100,UNg_150,RSg_25,RSg_49,RSg_75,RSg_150'"),
    "check": "python3 scripts/model_engineer_cycle.py --status",
    "check_target": {"op": ">=", "value": 1},
    "metric": "wf_sweep_summary",
    "arm": "UNg_25",
    "counterfactual": "UNg_150",
    "est_minutes": 25,
    "success": ("UNg_25 − UNg_150 ≥ +0.02 (폴드 승률 ≥0.8) → 소유유니버스 낙관 편향 실재 → 승격 "
                "프로토콜 기준선 재등록 승인 요청. |Δ|<0.02 면 '유니버스 크기는 AUC 레버가 아니다'로 "
                "닫고 데이터 축으로 간다."),
    "expected": "낙관 편향이면 +0.02~+0.05, 아니면 |Δ|<0.01",
    "cost": "약 10~15분 (panel_150u 캐시 · 9 config × 5폴드 × 5시드, CG11 4arm=3.9분 실측 기준)",
    "caution": ("panel_150u.npz 필요(존재, 2026-09-26 03:12). 피처 코드 변경 중이면 패널 스냅샷이 "
                "달라지므로, 스윕이 완료될 때까지 패널 재빌드를 띄우지 말 것."),
    "note": ("2026-09-28 16:0x 신설 — 틱이 ETA 가드로 U3 를 건너뛰고 실행 가능 pending 이 0 이라 "
             "다음 가설을 설계. timeout 3600 · est 25 로 20:00 컨테이너 재생성 창 전에 반드시 끝난다."),
}

if "CG12" not in byid:
    items.append(CG12)

fixed = []
for iid, reason in (
    ("L5b", "팩터/전략 코드(sys.path 배선)는 이 역할 소유가 아니다 — 구현 전 사용자 승인 필요"
            "(services/backtester·strategy-agents·job_runner 소유)"),
    ("L5c", "PIT 유니버스 교체는 strategy-agents/app/factors/universe.py 수정 — 다른 역할 소유 "
            "파일이라 승인 필요"),
):
    it = byid.get(iid)
    if it and it.get("status") == "pending":
        it["status"] = "needs_setup"
        it["setup_needed"] = reason
        it["note"] = (it.get("note") or "") + (
            f" | 2026-09-28 16:0x: pending→needs_setup — command 가 없어 매 틱 'pending 인데 command "
            f"없다' 경고를 내고 큐 판정만 흐렸다. {reason}")
        fixed.append(iid)

b["items"] = items
json.dump(b, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("CG12 added:", "CG12" in {i["id"] for i in items})
print("fixed:", fixed)
print("pending now:", [(i["id"], i.get("est_minutes")) for i in items if i.get("status") == "pending"])

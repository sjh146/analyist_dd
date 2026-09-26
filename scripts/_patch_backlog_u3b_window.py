#!/usr/bin/env python3
"""_patch_backlog_u3b_window.py — U3/U3b 창 수치 정정(2026-09-26 19:00).

실측(scripts/panel_subset.py 로 확인): 기준선 패널 panel_420_asofpatch 는 **281거래일**
(2025-07-31~2026-09-23)·49종목·13,609행이다. 종전 기록의 '314거래일' 은 R17 백필 이전
DB 전체 구간(2025-06-16~)을 잘못 옮긴 수치였다 → 창 비교 기준일을 2025-07-31 로 바로잡는다
(그대로 두면 U3b 가 다른 구간을 잘라 '창 효과' 대신 '표본 교체' 를 재게 된다).
"""
import json
import os

BACKLOG = "/home/jhshi/analyist_dd/docs/QUANT_MODEL_BACKLOG.json"
b = json.load(open(BACKLOG, encoding="utf-8"))

for it in b["items"]:
    if it["id"] == "U3":
        it["title"] = ("확장 이력 패널(995일 창 = 659거래일) 재빌드 + 로버스트 재측정 "
                       "— 기존 281거래일 창 대비 2.35배")
        it["baseline"] = {
            "value": 0.5406,
            "source": "L1/RB1 실측 · 49종목 · 5폴드×3시드 · 281거래일 창(2025-07-31~2026-09-23)",
            "note": "창이 다르므로 직접 비교는 약한 증거 — U3b 로 격리",
        }
        it["counterfactual"] = "기록 기준선 0.5406 (panel_420_asofpatch · 281거래일 창)"
    if it["id"] == "U3b":
        it["title"] = ("창 효과 격리 — 같은 패널(panel_995)에서 281거래일 행만 잘라 A/B "
                       "(동일 스냅샷·동일 피처, 창만 다름)")
        it["command"] = (
            "docker exec stock_xgboost_ml sh -c 'cd /app && python3 /app/scripts/panel_subset.py "
            "--src /app/app/models/wf/panel_995.npz --dst /app/app/models/wf/panel_995_w281.npz "
            "--since 2025-07-31 && OMP_NUM_THREADS=4 timeout 7200 python -u "
            "scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_995_w281.npz "
            "--folds 5 --seeds 3 --only LS_quant_q30_h5'")
        it["check"] = "docker exec stock_xgboost_ml ls -la /app/app/models/wf/panel_995_w281.npz"
        it["method"] = ("① panel_subset.py 로 panel_995 → panel_995_w281.npz "
                        "(--since 2025-07-31 = 기준선 패널과 같은 281거래일 창) "
                        "② 같은 설정(LS_quant_q30_h5·5폴드×3시드)으로 스윕 "
                        "③ Δ(659거래일 − 281거래일) 를 창 효과로 판정 "
                        "④ 기준선 패널(panel_420_asofpatch)의 0.5406 과도 함께 비교")
        it["evidence"] = (
            "실측: panel_420_asofpatch = 281거래일(2025-07-31~2026-09-23)·49종목·13,609행 "
            "(panel_subset.py 로 확인). U1 은 교차패널로 Δ−0.0266 으로 기록됐지만 같은 패널 "
            "안에서 행 집합만 바꾸면 Δ+0.0071(부호 반전)이었다 — 창 비교는 반드시 같은 "
            "스냅샷 안에서 해야 한다.")

b["updated_at"] = "2026-09-26T19:00:00+09:00"
tmp = BACKLOG + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(b, f, ensure_ascii=False, indent=2)
os.replace(tmp, BACKLOG)
for it in b["items"]:
    if it["id"] in ("U3", "U3b"):
        print(it["id"], it["status"], "|", it["title"])
        print("   cmd:", it["command"][:150], "...")

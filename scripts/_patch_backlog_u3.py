#!/usr/bin/env python3
"""_patch_backlog_u3.py — 백로그 정정(2026-09-26 18:45 틱).

① R17(가격 이력 백필)을 done 으로 확정 — 실측: market_data 3,093,147행 / 910거래일 /
   3,970종목(2023-01-02~2026-09-23), 목표(≥600거래일) 충족. 종전까지 pending 으로 남아
   틱의 next_item 이 매번 R17(단순 SELECT·metric 없음)을 집어 실행을 낭비할 상태였다.
② U3 명령을 **실제 실행 가능한 형태**로 정정: 종전 command 는 --panel panel_910.npz 만 지정해
   ① 그런 파일이 없고 ② days/limit 을 주지 않아 기본값(420일·50종목)으로 420일 창을 굽는다.
   → --days 995(=659거래일, 2024-01-05~) · --limit 50(기준선과 동일 유니버스) · panel_995.npz.
   창을 995일로 잡은 이유: 피처 파이프라인이 start 이전 **365일 lookback** 을 요구하므로
   가격 이력이 2023-01-02부터인 이 DB 에서 워밍업이 완전한 최대 창이 2024-01 초이다
   (그보다 앞을 넣으면 첫 해가 결측 덩어리 → '창 효과'가 아니라 '결측 효과'를 재게 된다).
③ U3b(창 효과 격리) 신설: 같은 패널에서 행만 잘라 314거래일 창과 A/B → 교차패널 비교의
   약한 증거를 같은 스냅샷 안의 강한 증거로 승격한다.
"""
import json
import sys

BACKLOG = "/home/jhshi/analyist_dd/docs/QUANT_MODEL_BACKLOG.json"
b = json.load(open(BACKLOG, encoding="utf-8"))
items = b["items"]

SWEEP = ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 43200 "
         "python -u scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_995.npz "
         "--days 995 --limit 50 --folds 5 --seeds 3 --only LS_quant_q30_h5'")

for it in items:
    if it["id"] == "R17":
        it["status"] = "done"
        it["attempts"] = it.get("attempts", []) + [{
            "ts": "2026-09-26T18:45:00+09:00", "rc": 0, "verdict": "달성",
            "detail": ("market_data 3,093,147행 / 910거래일 / 3,970종목 "
                       "(2023-01-02~2026-09-23) — check_target(≥600거래일) 충족. "
                       "연도별: 2023=245일·2024=244일·2025=242일·2026=179일 "
                       "(일별 종목수 2,998~3,926)."),
            "log": "(DB 직접 조회로 검증)",
        }]
        it["result"] = {"verdict": "달성", "delta": None, "rc": 0,
                        "detail": "910거래일 확보 — U3 의 전제 충족"}

    if it["id"] == "U3":
        it["status"] = "pending"
        it["priority"] = 1
        it["est_minutes"] = 700
        it["title"] = ("확장 이력 패널(995일 창 = 659거래일) 재빌드 + 로버스트 재측정 "
                       "— 기존 314거래일 창 대비 2.1배")
        it["hypothesis"] = (
            "학습 표본이 2.1배(314→659거래일)가 되면 로버스트 AUC 가 움직인다 — "
            "지금까지 소진한 라벨·유니버스·피처·하이퍼파라미터 축과 **다른 종류의 레버**다. "
            "창은 피처 워밍업(365일 lookback)이 완전한 최대치로 잡았다.")
        it["evidence"] = (
            "R17 실측: market_data 910거래일(2023-01-02~2026-09-23). 기존 패널(panel_420*, "
            "panel_150u*)은 314거래일 창에 묶여 있었다. U3a(교차패널)·U3b(동일패널 행 A/B)를 "
            "분리해 둔다 — 교차패널 비교는 스냅샷 차이로 부호가 뒤집힌 전례가 있다.")
        it["method"] = (
            "① panel_995.npz 빌드(체크포인트 500페어·재개 가능, 예상 3.5~7.5h) "
            "② 같은 런에서 LS_quant_q30_h5 5폴드×3시드 로버스트 측정 ③ 기록 기준선 0.5406"
            "(314거래일·panel_420_asofpatch)과 비교 — **타 패널 대조라 증거 약함** "
            "④ 창 효과의 확증은 U3b(같은 패널 행 부분집합)로 한다")
        it["command"] = SWEEP
        it["metric"] = "wf_sweep_summary"
        it["arm"] = "LS_quant_q30_h5"
        it["counterfactual"] = "기록 기준선 0.5406 (panel_420_asofpatch · 314거래일 창)"
        it["baseline"] = {
            "value": 0.5406,
            "source": "L1/RB1 실측 · 49종목 · 5폴드×3시드 · 314거래일 창",
            "note": "창이 다르므로 직접 비교는 약한 증거 — U3b 로 격리",
        }
        it["cost"] = "패널 빌드 3.5~7.5h(659거래일×49종목≈32,300페어, 1.2~2.7페어/초) + 측정 ~20분"
        it["success"] = "659거래일 창 로버스트 AUC 가 314거래일 실측(0.5406) 대비 +0.02 이상"
        it["caution"] = ("평일 20:00 컨테이너 재생성 창(est_minutes=700 → 구동기 ETA 가드). "
                         "재개: 같은 명령 재실행(체크포인트 .rows.pkl/.meta.json).")

if not any(i["id"] == "U3b" for i in items):
    items.append({
        "id": "U3b",
        "title": ("창 효과 격리 — 같은 패널(panel_995)에서 314거래일 행만 잘라 A/B "
                  "(동일 스냅샷·동일 피처, 창만 다름)"),
        "status": "backlog",
        "priority": 1,
        "affects_model": True,
        "baseline": {"value": None, "source": "U3 결과로 채운다(같은 런의 659거래일 값)"},
        "hypothesis": ("U3 에서 창이 늘어 AUC 가 변했다면, 그 변화가 정말 '창' 때문인지 "
                       "확인한다. 피처·종목·코드가 동일하고 행 범위만 다르면 차이는 순수한 "
                       "창 효과다(교차패널 비교의 혼입 제거)."),
        "evidence": ("실측 전례: U1 은 교차패널로 Δ−0.0266 으로 기록됐지만 같은 패널 안에서 행 "
                     "집합만 바꾸면 Δ+0.0071(부호 반전)이었다 — 창/표본 비교는 반드시 같은 "
                     "스냅샷 안에서 해야 한다."),
        "method": ("① scripts/panel_subset.py 로 panel_995 → panel_995_w314.npz "
                   "(--since 2025-06-16 = 기존 314거래일 창) ② 같은 설정(LS_quant_q30_h5·"
                   "5폴드×3시드)으로 스윕 ③ Δ(659거래일 − 314거래일) 를 창 효과로 판정"),
        "command": ("docker exec stock_xgboost_ml sh -c 'cd /app && python3 /app/scripts/panel_subset.py "
                    "--src /app/app/models/wf/panel_995.npz --dst /app/app/models/wf/panel_995_w314.npz "
                    "--since 2025-06-16 && OMP_NUM_THREADS=4 timeout 7200 python -u "
                    "scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_995_w314.npz "
                    "--folds 5 --seeds 3 --only LS_quant_q30_h5'"),
        "check": "docker exec stock_xgboost_ml ls -la /app/app/models/wf/panel_995_w314.npz",
        "check_target": {"op": ">=", "value": 1},
        "metric": "wf_sweep_summary",
        "arm": "LS_quant_q30_h5",
        "counterfactual": "U3 실측(같은 패널·659거래일 창) — 원장의 U3 result.per_exp",
        "baseline": {"value": 0.5406, "source": "314거래일 창 기록 기준선(U3b 대조군)"},
        "cost": "부분집합 생성 수 분 + 측정 ~10분",
        "success": "Δ(659 − 314)= +0.02 이상이면 창 효과 실재, |Δ|<0.02 면 '창은 레버가 아니다'로 닫는다",
        "depends_on": ["U3"],
        "caution": "U3 완료 후 실행(U3 결과가 대조군). panel_995.npz 가 없으면 먼저 U3 를 돌려야 한다.",
    })

b["updated_at"] = "2026-09-26T18:50:00+09:00"
tmp = BACKLOG + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(b, f, ensure_ascii=False, indent=2)
import os
os.replace(tmp, BACKLOG)

print("patched:", [i["id"] for i in items if i["status"] in ("pending", "needs_setup")])
for i in items:
    if i["id"] in ("R17", "U3", "U3b"):
        print(i["id"], i["status"], i.get("est_minutes"), "|", str(i.get("title"))[:70])

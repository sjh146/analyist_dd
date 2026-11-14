#!/usr/bin/env python3
"""2026-11-14 2차 갱신 — CG279 완료 기록 + CG280(유니버스 전이 = 생존편향 검정) 등록."""
import json
import os

P = "/home/jhshi/analyist_dd/docs/QUANT_MODEL_BACKLOG.json"
d = json.load(open(P, encoding="utf-8"))
items = d["items"] if isinstance(d, dict) else d
by = {i["id"]: i for i in items}

cg279 = by["CG279"]
cg279["status"] = "done"
cg279["result"] = {
    "verdict": "시간 안정(사전등록 충족) — 전체 95% CI [+0.338,+3.103] 0 배제 · 최근 6개월 Δ +0.936%p(양수)이나 최근 CI 는 0 포함 → 확정 아님, 전이 검정 필요",
    "detail": ("CO_smooth_d1_h5 · h5 · k=3 · 체결성 ON · 수수료 0.21%p · 225세션. 전체 Δ +1.703%p/세션 "
               "(t 3.50 · 양세션 58.7%) 이동블록(10세션·5,000회) 부트스트랩 CI95 [+0.338,+3.103] → 0 배제. "
               "최근 구간(2026-04-01~, 111세션) Δ +0.936%p(t 1.44 · 양세션 57.7%) CI95 [-0.990,+2.751] → 0 포함. "
               "즉 '전 기간 유의·최근 양수이나 최근 구간만으론 유의하지 않음'. 사전등록(CI하한>0 & 최근 Δ>0)은 충족. "
               "⚠ 단 이 검정은 panel_prod200(현 시총 기준 유니버스) 위이고, +1.7%p/5일(연 ~90%p)은 이례적으로 큰 값이라 "
               "생존편향·유니버스 특이·체결가정(종가 매수) 중 하나가 원인일 수 있다 → CG280 전이 검정 전에는 배포 근거로 쓰지 않는다."),
    "delta": 1.703, "per_exp": None, "rc": 0,
}
cg279["command"] = ("docker exec stock_xgboost_ml sh -c 'cd /app && python -u scripts/_cg279_block_bootstrap.py "
                    "--jsonl /app/reports/overnight/cg278_preds_allcodes.jsonl --tag CO_smooth_d1_h5 --k 3 "
                    "--horizon 5 --block 10 --reps 5000 --since 2026-04-01 "
                    "--json-out /app/reports/overnight/cg279_block_bootstrap.json'")
cg279["attempts"] = [{
    "ts": "2026-11-14T18:42:00+09:00", "rc": 0,
    "verdict": cg279["result"]["verdict"],
    "detail": "스크립트 신설(_cg279_block_bootstrap.py) + 즉시 실행 · 새 학습 0 · 1.4분",
    "log": "data/reports/me_cycle/logs/cg279_block_bootstrap.log", "elapsed_min": 1.4}]

cg280 = {
    "id": "CG280", "status": "pending", "priority": 1, "kind": "money", "owner": "engineer",
    "affects_model": False, "metric": "fillable_topk_vs_pool",
    "title": "[돈 지표·전이 검정] CG278/279 의 +1.70%p/5일 초과기대가 panel_prod200 특이인지 — 같은 arm·같은 프로토콜을 49종목 청정 패널에서 재측정",
    "hypothesis": ("CG278 실측으로 배포가능 arm(CO_smooth_d1_h5)이 전 유니버스 풀평균을 h5 k3 +1.703%p/세션"
                   "(t 3.50 · 블록 CI [+0.338,+3.103]) 이겼고 최근 6개월도 +0.936%p 로 양수다. 그런데 CG11/CG5 실측에서 "
                   "같은 config 가 유니버스를 바꾸면 부호가 뒤집혔다(49종목 +0.0227 → 150종목 -0.0109 AUC). "
                   "+1.7%p/5일(연 ~90%p)은 이례적으로 커서 생존편향(현 시총 기준 유니버스 = L5c 미해결)이나 "
                   "200종목 특이 구조일 가능성이 있다. 같은 프로토콜을 49종목 청정 패널(panel_420_asofpatch)에서 "
                   "재현하면 '실재하는 랭킹 엣지'인지 '유니버스 인공물'인지 갈린다."),
    "counterfactual": "같은 패널의 세션 풀평균(전 유니버스·체결성 통과 종목 동일가중)",
    "command": ("bash scripts/cg280_transfer_49.sh  # wf_label_sweep --panel panel_420_asofpatch.npz --folds 5 --seeds 3 "
                "--only CO_smooth_d1_h5,CO_core30_h5 --dump-preds --dump-all-codes + fillable_topk_horizon_sweep "
                "--k 3,5,10 --horizons 1,5 --primary 5"),
    "summary_out": "/app/reports/overnight/cg280_transfer_49.json",
    "paired_reference": "세션 풀평균(49종목 패널)",
    "success": ("49종목 패널에서도 h5 k3 Δ ≥ +0.1%p/세션 & t ≥ 2 면 '전이됨'(그때 배포 후보 검토, 승격 아님) / "
                "Δ ≤ 0 이면 '유니버스 특이'로 종결하고 착수 전제(L5c PIT 유니버스)를 사람 승인 항목으로 올린다"),
    "expected": "Δ 가 0 근처 또는 음(-) — 49종목에서는 표본(세션당 후보 ~20종목)이 작아 k3 효과가 사라질 가능성",
    "cost": "낮음(5폴드×3시드 ≈ 4분 + 집계 1분 · 스크립트 신설 10분)",
    "est_minutes": 15,
    "created_at": "2026-11-14T18:45:00+09:00",
    "setup_needed": ("`scripts/cg280_transfer_49.sh` 신설 필요(다음 틱 자율): cg278 스크립트에서 --panel 만 "
                     "panel_420_asofpatch.npz 로 바꾸고 arm/control 을 CO_smooth_d1_h5 / CO_core30_h5 로, "
                     "--horizons 1,5 --primary 5, summary_out cg280_transfer_49.json. 같은 --dump-all-codes 사용."),
}
if "CG280" not in by:
    items.append(cg280)

d["updated_at"] = "2026-11-14T18:45:00+09:00"
tmp = P + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(d, f, ensure_ascii=False, indent=1)
os.replace(tmp, P)
print("ok", len(items))

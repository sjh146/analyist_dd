#!/usr/bin/env python3
"""CG442 판정 기록 + CG443 등록 + 원장 기록 — CG423 돈 신호의 겹침보정/폴드 LOSO 재판정.

산출물: /tmp/cg442_summary.json (컨테이너 안 cg442_money*.json 에서 뽑은 수치)
"""
import json, datetime

S = json.load(open("/tmp/cg442_summary.json"))
now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")

P = "docs/QUANT_MODEL_BACKLOG.json"
d = json.load(open(P, encoding="utf-8"))
items = d["items"]
by_id = {it["id"]: it for it in items}

c = S["close"]; n = S["next_open"]
c3, c5 = c["ks"]["3"], c["ks"]["5"]
n3, n5 = n["ks"]["3"], n["ks"]["5"]
folds = S["fold_deltas"]
fk5 = [folds[f]["5"] for f in ("f1", "f2", "f3", "f4", "f5")]
fk3 = [folds[f]["3"] for f in ("f1", "f2", "f3", "f4", "f5")]
loso5 = [round(sum(v for j, v in enumerate(fk5) if j != i) / 4, 3) for i in range(5)]
loso3 = [round(sum(v for j, v in enumerate(fk3) if j != i) / 4, 3) for i in range(5)]
reg = S["regime"]; gate = S["exante_gate_trailing5d"]

per_exp = {
    "entry_close": {"k3_delta": c3["delta"], "k5_delta": c5["delta"],
                    "k3_iid_t": c3["iid_t"], "k5_iid_t": c5["iid_t"],
                    "k3_bb_t": c3["bb_t"], "k5_bb_t": c5["bb_t"],
                    "k3_bb_p_one_sided": c3["bb_p"], "k5_bb_p_one_sided": c5["bb_p"],
                    "halves": c5["halves"], "pool_mean": c["pool"], "sessions": c["sessions"]},
    "entry_next_open": {"k3_delta": n3["delta"], "k5_delta": n5["delta"],
                        "k3_iid_t": n3["iid_t"], "k5_iid_t": n5["iid_t"],
                        "k3_bb_t": n3["bb_t"], "k5_bb_t": n5["bb_t"],
                        "halves": n5["halves"], "pool_mean": n["pool"]},
    "fold_deltas_k3": fk3, "fold_deltas_k5": fk5,
    "loso_k3": loso3, "loso_k5": loso5,
    "regime_close": reg,
    "exante_gate_trailing5d": gate,
    "fee_roundtrip_pct": 0.21,
    "effective_independent_sessions": 51,
}

detail = (
    f"사전등록 ②(블록 부트스트랩 t≥2, 블록=5) 미달 — 진입=종가 k3 Δ+{c3['delta']:.3f}(iid t {c3['iid_t']}, "
    f"bb t {c3['bb_t']}) · k5 Δ+{c5['delta']:.3f}(iid t {c5['iid_t']}, bb t {c5['bb_t']}). "
    f"실행가능 진입(익일 시가)에서도 k5 Δ+{n5['delta']:.3f}(bb t {n5['bb_t']}) 로 같은 결론. "
    f"폴드 4/5 양수(k5 {fk5})이나 leave-one-fold-out 최소 +{min(loso5):.3f}(k3 +{min(loso3):.3f})로 "
    f"문턱 +0.5 에 미달. 원인 분해: 상승 {reg['up']['5']['sessions']}세션 Δ-1.106(t -2.55) vs "
    f"하락 {reg['down']['5']['sessions']}세션 Δ+3.143(t 7.09) — 엣지가 '하락국면 방어'에 전부 몰려 있고 "
    f"그 구간 절대기대는 -2.046%p/세션(여전히 손실). 사전(ex-ante) 트레일링 게이트로 갈라도 양 대역 Δ +0.58/+0.71 로 "
    f"국면 예측으로 환금되지 않는다. → 축 종결(미확정): 같은 255세션 재채점 금지, 새 세션 누적 후 CG443."
)

it = by_id["CG442"]
it["status"] = "done"
it["result"] = {"verdict": "노이즈(미확정)", "detail": detail,
                "delta": c5["delta"], "per_exp": per_exp}
it["note"] = (
    "2026-11-23 06:4x 엔지니어 자율(장외 틱, 규칙 4). 재학습 0 — cg410 덤프(5폴드×51날짜=255세션, "
    "날짜 비중첩 2025-10-22~2026-11-05)를 재채점만 했다. 새 방법론(CG439 교훈: h>1 겹침 → 이동블록 부트스트랩, "
    "단일 시드/폴드 지배 → LOSO)을 그대로 적용. 핵심: iid t 2.65 가 겹침 보정 후 1.53 으로 내려간다 "
    "— stride=1 h5 표본의 유효 독립세션은 51개뿐이다. 실제 p-단측 0.045(k3)·0.062(k5) 로 문턱 근처. "
    "⚠ 이 arm(CO_rank_smooth_d1_h5)은 횡단면 rank 변환이라 현 서빙(종목 단위 스트리밍) 경로로 배포 불가 "
    "— 신호가 확정돼도 배포하려면 배치 추론 경로(CG345/CG398, 현재 closed_rejected)가 별도 승인 대상이다. "
    "⚠ 사후 하위집단(하락국면·게이트 대역) 선택은 탐색적이다 — 확증은 CG443 의 사전등록으로만 한다."
)

# CG443 — 확증용(새 세션). 지금은 command 를 확정할 수 없다(패널 구간 연장 선행).
MONEY_CMD_443 = (
    "docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 5400 python -u scripts/wf_label_sweep.py "
    "--panel /app/app/models/wf/panel_prod200_v7.npz --folds 5 --seeds 3 --only CO_rank_smooth_d1_h5 "
    "--dump-preds /app/reports/overnight/cg443_preds.jsonl --out /app/reports/overnight/cg443_wf.jsonl "
    "--summary-out /app/reports/overnight/cg443_wf_summary.json && OMP_NUM_THREADS=2 python -u scripts/fillable_topk_expectancy.py "
    "--arm-jsonl /app/reports/overnight/cg443_dump.jsonl --arm-tag cg443 --control-jsonl /app/reports/overnight/cg443_null.jsonl "
    "--control-tag cg443_null --k 3,5 --exit close_h --horizon 5 --entry next_open --stride 1 --overlap-block 5 "
    "--json-out /app/reports/overnight/cg443_money.json'"
)

items.append({
    "id": "CG443",
    "kind": "experiment",
    "owner": "engineer",
    "affects_model": False,
    "priority": 2,
    "status": "needs_setup",
    "metric": "fillable_topk_vs_pool",
    "summary_out": "/app/reports/overnight/cg443_money.json",
    "est_minutes": 90,
    "not_before": "2026-12-05T20:00:00+09:00",
    "title": ("[돈 지표·확증] CO_rank_smooth_d1_h5 상대 엣지 — 새 세션(≥+15)으로 블록 t≥2 를 다시 본다 "
              "(CG442 종결 후 유일한 재개 경로)"),
    "arm": "CO_rank_smooth_d1_h5 (새 패널 · 5폴드 확장창)",
    "counterfactual": "세션 풀 평균(무작위 k 기대) + 같은 런 hash-null",
    "command": MONEY_CMD_443,
    "hypothesis": ("CG442 실측: Δ k5 +0.894%p/세션(iid t 2.65 · bb t 1.53) · 폴드 4/5 양수 · LOSO 최소 +0.483. "
                   "방향은 일관되나 유효 독립세션이 51개뿐이라 문턱에 못 미친다. 세션이 15개 이상 늘면 "
                   "블록 t 가 2 를 넘는지가 '실재 vs 잡음'을 가른다. 같은 표본 재채점은 금지(p-hacking)."),
    "evidence": ("CG442(2026-11-23): 종가진입 k3 Δ+1.029(bb 1.68)/k5 Δ+0.894(bb 1.53) · 익일시가 k5 Δ+0.941(bb 1.67) · "
                 "폴드 k5 [-0.512,+0.164,+2.021,+2.535,+0.261] · 상승 135세션 Δ-1.106 vs 하락 120세션 Δ+3.143."),
    "success": ("사전 등록(변경 금지): 새 표본에서 ① k=5 Δ(arm−풀) ≥ +0.5%p/세션 ② 블록 부트스트랩(블록=5) t ≥ 2 "
                "③ 폴드 승률 ≥ 4/5 ④ leave-one-fold-out Δ평균 ≥ +0.5. ①~④ 전부 → '상대 엣지 실재'로 확정하고 "
                "그때 배포 경로(배치 추론, 별도 승인) 검토로 넘긴다. ② 또는 ④ 실패 → 축 영구 종결."),
    "setup_needed": ("선행 3단: ① panel_prod200 신선 패널을 --end-date 2026-12-04 로 고정 빌드(구간 밀림으로 체크포인트가 "
                     "폐기되지 않게) ② 패널 날짜 범위가 2026-11-20 이후를 덮는지 확인(현 v6 는 11-05 까지) "
                     "③ 그 패널로 위 command 실행 + hash-null 덤프 생성(cg409_vol_gate.py 의 --out-null 경로 재사용). "
                     "command 의 panel_prod200_v7.npz 이름은 빌드 산출물명에 맞춰 승격 시 확정한다."),
    "expected": "블록 t 1.8~2.3 구간 — 문턱 근처라 어느 쪽으로도 갈 수 있다.",
    "cost": "패널 빌드(야간) + 스윕 5폴드×3시드 ≈ 60~90분",
    "created": now,
})

d["updated_at"] = now
json.dump(d, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

ledger = {
    "ts": now,
    "id": "CG442",
    "title": "[재채점·강건성] CG423 돈 신호(+1.388 t2.38)를 겹침보정 블록t + 폴드 LOSO 로 재판정 — 재학습 0",
    "rc": 0,
    "elapsed_min": 0.6,
    "log": "data/reports/me_cycle/logs/me_cycle_CG442_20261123-0630.log",
    "metric": "fillable_topk_vs_pool",
    "reported": False,
    "parsed": per_exp,
    "verdict": "노이즈(미확정)",
    "detail": detail,
}
with open("data/reports/model_engineer_ledger.jsonl", "a", encoding="utf-8") as fh:
    fh.write(json.dumps(ledger, ensure_ascii=False) + "\n")

print("updated CG442 -> done · registered CG443(needs_setup) · ledger appended")
print("items:", len(items))

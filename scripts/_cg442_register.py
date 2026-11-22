#!/usr/bin/env python3
"""CG442 등록 — CG423 의 돈 신호를 겹침보정 블록t + 폴드 LOSO 로 재판정.

왜: backtest-measurement-validity / CG439 교훈 — h>1 라벨의 stride=1 t 는 겹침으로 부풀려지고,
신호가 특정 폴드(국면)·시드 하나에 몰리면 '실재'가 아니다. CG423(+1.388%p t 2.38)은
그 두 검사를 아직 통과하지 않았다.
"""
import json, datetime

P = "docs/QUANT_MODEL_BACKLOG.json"
d = json.load(open(P, encoding="utf-8"))
items = d["items"]
assert not any(it.get("id") == "CG442" for it in items), "CG442 already registered"

MONEY_CMD = (
    "docker exec stock_xgboost_ml sh -c 'cd /app && "
    "for f in all 1 2 3 4 5; do "
    "if [ \"$f\" = all ]; then DF=\"\"; OUT=cg442_money; else "
    "case $f in 1) DF=\"--date-from 2025-10-22 --date-to 2026-01-05\";; "
    "2) DF=\"--date-from 2026-01-06 --date-to 2026-03-23\";; "
    "3) DF=\"--date-from 2026-03-24 --date-to 2026-06-08\";; "
    "4) DF=\"--date-from 2026-06-09 --date-to 2026-08-20\";; "
    "5) DF=\"--date-from 2026-08-21 --date-to 2026-11-05\";; esac; OUT=cg442_money_f$f; fi; "
    "OMP_NUM_THREADS=2 python -u scripts/fillable_topk_expectancy.py "
    "--arm-jsonl /app/reports/overnight/cg410_dump.jsonl --arm-tag cg409_nogate "
    "--control-jsonl /app/reports/overnight/cg410_null.jsonl --control-tag cg409_hashnull "
    "--k 3,5,10 --sides top,bottom --regime-split pool_sign --exit close_h --horizon 5 "
    "--stride 1 --overlap-block 5 $DF --json-out /app/reports/overnight/$OUT.json || exit 1; "
    "done'"
)

item = {
    "id": "CG442",
    "kind": "experiment",
    "owner": "engineer",
    "affects_model": False,
    "priority": 1,
    "status": "pending",
    "metric": "fillable_topk_vs_pool",
    "summary_out": "/app/reports/overnight/cg442_money.json",
    "est_minutes": 15,
    "title": ("[돈 지표·강건성] CG423 의 '베타 초과 +1.388%p(t 2.38)' 를 겹침보정 블록t + 폴드 LOSO 로 "
              "재판정 — h5·stride1 의 t 인플레와 단일 폴드(국면) 지배를 동시에 배제"),
    "arm": "CO_rank_smooth_d1_h5 (cg410 덤프 재사용 · 게이트 OFF · 5폴드 255세션)",
    "counterfactual": "세션 풀 평균(무작위 k 기대) + 같은 런 hash-null(cg410_null)",
    "command": MONEY_CMD,
    "hypothesis": ("CG423 의 k5 Δ+1.388%p(t 2.38) 는 ① h5 라벨을 stride1 로 겹쳐 세어 만든 t 인플레이거나 "
                   "② 최근 국면 폴드 하나가 만든 값일 수 있다. CG439 에서 같은 크기의 Δ+1.318 이 시드 하나에 "
                   "몰려 '미확정'으로 내려간 선례가 있으므로, 폴드 단위 LOSO 와 블록 부트스트랩을 먼저 통과시킨다."),
    "evidence": ("CG423 실측(2026-11-22 07:20): arm CO_rank_smooth_d1_h5 · 255세션 · k5 Δ+1.388(t 2.38) · "
                 "양세션 56.4% · 분할 both_positive [+0.848/+1.616]. 같은 덤프를 CG424 가 stride1 로 다시 재면 "
                 "k5 Δ+1.216(t 2.06) 이나 분할 unstable — 실행마다 분할표본 판정이 뒤집혔다. "
                 "폴드 구성 실측: 5폴드 × 51날짜 = 255세션(날짜 비중첩 2025-10-22~2026-11-05)."),
    "success": ("사전 등록(변경 금지): ① 메인 런 k=3·k=5 모두 Δ(arm−풀)≥+0.1%p & iid t≥2 "
                "② 블록 부트스트랩(블록=5) t ≥ 2 인 k ≥ 1 ③ 폴드 5개 중 ≥4 에서 Δ>0 이고 leave-one-fold-out "
                "Δ평균이 모두 ≥+0.5%p ④ 분할표본(앞/뒤) both_positive. ①~④ 전부 → '배포 비용을 정당화하는 "
                "돈 엣지 후보'로 승격 후보 등록(승격 자체는 별도 절차). ② 또는 ③ 실패 → '겹침/단일폴드 artifact'로 "
                "축을 닫고 AUC 축·데이터 축(CG388 11-26) 대기로 넘어간다."),
    "expected": ("블록 t 는 iid t 의 40~60% 수준으로 내려갈 것(겹침 h=5). 폴드별 Δ 가 5개 중 3개 이하에서만 "
                 "양수일 가능성 — 그러면 축 종결."),
    "cost": "재채점만(재학습 0) — 메인+폴드 5회 ≈ 3~5분",
    "created": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
}
items.append(item)
d["updated_at"] = item["created"]
json.dump(d, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("registered CG442 · items =", len(items))

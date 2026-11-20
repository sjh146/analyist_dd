#!/usr/bin/env python3
"""CG381(완료·돈 지표 국면 게이트) + CG382(다음 가설) 를 백로그에 등록한다(멱등).

근거: CG380(노이즈, event_rolling 강제포함 Δ+0.0025) → 축 종결.
      CG374(짝 Δ+0.0278) 의 머니 후속(cg374_paired_money.json)이 이미 산출돼 있었고,
      챔피언 top5 −3.13%p/세션·재학습 −3.51%p/세션(둘 다 음수, 짝 Δ t 0.34) → 승격 금지 유지.
      이어서 '진입 시점에 알 수 있는 국면 신호'로 세션을 거르는 첫 머니 실험을 실행(CG381).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
PATH = "docs/QUANT_MODEL_BACKLOG.json"

NOW = datetime.now(KST).replace(microsecond=0)

CG381 = {
    "id": "CG381",
    "status": "done",
    "priority": 2,
    "kind": "돈지표",
    "owner": "엔지니어",
    "affects_model": False,
    "metric": "fillable_topk_expectancy",
    "arm": "cg374_champion_s0..2",
    "title": ("[돈 지표·국면 게이트] 진입 시점에 알 수 있는 과거 5세션 풀수익 부호로 세션을 거르면 "
              "챔피언 top-k 순기대가 양(+)으로 돌아서는가 — 재학습 0, 기존 덤프 재사용"),
    "hypothesis": ("CG374 머니 후속 실측: 챔피언 top5 −3.13%p/세션(t −2.29) · 재학습 −3.51%p/세션 · "
                   "두 arm 모두 절대 음수이고 짝 Δ +0.57%p(t 0.34) = AUC +0.0278 이 돈으로 환산되지 않았다. "
                   "절대 음수의 원인이 종목선택이 아니라 '하락 국면 노출'이면, 과거 5세션 풀수익 부호라는 "
                   "PIT 신호로 세션을 거를 때 ON 밴드의 절대 기대가 0 위로 올라와야 한다."),
    "command": ("docker exec stock_xgboost_ml sh -c 'cd /app && for s in 0 1 2; do OMP_NUM_THREADS=2 timeout 900 "
                "python -u scripts/fillable_topk_expectancy.py --arm-jsonl /app/reports/overnight/cg374_champion_s$s.jsonl "
                "--control-jsonl /app/reports/overnight/cg374_retrain_cg346_s$s.jsonl --k 3,5 --exit close_h --horizon 5 "
                "--gate-rule trailing_pool --gate-days 5 --gate-min-pct 0.0 "
                "--json-out /app/reports/overnight/cg381_gate_s$s.json; done'"),
    "counterfactual": ("같은 런의 off 밴드 + 무게이트 cg374_paired_money.json(챔피언 top5 −3.13%p/세션 · "
                       "필터 max_day_chg 25% · 거래대금 10억 · 수수료 왕복 0.21%p)"),
    "success": ("① ON 밴드 세션 ≥15 ② k=5 net > 0 이고 t ≥ 2 ③ 3시드 부호 일치(≥2/3) "
                "④ 하나라도 미달이면 '국면 게이트 무효'로 축 종결, 초과하면 트레이더 환류 후보로 등록"),
    "expected": "재학습 0 · 3~5분으로 '모델 이전에 국면이 음수를 만드는가'를 가른다.",
    "rationale": "측정은 이미 있는 덤프로 끝난다. 게이트는 진입 시점 정보만 쓴다(룩어헤드 없음).",
    "est_minutes": 5,
    "created_at": NOW.isoformat(),
    "needs_approval": False,
    "result": {
        "verdict": "노이즈",
        "detail": ("ON 밴드(과거 5세션 풀수익>0) 세션이 4/4/5개뿐 · k=5 net −1.84(t −1.06) / +2.52(t 1.31) / "
                   "−3.65(t −2.04)%p — 3시드 부호 불일치·표본 미달 → 게이트 무효. "
                   "다만 ON 밴드 '풀' 전방 h5 평균이 −5.60/−5.49/−4.77%p(t −2.71/−5.21/−4.68)로 전 시드 일관 "
                   "음수 = 트레일링 양(+) 이후 h5 반전 후보(같은 풀이라 시드 3개는 독립표본 아님, n=23세션). "
                   "OFF 밴드 풀 −2.75/−3.06/−2.78%p · 챔피언 top5 −1.39~−3.22%p → 무게이트 값과 일치."),
        "delta": None,
        "rc": 0,
        "evidence": ["services/xgboost-ml/reports/overnight/cg381_gate_s0.json",
                     "services/xgboost-ml/reports/overnight/cg381_gate_s1.json",
                     "services/xgboost-ml/reports/overnight/cg381_gate_s2.json"],
    },
}

CG382 = {
    "id": "CG382",
    "status": "pending",
    "priority": 2,
    "kind": "돈지표",
    "owner": "엔지니어",
    "affects_model": False,
    "metric": "fillable_topk_expectancy",
    "arm": "CG376_noroll_h5__ff",
    "title": ("[머니 우선] 오늘 최고 AUC arm(38피처·0.5500)이 체결가능 순기대에서도 챔피언을 이기는가 — "
              "같은 창 덤프 후 날짜 짝 비교(AUC 1등 = 돈 1등인가)"),
    "hypothesis": ("AUC 축 상한이 22사이클 +0.011 인 상태에서 유일하게 남은 질문은 'AUC 우위가 돈으로 환산되는가'다. "
                   "CG374 는 AUC +0.0278 → 짝 머니 Δ +0.57%p(t 0.34) 로 환산 실패를 보였다(1쌍). "
                   "패널 v6 최고 AUC arm(0.5500, 강제 8집계 포함 38피처)을 같은 필터로 재면 그 실패가 일반적인지 갈린다."),
    "command": ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 3600 python -u scripts/wf_label_sweep.py "
                "--panel /app/app/models/wf/panel_prod200_v6.npz --folds 5 --seeds 5 --only CG376_noroll_h5__ff "
                "--dump-preds /app/reports/overnight/cg382_ff.jsonl --dump-all-codes "
                "--out /app/reports/overnight/cg382_sweep.jsonl --summary-out /app/reports/overnight/cg382_summary.json && "
                "for s in 0 1 2; do OMP_NUM_THREADS=2 timeout 900 python -u scripts/fillable_topk_expectancy.py "
                "--arm-jsonl /app/reports/overnight/cg382_ff.jsonl --control-jsonl /app/reports/overnight/cg374_champion_s$s.jsonl "
                "--k 3,5 --exit close_h --horizon 5 --max-day-chg 25 --min-value 1e9 "
                "--json-out /app/reports/overnight/cg382_money_s$s.json; done'"),
    "counterfactual": ("cg374 챔피언 덤프(같은 체결필터·같은 28세션 · top5 −3.13%p/세션) — 두 arm 의 학습창은 다르므로 "
                       "'날짜 교집합 짝'으로만 해석하고 절대값 비교는 하지 않는다"),
    "success": ("① rc=0 · 덤프에 3시드 × 전종목 행 존재 · 날짜 교집합 ≥20세션 ② 짝 Δ(arm−챔피언) top3 ≥ +1.0%p 이고 "
                "t ≥ 2 이며 3시드 중 ≥2 양(+) 이면 'AUC 우위가 돈으로 환산된다' 후보로 등록(승격은 별도) "
                "③ |Δ| < +1.0%p 이거나 부호 불일치면 'AUC 1등은 돈 1등이 아니다'로 AUC→돈 환산 축을 닫는다"),
    "expected": "약 25~35분. 22사이클 무개선의 원인이 '축 고갈'인지 'AUC↔돈 단절'인지 판정 근거를 만든다.",
    "rationale": ("최우선 규칙(모든 실험은 돈 지표로 보고)의 마지막 미측정 조합이다. 덤프 경로(--dump-preds --dump-all-codes)와 "
                  "체결성 필터·수수료 0.21%p 는 CG169/CG306 에서 검증된 경로를 그대로 쓴다."),
    "est_minutes": 40,
    "created_at": NOW.isoformat(),
    "needs_approval": False,
    "not_before": NOW.isoformat(),
}


def main() -> int:
    with open(PATH, encoding="utf-8") as f:
        doc = json.load(f)
    items = doc["items"]
    have = {it["id"] for it in items}
    added = []
    for it in (CG381, CG382):
        if it["id"] in have:
            print(f"[skip] {it['id']} 이미 있음")
            continue
        items.append(it)
        added.append(it["id"])
    doc["updated_at"] = NOW.isoformat()
    doc["updated"] = NOW.date().isoformat()
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    print("[ok] added:", added, "| total:", len(items))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

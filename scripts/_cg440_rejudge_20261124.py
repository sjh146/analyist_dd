#!/usr/bin/env python3
"""CG440 사후 정정 + 후속 가설(CG459) 등록 — 2026-11-24 05:2x 엔지니어.

왜: 2026-11-24 03:09 실행분(not_before 2026-12-02 이전 강행, 세션 20/26)이
`summary_out=..._s0.json` 하나만 읽혀 **단일 시드**로 '노이즈' 판정됐다.
같은 런의 5시드 집계 실측 Δ+1.5244(4/5 양수, LOO 최소 +1.256, mean t 1.41,
arm 절대 −2.8503, 양(+) 시드 0/5) → 근거가 다르다.
"""
import json
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
now = datetime.now(KST).isoformat(timespec="seconds")
p = "docs/QUANT_MODEL_BACKLOG.json"
d = json.load(open(p, encoding="utf-8"))
items = {i["id"]: i for i in d["items"]}

b = items["CG440"]
b["metric"] = "money_seed_family"
b["summary_out"] = "data/reports/overnight/cg440_seed_family_pool.json"
b["status"] = "pending"
b["command"] = b["command"] + (
    " ; for s in 0 1 2 3 4; do docker cp stock_xgboost_ml:/app/reports/overnight/cg440_money_s${s}.json "
    "/tmp/cg440/ ; done ; python3 scripts/money_seed_family.py --glob '/tmp/cg440/cg440_money_s*.json' "
    "--null pool --ks 3,5,10 --out data/reports/overnight/cg440_seed_family_pool.json")
b["note"] = (
    "2026-11-24 05:2x 엔지니어: 03:09 실행분은 **측정무효**로 기록한다 — ① not_before(2026-12-02 20:00) 이전 "
    "강행(--start --force)이라 사전등록 표본(세션 26)에 미달(실측 세션 20, OOS 2026-10-02~11-17 31일) "
    "② 그런데도 판정은 summary_out=..._s0.json **한 시드**로 내려졌다(구동기 파서가 시드 루프를 모름). "
    "같은 런 5시드 집계(scripts/money_seed_family.py --null pool): k=5 Δ +1.5244(sd 1.10, 4/5 양수, "
    "pooled 64/36, mean t +1.41, LOO 최소 +1.256) · arm 절대 −2.8503%p(양(+) 시드 0/5) · 모든 시드 "
    "분할 neither. → 시드 부호 불일치(s1 −0.564)라 하드룰상 신호 아님. 파서는 시드 루프+단일 시드 요약을 "
    "'판정불가'로 닫도록 수리했고, 이 항목은 metric=money_seed_family 로 바꿔 12-02 재실행 때 집계로 판정한다. "
    "부수 관찰: 이 창의 풀 평균이 −3.65%p/세션이라 절대 순기대는 구조적으로 음(−)이다 — 상대 랭킹 Δ로만 "
    "평가해야 하는 창이었다(절대 알파 축은 CG459 로 분리).")
b.setdefault("attempts", [])
b["attempts"].append({
    "ts": now, "rc": 0, "verdict": "측정무효",
    "detail": ("사전등록 표본 미달(세션 20 < 26, not_before 2026-12-02 이전 강행) + 단일 시드(s0) 판정. "
               "5시드 집계 Δ+1.5244(4/5 양수, mean t 1.41, LOO 최소 +1.256) · arm 절대 −2.8503%p(0/5 양수) "
               "→ 시드 부호 불일치. 12-02 재실행 대기로 상태 pending 복귀."),
    "log": "data/reports/me_cycle/logs/me_cycle_CG440_20261124-030905.log",
})
b["result"] = {
    "verdict": "측정무효",
    "detail": ("세션 20/26 · 단일 시드 판정 → 5시드 집계로 정정: k=5 Δ+1.5244(4/5, mean t 1.41, LOO +1.256) "
               "· arm 절대 −2.8503%p(0/5) · 분할 neither(전 시드)"),
    "evidence_file": "data/reports/overnight/cg440_seed_family_pool.json",
}

if "CG459" not in items:
    d["items"].append({
        "id": "CG459",
        "kind": "experiment",
        "owner": "engineer",
        "affects_model": False,
        "priority": 2,
        "status": "needs_setup",
        "metric": "money_seed_family",
        "summary_out": "data/reports/overnight/cg459_seed_family_pool.json",
        "est_minutes": 90,
        "not_before": "2026-12-02T20:00:00+09:00",
        "title": "[돈 지표·신규축] 시장 국면 게이트 — h5 절대 순기대가 '상승 국면'에서만 양(+)인가 (CG440 5시드 실측 후속)",
        "arm": "champion+trailing_pool_gate(high)",
        "counterfactual": "같은 런의 무게이트 롱(항상 top-k) + 세션 풀 평균(hash-null)",
        "hypothesis": ("CG440 5시드 실측에서 상대 랭킹 Δ는 +1.52%p/세션(4/5 양수)인데 절대 순기대는 "
                       "−2.85%p/세션(0/5 양수)이었다 — 이 창의 풀 평균이 −3.65%p/세션이라 **베타가 알파를 덮었다**. "
                       "따라서 모델 개선이 돈이 되려면 '언제 롱할지'가 필요하고, 직전 5세션 풀수익(국면)으로 "
                       "게이트하면 절대 순기대가 양(+)으로 뒤집히는지가 이 축의 질문이다."),
        "evidence": ("2026-11-24 CG440 5시드: pool −3.654/−3.952/−6.123/−4.038/−4.106 (전부 음) · "
                     "arm −2.066/−4.516/−3.904/−2.257/−1.508 · Δ +1.588/−0.564/+2.219/+1.781/+2.597. "
                     "배포 챔피언 돈 기준(113세션/1771거래) 순기대 −0.3431%p(t −1.72) — 무게이트 롱은 이미 음(−)."),
        "success": ("사전 등록(변경 금지): k=5 · stride=1 · exit close_h h5 · 수수료 왕복 0.21%p 기준 "
                    "① 게이트 ON(직전 5세션 풀수익>0) 조건의 **절대** 순기대 ≥ +0.5%p/세션 "
                    "② 블록 부트스트랩(블록=5) t ≥ 2 ③ 무게이트 대비 절대 기대 +1.0%p 이상 개선 "
                    "④ 5시드 중 ≥4 시드가 ①~③ 동시 충족(시드 부호 유지) "
                    "⑤ 게이트 ON 세션 수 ≥ 8 (표본 문턱). ①~⑤ 전부 → '국면 게이트'를 승격 게이트 표본 정의 "
                    "안건으로 리뷰보드에 올린다(승격 아님). ④ 또는 ⑤ 실패 → 국면 게이트 축 종결."),
        "expected": ("상승 국면 세션이 표본의 1/3 이하면 ⑤ 미달로 닫힐 가능성이 크다 — 그 경우 결론은 "
                     "'롱온리 전략은 이 시장에서 알파 없음'으로 축을 닫고 데이터 축(인트라데이 CG450)으로 간다."),
        "cost": "기존 덤프 재채점 5시드 × 2조건 ≈ 15분 + 새 표본 확보(세션 ≥+15) 대기",
        "command": ("for s in 0 1 2 3 4; do docker exec stock_xgboost_ml sh -c \"cd /app && "
                    "OMP_NUM_THREADS=2 python -u scripts/fillable_topk_expectancy.py --arm-jsonl "
                    "/app/reports/overnight/cg440_champion_s${s}.jsonl --arm-tag champion_s${s}_gate "
                    "--control-jsonl /app/reports/overnight/cg440_retrain_cg346_s${s}.jsonl "
                    "--control-tag retrain_cg346_s${s} --k 5 --exit close_h --horizon 5 --stride 1 "
                    "--gate-rule trailing_pool --gate-side high --gate-days 5 --overlap-block 5 "
                    "--json-out /app/reports/overnight/cg459_money_s${s}.json\" ; docker cp "
                    "stock_xgboost_ml:/app/reports/overnight/cg459_money_s${s}.json /tmp/cg459/ ; done ; "
                    "python3 scripts/money_seed_family.py --glob '/tmp/cg459/cg459_money_s*.json' "
                    "--null pool --ks 5 --out data/reports/overnight/cg459_seed_family_pool.json"),
        "note": ("2026-11-24 05:2x 엔지니어 자율 신설. **같은 20세션 재채점은 판정이 아니다**(다중비교) — "
                 "12-02 이후 표본이 늘어난 같은 커맨드로만 판정한다. 게이트 세션 수가 8 미만이면 판정불가로 두고 "
                 "12월 중순 재측정. 덤프(cg440_*_s*.jsonl)는 이미 있으므로 재학습 비용 0."),
        "created": now,
        "attempts": [],
    })

d["updated_at"] = now
json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("OK", now, "items", len(d["items"]))

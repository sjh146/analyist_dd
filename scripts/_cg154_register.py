#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""quant-model-engineer 야간 틱(2026-10-09 23:1x) 백로그 갱신.

- CG150(후보 돈 번역=노이즈) → 후보 승격 축 종결 note
- CG114(하위 꼬리 재시험 금지) → CG151 상시성 확인 note(재시험 아님·일관성 대조)
- CG154(신규 pending): 랭크 IC 표본·종목수 확대 검정(검정력 경계)
- CG155(신규 pending): CG154 산출물의 돈 번역
"""
import json, shutil, datetime, os

P = "docs/QUANT_MODEL_BACKLOG.json"
bak = P + ".bak_20261009_2310"
shutil.copy2(P, bak)

d = json.load(open(P, encoding="utf-8"))
items = d["items"]
by = {i["id"]: i for i in items}
now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")

assert "CG154" not in by and "CG155" not in by, "already present"

# ── 1) CG150 폐쇄 note ────────────────────────────────────────────────
c150 = by["CG150"]
c150["note"] = (str(c150.get("note") or "") +
    " | 2026-10-09 23:1x 엔지니어 자율(야간 틱): **후보 승격 축 종결** — 후보(champion_cand)는 "
    "로버스트 AUC 짝 우위 +0.0291(5/5 앵커 부호일관, CG147)를 갖지만 돈 짝 Δ 는 +0.024%p/세션"
    "(t 0.13, 세션 100)이고 k=5 는 -0.092(t -0.72)다. 표본을 30→100 세션으로 3.3배 늘리자 "
    "CG149 의 +0.589%p(t1.70)가 소멸했다 → **AUC 만 오른 후보는 승격 금지(최우선 규칙 4)**, "
    "이 축은 닫는다. 승격 후보 아님.")

# ── 2) CG114 상시성 note(재시험 아님) ──────────────────────────────────
c114 = by["CG114"]
c114["note"] = (str(c114.get("note") or "") +
    " | 2026-10-09 23:1x 야간 틱 일관성 대조(신규 실험 아님·재시험 금지 준수): 새 산출물"
    "(cg150_*.jsonl, champion/champion_cand, exit next_open)에서 하위 k 를 다시 재 보니 "
    "k=3 Δ(bottom-k − 풀평균) -0.186(t -1.92) · k=5 -0.139(t -1.78) · k=10 -0.095(t -1.55) "
    "(|t|<2), bottom-k vs 챔피언 Δ -0.060(t -0.36) → **CG99/CG114 의 '하위 꼬리 반전 = 노이즈' "
    "결론이 새 모델·새 exit 에서도 유지된다**(축 폐쇄 유효). 새 항목으로 등록하지 않는다.")

# ── 3) CG154 ───────────────────────────────────────────────────────────
cmd154 = ("docker exec stock_xgboost_ml sh -c 'cd /app && "
 "OMP_NUM_THREADS=2 timeout 5400 python -u scripts/champion_robust_eval.py --universe training "
 "--label-kind rel --horizon 5 --folds 5 --dates-per-fold 25 --stocks 150 "
 "--model-dir app/models/champion --asof-date 2026-10-07 "
 "--dump-preds /app/reports/overnight/cg154_champ.jsonl --dump-all "
 "--out /app/reports/overnight/cg154_champ.json && "
 "OMP_NUM_THREADS=2 timeout 5400 python -u scripts/champion_robust_eval.py --universe training "
 "--label-kind rel --horizon 5 --folds 5 --dates-per-fold 25 --stocks 150 "
 "--model-dir app/models/champion_cand --asof-date 2026-10-07 "
 "--dump-preds /app/reports/overnight/cg154_cand.jsonl --dump-all "
 "--out /app/reports/overnight/cg154_cand.json && "
 "python -u scripts/rank_ic_money.py --arm-jsonl /app/reports/overnight/cg154_cand.jsonl "
 "--arm-tag champion_cand --control-jsonl /app/reports/overnight/cg154_champ.jsonl "
 "--control-tag champion --fillable --json-out /app/reports/overnight/cg154_rank_ic.json'")

cg154 = {
 "id": "CG154",
 "title": "후보·챔피언 랭크 IC 의 검정력 경계 판정 — 같은 세션집합에서 종목수만 60→150 으로 늘려 IC t1.96 이 t≥2 로 확정되는가",
 "status": "pending",
 "priority": 1,
 "kind": "measure",
 "owner": "engineer",
 "affects_model": False,
 "created_at": now,
 "arm": "champion_cand vs champion (app/models/champion_cand vs app/models/champion · 같은 앵커 2026-10-07 · 같은 5폴드×25일 세션집합 · 150종목 · 체결성 필터 ON)",
 "counterfactual": "CG153 실측(같은 dumps·60종목·n100): cand mean_IC +0.0451 · t 1.96 · 양세션 57.0% · 앞/뒤 0.0245/0.0657 · champ mean_IC 0.0000(t 0.0) · 짝 ΔIC +0.0293(t 0.82)",
 "command": cmd154,
 "metric": "rank_ic_money",
 "success": "사전등록(도구 기본값, 변경 금지): arm mean_IC>0 · t≥2.0 · 양(+)세션≥55% · 앞/뒤 절반 모두 양(+) → '후보가 랭킹에 돈 방향 정보를 갖는다'(순기대의 필요조건). 미달이면 종목수 확대에도 미검출 = 후보 랭킹 정보 없음(축 종결). ⚠ IC 는 순기대가 아니다 — 승격 근거가 될 수 없다.",
 "expected": "평가 2런(150종목×125일) ≈60~70분 + 집계 ≈2분 (동시 학습/평가 금지 · 구동기 lock 이 직렬화)",
 "est_minutes": 90,
 "hypothesis": "CG150 의 돈 지표는 세션수에 갇혀 검출바닥 Δ≈0.27%p/세션(arm-vs-pool)·0.38%p(arm-vs-control)인데 승격 문턱은 +0.1%p 다. 랭크 IC 는 같은 세션집합에서 세션당 종목수에 반비례해 sd_ic 가 줄어드므로(∝1/√n), 60→150종목이면 CG153 의 sd_ic 0.230 → ≈0.145 이고 같은 IC +0.0451 에서 t≈3.1 이 된다. 즉 '후보 랭킹 정보 없음'이 아니라 '검정력 부족'이었는지를 같은 조건에서 종목수만 늘려 판정한다.",
 "note": "2026-10-09 23:1x 야간 틱 엔지니어 자율 등록. 왜 이 가설인가: 19사이클 무개선의 원인 후보는 ①정보가 없다 ②검정력이 없다 인데, 지금까지 돈 축은 검정력 부족으로 '미검출'과 '신호없음'을 구분하지 못했다(CG111: k=3 sd 0.415). IC 도구는 CG113 에서 t4.53 을 낸 전례(=같은 표본에서 검정력이 한 자릿수 높다)가 있고, 새 후보는 60종목에서 t1.96 으로 문턱에 붙어 있어 **가장 먼저 판정 가능해지는 경계 사례**다. 결과가 어느 쪽이든 결론이 선명하다: 신호있음 → 소비 정책(분위 top-k) 쪽으로 넘기고 승격은 별도 절차/미검출 → '후보 AUC 우위는 정적 틸트 산물'로 종결. 파생: CG155 가 같은 dumps 의 돈 번역을 맡는다.",
 "attempts": [],
}

# ── 4) CG155 ───────────────────────────────────────────────────────────
cmd155 = ("docker exec stock_xgboost_ml sh -c 'cd /app && "
 "python -u scripts/fillable_topk_expectancy.py "
 "--arm-jsonl /app/reports/overnight/cg154_cand.jsonl --arm-tag champion_cand "
 "--control-jsonl /app/reports/overnight/cg154_champ.jsonl --control-tag champion "
 "--k 3,5,10 --exit next_open --horizon 5 "
 "--json-out /app/reports/overnight/cg154_money.json'")

cg155 = {
 "id": "CG155",
 "title": "CG154 산출물의 돈 번역 — 150종목 확대가 top-k 순기대 짝 Δ 의 검출바닥을 실제로 낮추는가",
 "status": "pending",
 "priority": 3,
 "kind": "measure",
 "owner": "engineer",
 "affects_model": False,
 "created_at": now,
 "arm": "champion_cand vs champion (CG154 와 같은 dumps · 같은 앵커 · 체결성 필터 ON · 수수료 왕복 0.21%p)",
 "counterfactual": "CG150 실측(60종목·n100 세션: k3 Δ+0.024%p t0.13 → 노이즈)",
 "command": cmd155,
 "metric": "fillable_topk_expectancy",
 "success": "사전등록(변경 금지 · 승격 표준과 동일 단위): k=3 과 k=5 둘 다 arm 순기대>0 & 짝 Δ(arm−대조군) ≥ +0.1%p/세션 & 짝 t ≥ 2 & arm 분할 both_positive → 신호있음. 미달이면 '종목수 60→150 확대(풀 중앙값 상승 = 바스켓 분산 감소)에도 베타 초과 미검출' 로 기록하고 후보 돈 축을 닫는다.",
 "expected": "집계 1분(CG154 산출물 재사용 — 학습 없음)",
 "est_minutes": 5,
 "hypothesis": "CG150 의 k=3 짝 Δ 는 세션당 풀 중앙값 25종목에서 나왔다(풀 대부분이 필터로 탈락). CG154 는 같은 세션을 150종목으로 채점하므로 같은 k 에서 바스켓이 더 분산돼 per-session sd 가 줄고, 검출바닥(Δ sd)이 낮아진다 → 같은 효과크기에서 t 가 올라간다. 즉 '돈으로 번역되지 않는다'가 '번역할 검정력이 없었다'인지 확인한다.",
 "note": "2026-10-09 23:1x 야간 틱 엔지니어 자율 등록. CG154 와 순서 의존(같은 jsonl 재사용) — priority 3 으로 CG154(1) 뒤에 돈다. CG154 미완주면 rc=2(파일 없음)로 실패한다(그 사실이 보고된다). 미리 보는 파일: /app/reports/overnight/cg154_cand.jsonl · cg154_champ.jsonl.",
 "attempts": [],
}

items.append(cg154)
items.append(cg155)
d["updated_at"] = now
d["updated"] = now
json.dump(d, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

# 검증
d2 = json.load(open(P, encoding="utf-8"))
ids = [i["id"] for i in d2["items"]]
assert ids.count("CG154") == 1 and ids.count("CG155") == 1, "insert failed"
from collections import Counter
print("status counts:", Counter(i.get("status") for i in d2["items"]))
print("pending:", [(i["id"], i.get("priority"), i.get("metric")) for i in d2["items"] if i.get("status") == "pending"])
print("OK · backup =", bak)

#!/usr/bin/env python3
"""CG522 판정 기록 + CG523 등록 (2026-11-27 엔지니어 자율 틱).

- data/reports/cg522_panel_universe_audit.json : 감사 증거(파일:라인 + 실측 수치)
- docs/QUANT_MODEL_BACKLOG.json : CG522 결과 채우기 · CG523 pending 등록
- data/reports/model_engineer_ledger.jsonl : CG522 사후 판정 1건(파서 부재로 판정불가였던 것)
"""
import json
import os
from datetime import datetime, timedelta, timezone

PROJ = "/home/jhshi/analyist_dd"
KST = timezone(timedelta(hours=9))
now = datetime.now(KST).isoformat(timespec="seconds")

EVIDENCE = {
    "generated_at": now,
    "question": "패널 절대 기대(+0.41~+0.95%p/세션)가 실 전방(−0.34%p)과 반대인 이유 — 유니버스가 "
                "전 구간(미래) 정보로 골라졌는가",
    "verdict": "부분 확인 — 생존편향은 실재하나 크기가 작고(+0.15%p/5d), 관측된 격차의 주 원인은 "
               "①기간효과(풀 +1.02%p → −0.76%p) ②유니버스 불일치(라이브 top5 ∩ 패널200 = 5.2%)다. "
               "→ 패널 절대 %p 는 어떤 경우에도 인용 금지(사전등록 ② 충족).",
    "code_evidence": [
        "services/xgboost-ml/app/training/universe.py:41-59 — 적격 집합 = s.market IN (KOSPI,KOSDAQ) "
        "AND instrument_type='STOCK', 최근 60일 창에서 30거래일 이상 + ETF/ETN 이름 제외",
        "services/xgboost-ml/app/training/universe.py:62-74 — 창 시작 = (UNIVERSE_ASOF_DATE|now) − 60일 "
        "→ 선택 시점이 빌드일이므로 '그 시점 생존 종목'만 후보(생존편향)",
        "services/xgboost-ml/app/training/universe.py:167-188 — 최신일 내림차순 + seed 셔플(recency)",
        "scripts/train_curated.py:83-96 — curated 경로(UNIVERSE_SQL)는 since(기본 2026-04-01) 이후 "
        "AVG(trading_value) 로 정렬 가능하지만 기본 order='code'·min_value=0 → 유동성 선택 아님",
        "scripts/wf_wave.py:231-258 — universe='prod' 면 select_training_universe(limit=200,min_days=30,seed)",
    ],
    "measurements": {
        "panel": "panel_prod200_v6o.npz · 61,640행 · 314거래일(2025-08-04~2026-11-17) · 200종목",
        "market_mix_today": {"KOSDAQ": 138, "KOSPI": 62},
        "reproducibility_overlap": {
            "select_training_universe(recency,seed=0)_today": "75/200 (37.5%)",
            "select_training_universe(liquidity)_today": "16/200",
            "curated_KOSDAQ_code_order_today": "14/200",
            "curated_all_market_min_value_1e8": "9/200",
        },
        "survivorship_inflation": {
            "window": "2025-08-04~2026-08-03 (선택 창 이전 구간)",
            "panel_199_codes_mean_fwd5_pct": 0.8132,
            "all_market_mean_fwd5_pct": 0.6633,
            "inflation_pctp_per_5d": 0.1499,
            "note": "사라진(2026-07 이전 데이터 종료) 53종목의 평균 fwd5 +12.2% 는 상장폐지 직전 "
                    "가격공백 오염으로 편향 추정에 쓰지 않았다.",
        },
        "period_effect_panel_pool_equal_weight_fwd5": {
            "2025-08-04~2026-11-05 (306세션)": 1.0242,
            "2025-10-22~2026-11-05 (255세션)": 1.1347,
            "2026-09-15~2026-11-05 (34세션)": -0.7590,
            "2025-08-04~2026-07-31 (242세션)": 1.0509,
            "live_forward_h5_pool_pct": -1.6872,
        },
        "panel_edge_persists": {
            "source": "CG521 money(패널 200, stride1, 255세션)",
            "arm_minus_pool_k5_full_pctp": 1.0192,
            "arm_minus_pool_k5_recent34_pctp": 1.0785,
            "recent34_t": 1.39, "recent34_pos": "20/34",
        },
        "universe_mismatch": {
            "live_predictions_avg_codes_per_session": 3909.4,
            "champion_top5_in_panel200": "7/135 = 5.2% (2026-10-01~11-26)",
            "live_cross_section_deciles": "17세션 · 3,615종목/일 · ρ −0.103 · D10−D1 +0.1417%p(t 0.26) "
                                          "· 풀 −1.8812%p · top10 −4.2662%p/5d",
        },
    },
    "conclusion_for_protocol": [
        "패널 돈 지표의 절대 %p 는 인용 금지 — 같은 런·같은 세션·같은 유니버스의 Δ만 인용한다.",
        "패널 Δ(+1.02%p/5d)도 라이브 대리지표가 아니다: 라이브는 하루 3,615종목 횡단면을 채점하고 "
        "챔피언 상위5가 패널200에 드는 비율이 5.2%다.",
        "라이브 횡단면에서는 순서 정보가 없다(ρ −0.10 · D10−D1 +0.14%p) → 다음 레버는 "
        "'점수 개선'이 아니라 '채점 대상 유니버스 정합'과 '라이브 유니버스 재학습'이다.",
    ],
}

path = os.path.join(PROJ, "data/reports/cg522_panel_universe_audit.json")
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "w", encoding="utf-8") as f:
    json.dump(EVIDENCE, f, ensure_ascii=False, indent=1)

DETAIL = (
    "유니버스 선택 = select_training_universe(recency) — 빌드일 기준 최근 60일에 30거래일 이상 + ETF/ETN 제외"
    "(universe.py:41-74) · 유동성 200 아님(오늘 시장구성 138 KOSDAQ/62 KOSPI) · 재현성 75/200(recency seed0)"
    " · 생존편향 실측 +0.15%p/5d(패널 +0.8132 vs 시장 +0.6633, 2025-08~2026-08) · "
    "절대값 반전은 기간효과(패널 풀 +1.02%/5d 전구간 → −0.76%/5d 최근 34세션, 라이브 h5 풀 −1.69) · "
    "그러나 라이브 상위5 ∩ 패널200 = 5.2%(7/135)이고 라이브 횡단면(3,615종목/일·17세션) ρ −0.103·D10−D1 "
    "+0.1417%p(t 0.26) → 패널 Δ는 라이브 엣지의 대리지표가 아니다. 증거: data/reports/cg522_panel_universe_audit.json"
)

with open(os.path.join(PROJ, "docs/QUANT_MODEL_BACKLOG.json"), encoding="utf-8") as f:
    b = json.load(f)
items = b["items"]

cg522 = next(i for i in items if i["id"] == "CG522")
cg522["status"] = "done"
cg522["finished_at"] = now
cg522["result"] = {"verdict": "생존편향·기간효과·유니버스불일치 3중 오염 — 패널 절대 %p 인용 금지",
                   "detail": DETAIL, "delta": None, "per_exp": None, "rc": 0}
cg522["note"] = (cg522.get("note", "") +
                 f" | {now} 엔지니어 사후 판정(구동기는 파서 부재로 '판정불가'로 닫았다): " + DETAIL)
cg522["reported"] = True
cg522["evidence"] = "data/reports/cg522_panel_universe_audit.json"

if not any(i["id"] == "CG523" for i in items):
    items.append({
        "id": "CG523",
        "title": "[돈 지표·유니버스 정합] 배포 유니버스(하루 ~3,600종목)에서 챔피언 점수의 순서 정보를 "
                 "재라 — 패널200 Δ(+1.02%p/5d)는 라이브 대리지표인가 (상위5 ∩ 패널 = 5.2%)",
        "status": "pending",
        "priority": 1,
        "kind": "돈 지표·유니버스정합",
        "owner": "engineer",
        "affects_model": False,
        "metric": "score_deciles",
        "est_minutes": 5,
        "hypothesis": "CG522 감사: 라이브 예측은 하루 3,531~3,909종목을 채점하는데 챔피언 상위5가 "
                      "패널200에 드는 비율이 5.2%(7/135)다 → 패널 안에서 잰 top-k Δ(+1.02%p/5d)는 "
                      "모델이 실제로 선택하는 횡단면의 엣지가 아니다. 라이브 횡단면에서 점수의 10분위 "
                      "순기대가 단조 증가하면 '채점 유니버스'와 무관하게 순서 정보가 있고, 평평하면 "
                      "모델은 자기 패널 밖에서 정보가 없어 유니버스 정합(또는 라이브 재학습)이 선행 조건이다.",
        "counterfactual": "패널 200 CG521 stride1: arm−pool k5 +1.0192%p(255세션) · 최근 34세션 +1.0785%p"
                          "(t 1.39, 20/34) ↔ 라이브 전방 h5 topk−풀 Δ−0.220%p(t −0.14, 7d)",
        "success": "사전 등록(변경 금지): 라이브 횡단면 분위 ρ ≥ +0.5 **이고** D10−D1 ≥ +0.3%p 이면 "
                   "'순서 정보 있음'(패널 밖에서도 유효). 미달이면 '랭킹 무효' — 유니버스 정합/재학습 축으로 이동.",
        "command": "cd /home/jhshi/analyist_dd && timeout 600 python3 scripts/cg523_live_universe_deciles.py "
                   "--json-out data/reports/overnight/cg523_summary.json",
        "expected": "17세션·6.1만행·초 단위 실행(읽기 전용 SQL). 판정은 구동기 judge_score_deciles 가 한다.",
        "cost": "낮음(2초·읽기 전용)",
        "created_at": now,
        "updated_at": now,
        "note": f"{now} 엔지니어 자율 — CG522 후속. 사전 실행값(수동 1회): ρ −0.103 · D10−D1 +0.1417%p"
                f"(t 0.26, 양 10/음 7, 앞 −0.30/뒤 +0.54) · 풀평균 −1.8812%p · top10 −4.2662%p/5d "
                f"→ 사전문턱 미달(랭킹 무효). 구동기 틱이 같은 명령을 다시 돌려 원장에 자기 판정을 남긴다.",
    })

with open(os.path.join(PROJ, "docs/QUANT_MODEL_BACKLOG.json"), "w", encoding="utf-8") as f:
    json.dump(b, f, ensure_ascii=False, indent=1)

ledger = os.path.join(PROJ, "data/reports/model_engineer_ledger.jsonl")
rec = {
    "ts": now, "id": "CG522", "title": cg522["title"],
    "rc": 0, "elapsed_min": None, "log": cg522.get("log", ""),
    "metric": "panel_universe_audit", "recovered": True,
    "parsed": {"verdict_auto": "판정불가였음(parser 부재)", "evidence": "data/reports/cg522_panel_universe_audit.json",
               "panel_market_mix": {"KOSDAQ": 138, "KOSPI": 62},
               "repro_overlap_recency_seed0": "75/200",
               "survivorship_inflation_pctp_5d": 0.1499,
               "panel_pool_fwd5_full_pct": 1.0242, "panel_pool_fwd5_recent34_pct": -0.7590,
               "live_top5_in_panel200": "7/135"},
    "verdict": "생존편향·기간효과·유니버스불일치 3중 오염 — 패널 절대 %p 인용 금지",
    "detail": DETAIL,
    "reported": True,
    "note": "구동기 기록 보정 — 항목 metric 'panel_universe_audit' 에 파서가 없어 07:08 틱이 "
            "'판정불가'로 닫았다. 같은 사이클에서 코드·DB 읽기 전용 실측으로 사후 판정했고, "
            "구동기는 이후 '파서 없는 metric 은 done 으로 닫지 않는다'(needs_setup)로 수리했다.",
}
with open(ledger, "a", encoding="utf-8") as f:
    f.write(json.dumps(rec, ensure_ascii=False) + "\n")

print("OK", path, cg522["status"], len(items))

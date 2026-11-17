#!/usr/bin/env python3
"""2026-10-11 04:1x 엔지니어 장외 틱 — 백로그 갱신(CG164·CG165 + CG166 신설)."""
import json
import os
from datetime import datetime

P = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "docs/QUANT_MODEL_BACKLOG.json")
b = json.load(open(P, encoding="utf-8"))
items = {i["id"]: i for i in b["items"]}
NOW = "2026-10-11T04:15+09:00"

# ── CG164: setup ① (플래그 구현) 완료 표기 ──────────────────────────────────
cg164 = items["CG164"]
cg164["evidence"] += (
    " | 2026-10-11 04:0x 엔지니어 자율(재현·구현). ① **프로브 재실행**(장외 틱): "
    "`docker exec -e PYTHONPATH=/app stock_xgboost_ml sh -c 'cd /app && python -u "
    "scripts/_cg164_publish_input_probe.py'` → verdict=\"이름 드리프트 — 최대 6개가 0.0 으로 대체됨\" "
    "(engine_keys=173 · missing_of_champion=6 = rank_return_5d/20d·rank_volatility_20d·"
    "rank_volume_ratio_5/20·rank_ma_position_5) · 표본 3/4에서 champion_vec_exact_zero 58~66/173. "
    "`features_used` 는 67,779행 전부 nonempty=0(입력 스냅샷 없음) 재확인. "
    "② **setup ① 구현 완료**: `app/training/retrain_champion.py` 에 `--drop-serving-blind-features` "
    "(+ `SERVING_BLIND_FEATURES`) 신설 — 제거 대상이 **모델 피처 목록에서도** 빠진다(목록에 남으면 "
    "서빙에서 0.0 으로 되살아난다). meta 에 `drop_serving_blind`·`dropped_serving_blind` 기록. "
    "자체점검 `scripts/_cg164_drop_blind_test.py` → **6/6 PASS**(OFF = FeaturePipeline 목록 199개와 "
    "비트 동일 · ON = canonical − rank_* 6개 · 순서 유지 · meta 기록 · OFF 무회귀). "
    "③ 부수 실측: FeaturePipeline canonical = **199피처** 인데 배포 champion = **173피처**(2026-09-23 학습) "
    "→ 배포 모델은 파이프라인보다 26피처 뒤처져 있다(별도 정합 이슈, CG166 재학습이 함께 해소).")
cg164["setup_needed"] = (
    "① (**완료**, 2026-10-11 04:0x) 서빙 불가 피처를 빼는 재학습 플래그 — `--drop-serving-blind-features` "
    "구현·자체점검 6/6 PASS(기본 OFF = 프로덕션 비트 동일). 이제 실행 가능 → 실행 항목은 CG166 으로 분리. "
    "② (**승인 대상**) 서빙측에 배치 rank 를 실제로 공급하는 길 — 추론 계약 변경(당일 전 종목 배치 추론)이라 "
    "리뷰보드 대상. ⚠ 둘 중 하나만 택하면 된다: ② 를 승인하면 rank 를 **살릴 수 있고**, 승인하지 않으면 "
    "CG166(제거 재학습)이 라이브 입력 정합을 회복한다. 어느 쪽도 'rank 를 성능 레버로 재시험'하는 것이 아니다.")
cg164["note"] += (
    " | 2026-10-11 04:1x: setup ① 을 구현로 종결(자체점검 6/6). 남은 실행은 CG166 이며, 이 항목은 "
    "② 승인 판단 대기로 남긴다.")

# ── CG165: 원인 규명 + 자율 반쪽 완료(게이트 배선·백필) ─────────────────────
cg165 = items["CG165"]
cg165["title"] = ("[데이터신선도] 배선된 피처 원천 3종(supply_market_features·financial_ratio_features·"
                  "macro_features)이 09-28/29 정지 — **스케줄이 아예 없다**(12일간 무보고)")
cg165["command"] = "python3 scripts/source_freshness_gate.py"
cg165["metric"] = "source_freshness"
cg165["counterfactual"] = (
    "현행: `supply_market_features` max(trade_date)=**2026-09-29**(09-29 는 1행만) · "
    "`financial_ratio_features` **2026-09-29** · `macro_features` **2026-09-28** vs 기준 `market_data` "
    "**2026-10-08** → 지연 6·6·7거래일(09-30~10-08 결손). 같은 시각 정상: foreign_institutional·"
    "event_features·krx_short_selling·disclosures 전부 10-08(지연 0). 미배선 원천 스윕은 이름 그대로 "
    "**미배선**만 보고, 배선된(=모델 입력) 원천의 신선도는 어떤 틱도 보지 않았다.")
cg165["hypothesis"] = (
    "세 테이블은 모두 **파생 피처 빌더**(scripts/build_supply_market_features.py · "
    "build_financial_ratio_features.py · build_macro_features.py)가 만든다. 원천 수집(foreign_institutional·"
    "macro_indicators·krx_short_selling)은 크론이 돌아 신선한데 파생 빌더만 멈췄다 → 원인은 '수집 실패'가 "
    "아니라 **스케줄 부재**다(빌더를 부르는 크론·파이프라인 단계가 어디에도 없다).")
cg165["evidence"] = (
    "2026-10-11 04:0x~04:1x 엔지니어 자율(장외 틱, 읽기 전용 + 백필 실행). "
    "① **DB 실측**: supply_market_features 1,082,213행·max 2026-09-29(09-28 3,733행 → 09-29 **1행** → 이후 무행) · "
    "financial_ratio_features 779,914행·max 2026-09-29 · macro_features 315행·max 2026-09-28. 대조: "
    "market_data/stock_prices/foreign_institutional/event_features/krx_short_selling/disclosures = 2026-10-08. "
    "② **원인 규명(스케줄 부재)**: `/etc/cron.d/analyist_dd`·사용자 crontab·`/home/jhshi/cron/*.sh`·"
    "`scripts/full_pipeline_dd.sh` 어디에도 두 빌더 호출이 없다(재현: `grep -rn build_supply_market_features "
    "/etc/cron.d/ ~/cron/*.sh scripts/full_pipeline_dd.sh` → 0건). 반면 같은 계열 파생 빌더 event_features 는 "
    "'이벤트 피처 빌더 (평일 19:10)' 잡이 있어 신선도가 유지된다 — **이 셋만 잡이 없다**. "
    "③ **게이트 배선(자율·감지 전용)**: `scripts/source_freshness_gate.py` 신설(기준=market_data 최신 거래일, "
    "원천별 거래일 지연: ≤1 OK · 2~4 WARN · ≥5 CRITICAL, 오탐 가드로 report_date 기반 financial_statements 제외) "
    "+ 구동기 `source_freshness_note()` 를 매 틱에 배선(캐시 1h, 실측 5.6s/콜드·0.0s/캐시). "
    "자체점검 `scripts/_source_freshness_test.py` **11/11 PASS**. 게이트 첫 실행이 곧 발견이다: "
    "CRITICAL — macro_features 7 · supply 6 · financial_ratio 6거래일. "
    "④ **백필 실행(자율)**: `scripts/run_cg165_backfill.sh`(setsid) → supply_market_features "
    "**rc=0 · 삭제 1,082,213 → 삽입 1,108,353행(2025-06-16~2026-10-08) · 315.7s** → max(trade_date)=**2026-10-08** "
    "확인(결손 6거래일 해소) · financial_ratio 빌더 진행 · macro 는 대기 런처(`run_cg165_macro_wait.sh`)가 "
    "직렬 실행(동시 실행 금지).")
cg165["success"] = (
    "① 세 테이블의 거래일 지연이 <2거래일로 돌아온다(백필 완료: supply 2026-10-08 확인) ② 재발하지 않는다 — "
    "빌더가 **매일 자동** 실행되거나, 늦어도 게이트가 WARN 이상을 틱 보고에 남긴다(게이트 배선 완료: "
    "`python3 scripts/source_freshness_gate.py` → 지연 표 + JSON).")
cg165["setup_needed"] = (
    "남은 것은 **승인 1건(크론 추가)** 뿐이다 — 자율 반쪽(게이트 배선·백필·자체점검)은 완료. "
    "① 일일 스케줄: 세 빌더를 매 평일 장 마감 후(예: 20:40, evening pipeline 뒤) 1회 실행하는 크론 잡 추가 "
    "(event_features 평일 19:10 잡과 동형 — `/home/jhshi/.hermes/cron/jobs.json` 또는 `~/.hermes/scripts/`에 "
    "tick 스크립트). 크론 수정은 스킬 기준 승인 대상이라 여기서 멈춘다. "
    "② (선택) 리서처 소유 감시로 이관 — 이 게이트는 엔지니어 쪽 임시 안전망이다.")
cg165["note"] = (
    "2026-10-11 03:0x 엔지니어 자율(장외 틱) 신설 → 같은 세션 04:0x 에 원인 규명·게이트 배선·백필까지 완료. "
    "**이 항목은 새 성능 레버가 아니다**(변환·게이트·라벨 재시험 금지) — 데이터 계약·측정 신뢰도 항목이다. "
    "⚠ 같은 뿌리의 위험: 파생 빌더에 스케줄이 없으면 다음에도 조용히 멈춘다(수집기 크론이 있어도 "
    "**파생 단계**는 안 돈다). 감시는 '테이블 존재'가 아니라 **날짜 지연**으로 해야 잡힌다.")

# ── CG166 신설: CG164 ① 의 실행(제거 재학습) ────────────────────────────────
b["items"].append({
    "id": "CG166",
    "title": "[배포정합·실행] 서빙 불가 피처(rank_* 6)를 제거한 재학습 + 로버스트 OOS — CG164 ① 실현",
    "status": "pending",
    "priority": 1,
    "kind": "deploy_integrity",
    "owner": "engineer",
    "affects_model": True,
    "arm": "champion_cand_cg166(199−6=193피처, --drop-serving-blind-features)",
    "baseline": "배포 champion(173피처·09-23 학습) · 등록 기준선 0.5406",
    "metric": "champion_robust_eval",
    "command": ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 16200 python -u "
                "app/training/retrain_champion.py --end-date 2026-10-08 --days 90 --stock-limit 200 "
                "--out-dir /app/app/models/champion_cand_cg166 --drop-serving-blind-features "
                "--checkpoint-path /app/app/models/wf/ckpt_cg166 && OMP_NUM_THREADS=4 timeout 5400 python -u "
                "scripts/champion_robust_eval.py --model-dir app/models/champion_cand_cg166 "
                "--out app/reports/champion_robust_eval.json'"),
    "counterfactual": (
        "현행 배포 champion(173피처, 2026-09-23 학습 — rank_* 6개가 서빙에서 0.0) · "
        "플래그 OFF 재학습(같은 구간)은 **대조군 arm** 으로만 의미가 있다(별도 예산 1회)."),
    "hypothesis": (
        "서빙이 공급할 수 없는 6개(rank_*)를 **모델 피처 목록에서도** 빼고 같은 구간 데이터로 재학습하면, "
        "라이브 입력계와 학습 입력계가 일치한다(스큐 0). rank 축 자체의 신호 상한은 +0.011 로 닫혀 있으므로 "
        "**개선이 아니라 무회귀 + 정합 회복**을 기대값으로 둔다."),
    "evidence": (
        "선행: CG164 evidence ② (플래그 구현·자체점검 6/6 PASS) · 프로브 실측(173 중 6개 missing). "
        "재학습 구간은 --end-date 2026-10-08 로 **고정**(체크포인트 키 유지 — 매일 '오늘'로 밀면 매일 폐기)."),
    "success": (
        "① 서빙 입력 정합: 재학습 산출물의 feature_names 에 rank_* 가 없고 `_cg164_publish_input_probe.py` 의 "
        "`missing_of_champion == 0` ② 로버스트(다중폴드) AUC 가 기준선 대비 **무회귀**(Δ ≥ −0.005) — "
        "여기서 끝나면 '배포 가능한 라이브 입력계 확보'로 기록한다. Δ ≥ +0.02 면 별도 항목으로 승격 검토(승격은 "
        "이 루프에서 하지 않는다). 개선을 주장하려면 같은 런·같은 구간의 플래그 OFF 대조군이 필요하다."),
    "expected": (
        "개선 기대는 낮다(rank 축 상한 +0.011). 얻는 것은 ① 라이브 입력 정합 ② 배포 champion 이 파이프라인보다 "
        "26피처 뒤처진 문제의 동시 해소 ③ CG162 스코어 압축의 '모델 신원' 가설 반증/지지."),
    "cost": "재학습 200종목×90일(≈18k 페어, 실측 ~1.3페어/s → 3.5~4.5h) + 로버스트 eval ~20분. 장외 창 필요.",
    "est_minutes": 260,
    "setup_needed": None,
    "note": (
        "2026-10-11 04:1x 엔지니어 자율(장외 틱) 신설 — CG164 setup ① 이 완료되어 실행 항목으로 분리했다. "
        "⚠ 새 성능 레버 시험이 아니다(rank 축은 TR1/TR2/CG130 으로 닫힘). ⚠ 평일이면 15:35 이후 착수(장중 금지) · "
        "컨테이너 timeout 은 실측 ETA(≈16200s)로 여유를 뒀다. ⚠ 다른 백필과 **동시 실행 금지**(4코어)."),
    "registered": NOW,
    "attempts": 0,
})

b["updated_at"] = NOW
b["updated"] = NOW
json.dump(b, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("items:", len(b["items"]))
for i in b["items"]:
    if i["id"] in ("CG164", "CG165", "CG166"):
        print(" ", i["id"], i["status"], i["priority"], i["metric"])
print("pending:", [i["id"] for i in b["items"] if i["status"] == "pending"])

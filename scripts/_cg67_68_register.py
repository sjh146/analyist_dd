#!/usr/bin/env python3
"""CG67·CG68 백로그 등록 (2026-10-02 18:2x, 야간 자율 세션).

CG67: 패널의 disclosure_count_5d 가 전 행 0 인 결함(원천에는 42% 커버리지)을 as-of 재계산으로
      부활시키고 게이트 ON A/B → 모델 Δ.
CG68: 배포 경로 **전방(forward) 성적표** — ml_predictions × 실현 선행수익 (읽기 전용 계측기).

같은 실행에서 CG9 노트를 갱신한다(명령이 실재하지 않는 플래그 `--rank-pct`/`--depth`/`--lr` 를
가리키고, 성공기준이 학습구간 이전 3창뿐이라 검정력이 없다는 사실을 명시).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
PATH = "/home/jhshi/analyist_dd/docs/QUANT_MODEL_BACKLOG.json"

b = json.load(open(PATH))
items = b["items"] if isinstance(b, dict) else b
have = {i.get("id") for i in items}

CG67 = {
    "id": "CG67",
    "title": "공시/이벤트 축 배선 결함 수리 — 패널 disclosure_count_5d 전 행 0 (원천엔 패널 유니버스 42% 커버리지) 부활 A/B",
    "status": "done",
    "priority": 2,
    "affects_model": True,
    "hypothesis": "panel_420_asof3 의 `disclosure_count_5d` 는 0/13,609 로 죽어 있는데, 같은 유니버스·같은 구간에서 `disclosures`(1,712행·49종목 전원)로 as-of(5일 윈도우) 재계산하면 5,816/13,769=42.2% 셀이 비영이 된다 → 데이터 부재가 아니라 **패널 빌드 시점에 원천이 비어 있었던 배선 결함**. 되살린 밀도 피처가 top30 선별에 들어가면 게이트 ON AUC 가 움직인다.",
    "evidence": "① `scripts/_event_axis_probe.py` 실측(2026-10-02 18:0x): disclosures(패널 유니버스·패널 구간) 1,712행·49종목 / as-of 재계산 시 disclosure_count_5d>0 예상 셀 5,816/13,769(42.2%) / 패널 컬럼은 0/13,609. ② 패널의 news_count_5d 는 13,328/13,609 로 살아 있고 event_stake_change_5d 는 222 로 일부 배선됨 → 공시 카운트만 죽은 상태. ③ base 패널 panel_420_asofpatch 는 2026-09-24 빌드인데 disclosures 는 그 뒤 채워졌다.",
    "method": "① scripts/patch_panel_events.py 신설 — 기존 npz 의 해당 컬럼만 as-of 규칙(disclosures: rcept_dt<=date AND rcept_dt>=date-5d)으로 재계산(전체 재빌드 대체). ② panel_420_asof3 → panel_420_asof4ev (스모크 --limit 2000 19.80% → 전량 3,734/13,609=27.44% 비영). ③ 같은 3-arm(5폴드×5시드, LS_quant_q30_h5·CO_core30_h5·CO_smooth_d1_h5) 스윕으로 CG64/CG66 과 동일 프로토콜 A/B.",
    "command": "docker exec stock_xgboost_ml python /app/scripts/patch_panel_events.py --in app/models/wf/panel_420_asof3.npz --out app/models/wf/panel_420_asof4ev.npz && docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 3000 python -u scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_420_asof4ev.npz --folds 5 --seeds 5 --only LS_quant_q30_h5,CO_core30_h5,CO_smooth_d1_h5'",
    "counterfactual": "panel_420_asof3 (같은 행·같은 다른 피처, disclosure_count_5d 만 죽어 있음) — CG64/CG66 실측 CO_core30_h5 0.5350±0.0189 · CO_smooth_d1_h5 0.5512±0.0218 · LS_quant_q30_h5 0.5374±0.0305/0.5381±0.0282",
    "success": "게이트 ON 대조군 대비 짝 Δ ≥ +0.02 (폴드 std ±0.02 수준이므로 미만은 노이즈)",
    "result": {
        "verdict": "노이즈",
        "detail": "panel_420_asof4ev(5폴드×5시드, disclosure_count_5d 비영 0→27.44%): CO_core30_h5 **0.5350±0.0189**(CG64/CG66 과 소수점까지 동일) · CO_smooth_d1_h5 **0.5512±0.0218**(동일) · LS_quant_q30_h5 0.5383±0.0281(CG66 0.5381 → Δ+0.0002). 게이트 ON 두 arm 이 비트 동일 = 되살린 컬럼이 top30 선별에 **들어가지 않았다**(학습행렬 불변). 게이트 OFF 에서는 Δ+0.0002 = 노이즈. → 공시 카운트 축은 모델 기여 0. 배선 결함 수리 자체는 완료(컬럼이 더 이상 죽어 있지 않음).",
        "delta": None,
        "per_exp": None,
        "rc": 0,
    },
    "note": "2026-10-02 18:1x 크론 세션에서 구동기 밖(포그라운드)으로 실행 — 원장 기록 없음(감사 흔적은 이 항목, CG66 과 같은 방식). 패널의 '전 행 0' 컬럼을 데이터 부재로 단정하지 말고 원천의 유니버스·구간 실효 커버리지를 먼저 찍어라(교훈 누적). ⚠ 실행 중 `services/xgboost-ml/reports/overnight/wf_label_sweep_summary.json` 을 덮어쓴다(사전 백업 /tmp/wf_summary_pre_cg67.json, CG64/CG66 수치는 백로그·원장에 보존).",
}

CG68 = {
    "id": "CG68",
    "title": "배포 경로 전방(forward) 성적표 — ml_predictions × 실현 선행수익 (읽기 전용 계측기 신설)",
    "status": "done",
    "priority": 1,
    "affects_model": False,
    "hypothesis": "지금까지 배포 경로 OOS 는 전부 창 기반 champion_robust_eval 인데 학습구간이 항상 최신까지라 남는 창이 학습구간 **이전**뿐이다(CG45·CG58·CG61·CG62 실측) → 전방 검증이 없다. 실거래 경로가 매일 남기는 `ml_predictions`(confidence·predicted_direction) 를 실현 선행수익과 조인하면 진짜 전방 표본이 된다.",
    "evidence": "DB 실측: ml_predictions 37,377행·2026-09-22~10-02·4,342종목·model_version 'v1.0', confidence 0.0000~0.8914(일별 4,340행).",
    "method": "scripts/forward_scorecard.py 신설 — ① ml_predictions(종목·날짜·confidence) ② market_data 종목별 종가 시계열로 r_h = close(t+h)/close(t)-1 계산(h=1·5) ③ pooled AUC + 날짜별 횡단면 AUC 평균 + base rate + 날짜별 top10 평균 실현수익 + 전체 평균.",
    "command": "docker exec stock_xgboost_ml python /app/scripts/forward_scorecard.py",
    "counterfactual": None,
    "success": "매일 누적 실행으로 h5 전방 AUC·top10 실현수익의 표본이 쌓이는 것(판정 문턱 아님 — 계측기).",
    "result": {
        "verdict": "전방 우위 미검출 (표본 부족, 누적 필요)",
        "detail": "1차 실행(2026-10-02 08:12 UTC): h1 n=14,093페어·5일 pooled AUC **0.5468**·날짜별 평균 0.5272 [0.5000, 0.6234, 0.5233, 0.4620]·상승 base rate 52.5%·top10 평균 실현수익 **−0.028%** vs 전체 평균 **+0.714%**. h5 n=2,570페어·2일 pooled AUC **0.5003**·날짜별 0.5000·top10 **+1.18%** vs 전체 **+3.24%**. → 배포 경로의 전방 신호는 이 짧은 창에서 동전과 구분되지 않고, confidence 상위 종목이 오히려 전체 평균을 하회한다(표본 5일/2일이라 판정 아님 — 누적 대상).",
        "delta": None,
        "per_exp": None,
        "rc": 0,
    },
    "note": "⚠ 이 항목을 원장에 per_exp 로 넣지 말 것(스코어보드가 arm 최고값으로 오독). 계측기는 표본이 쌓여야 의미가 있다 — 일 1회 실행 배선(크론 추가)은 승인 대상으로 올렸다. window·라벨 정의상 '전방'이지만 ml_predictions 의 model_version 이 'v1.0' 단일이라 10-01 승격/10-02 롤백 전후 모델 구분은 불가(개선 요청).",
}

if "CG67" not in have:
    items.append(CG67)
if "CG68" not in have:
    items.append(CG68)

for it in items:
    if it.get("id") == "CG9":
        old = str(it.get("note") or "")
        it["note"] = (
            "⚠ 2026-10-02 갱신: 이 항목의 `command` 는 **실재하지 않는 플래그**를 가리킨다"
            "(`--rank-pct`·`--depth`·`--lr`). 실제 구현은 `--label-kind {h1_direction,rel,rel_smooth}`"
            " · `--horizon` · `--model-params '{\"max_depth\":1,\"learning_rate\":0.05}'` 이므로 A단계는"
            " **이미 구현 완료**다(추론 계약 무변경). B단계(rank 를 추론에 적용)는 app/inference/predictor.py"
            " 단일종목 스트리밍 경로 때문에 **계약 변경 = 승인 대상**으로 남는다. "
            "⚠ 성공기준 재검토 필요: champion_robust_eval 의 정직한 창은 학습구간 **이전** 3개뿐이라"
            " SE≈0.08 → +0.02 를 검출할 힘이 없다(CG38 실측). 배포 경로 A/B 는 CG68 전방 성적표로"
            " 누적 측정하는 쪽이 옳다. " + old[:400]
        )

b["updated_at"] = datetime.now(KST).isoformat(timespec="seconds")
json.dump(b, open(PATH, "w"), ensure_ascii=False, indent=2)
print("ok: items =", len(items), "| added:", [i["id"] for i in (CG67, CG68) if i["id"] not in have])

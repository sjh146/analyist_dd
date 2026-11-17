#!/usr/bin/env python3
"""일회성: CG255 결과 보강 + CG256/CG259(done)·CG260(pending) 등록."""
import json
from datetime import datetime

PATH = "docs/QUANT_MODEL_BACKLOG.json"
b = json.load(open(PATH))
items = b["items"]
now = datetime.now().astimezone().replace(microsecond=0).isoformat()

by_id = {it["id"]: it for it in items}

# ---- 1) CG255: 스크린 원문 답을 '유동성 피처 유무'로 특정 ----
cg255 = by_id["CG255"]
cg255["status"] = "done"
cg255["finished_at"] = "2026-11-13T23:05:00+09:00"
cg255["result"]["verdict"] = "② 무정보/부재 — 패널에 '거래대금·시총 수준' 피처가 없다"
cg255["result"]["answer"] = (
    "panel_prod200 213피처 실측: 거래대금/시총 계열 횡단면 피처 **0개**. "
    "이름에 value/volume 이 들어가는 12개는 전부 밸류에이션 비율(value_pe·value_pbr·value_psr·value_pcr·value_pfcr·value_ev_ebit·value_ncav) "
    "또는 **상대** 거래량(volume_ratio_5 0.5057·volume_ratio_20 0.5049·rank_volume_ratio_5 0.5053 t1.09·rank_volume_ratio_20 0.5053 t1.70·volume_price_trend 0.4741·price_volume 0.5038·volatility_volume 0.4768) 이고 "
    "절대 거래대금(krw)·시가총액 수준은 한 컬럼도 없다. krx_total_trading_value 는 시장레벨(단일값)이라 횡단면 랭킹에 못 쓴다. "
    "→ CG253 의 '거래대금 하위' 신호는 패널이 표현하지 못하는 축이다(① 이 아니라 ②). 이 시점에서 '유동성 rank 신규 피처'가 데이터 축 후보로 등록될 조건이 성립."
)

# ---- 2) CG256 (done): 패널 유니버스 안에서 유동성 신호 재측정 = 후보 기각 ----
items.append({
    "id": "CG256",
    "status": "done",
    "priority": 1,
    "kind": "measurement",
    "owner": "engineer",
    "affects_model": True,
    "metric": "panel_liq_money",
    "from_experiment": "CG255",
    "title": "[데이터 축·모델] 유동성 rank 신규 피처는 AUC 레버인가 — 패널 유니버스(prod200) 안에서 같은 측정을 먼저 한다(재빌드 전 게이트)",
    "hypothesis": "CG255 가 '거래대금 피처가 패널에 없다'로 끝났으므로 다음 수는 패널 재빌드(수 시간)다. 그러나 재빌드 전에 확인할 것이 있다: 돈 신호는 SNS 관측 풀(약 120종목/일)에서 쟀고, 모델은 200종목 유니버스를 랭크한다. 모델 유니버스 안에서 같은 신호가 살아 있지 않으면 재빌드는 낭비다.",
    "counterfactual": "같은 패널·같은 날짜에서 ① 거래대금 30억 하한(신호 측정 조건과 동일) ② 하한 없음 ③ 10억 하한 — 세 조건의 Δ·t·분할",
    "command": "docker exec stock_xgboost_ml sh -c 'cd /app && timeout 600 python -u scripts/panel_liq_probe.py --panel /app/app/models/wf/panel_prod200.npz --money-k 5,10 --min-value 0 --json-out /app/reports/overnight/cg256_panel_liq_mv0.json'",
    "arm": "거래대금 하위 k (k=5,10) · 패널 유니버스 200종목",
    "baseline": "같은 날 같은 필터 풀평균(무작위 k 기대) · 수수료 왕복 0.21%p",
    "success": "Δ ≥ +0.10%p & t ≥ 2 & 분할 both_positive & 세션 ≥ 20 → 유동성 피처 재빌드 착수. 미달 → '유동성 축은 모델 레버가 아니다'로 후보 기각(패널 재빌드 미착수, 비용 0).",
    "expected": "전 시장 실측(CG251)이 Δ+0.29%p·절대 음(−)이었으므로 모델 유니버스에서도 미달 예상.",
    "cost": "낮음(DB 조회 + 순위계산, 1분)",
    "est_minutes": 2,
    "created_at": "2026-11-13T23:10:00+09:00",
    "finished_at": "2026-11-13T23:15:00+09:00",
    "summary_out": "/app/reports/overnight/cg256_panel_liq_mv0.json",
    "result": {
        "verdict": "후보 기각 — 패널 유니버스에서 저거래대금 신호는 살아있지 않다(비단조)",
        "detail": (
            "패널 panel_prod200(200종목·279일·2025-08-04~2026-09-23)에서 실측. "
            "[30억 하한(신호와 동일 조건)] 평균 47종목/일: fwd1 k=5 Δ−0.082%p(t −0.54) · k=10 Δ−0.105(t −1.11) · fwd5 k=5 +0.073(t 0.21) · k=10 +0.058(t 0.26) → 전부 문턱 미달. "
            "[하한 없음] 평균 197종목/일: IC log(tv)↔fwd1 −0.0341(t −2.84 · 양일 45.8%) · ↔fwd5 −0.0348(t −3.09) — 방향은 CG253 과 같지만 "
            "**top-k 돈 지표는 반대로 간다**: fwd1 k=5 Δ−0.216(t −1.25) · k=10 Δ−0.244(t −2.01, 분할 [+0.086/−0.571] ✗) · fwd5 k=10 Δ−0.531(t −1.90). "
            "→ 즉 '거래대금 하위'는 극단(최하위 5~10종목)에서 오히려 손실이다(비단조). "
            "IC 는 유의하지만 |IC| 0.034 는 패널 스크린에서 이미 무정보 판정 받은 군(rank_return_5d −0.0325·bb_position −0.0194)과 같은 크기다. "
            "결론: 유동성 rank 신규 피처는 재빌드(수 시간)를 정당화하지 못한다 → 데이터 축 후보에서 기각, CG253 신호는 'SNS 관측 풀 조건화'의 산물로 기록."
        ),
        "delta": -0.2437,
        "rc": 0,
    },
    "evidence": "scripts/panel_liq_probe.py(신설, 이번 사이클) · /app/reports/overnight/cg256_panel_liq_mv0.json · mv3e9/mv1e9 파일 동봉",
    "note": "⚠ 패널 스냅샷이 2026-09-23 까지라 최근 국면(10~11월)은 못 본다. CG253 의 60일 창은 09-14~11-13 이므로 창이 겹치지 않는다 — 이건 '유니버스' 비교이고 '창' 비교가 아니다.",
})

# ---- 3) CG259 (done): 체결 비용(종가 30분창 고저폭) 실측 ----
items.append({
    "id": "CG259",
    "status": "done",
    "priority": 2,
    "kind": "measurement",
    "owner": "engineer",
    "affects_model": False,
    "metric": "liq_execution_cost",
    "from_experiment": "CG254",
    "title": "[돈 지표·비용] 저거래대금 Δ(+1.20%p)의 슬리피지 0.5%p 는 가정이었다 — 15:01~15:30 창의 실측 고저폭으로 확인",
    "hypothesis": "CG254 는 슬리피지 0.5%p 를 가정값으로 두고 Δ 불변성을 보였다. 그러나 하한 30억 종목이 실제로 얼마나 흔들리는지는 미실측이었다. minute_bars 는 15:01~15:30 만 있지만(수집기 결함 XR26) **종가 진입 전략의 체결 창이 바로 그 구간**이므로 고저폭을 체결비용 상한 프록시로 쓸 수 있다.",
    "counterfactual": "같은 날 종목들을 거래대금 10분위로 나눠 D1(최저)과 전체의 30분창 고저폭을 비교",
    "command": "docker exec stock_xgboost_ml sh -c 'cd /app && timeout 600 python -u scripts/liq_slippage_probe.py --days 60 --edge 1.20 --json-out /app/reports/overnight/cg259_liq_slip.json'",
    "arm": "거래대금 10분위 D1(최저)",
    "baseline": "같은 날 전체 종목 중앙 고저폭",
    "success": "D1 중앙 고저폭이 신호(1.20%p)보다 작으면 '비용 여유 있음', 크면 '비용에 먹힌다'",
    "expected": "저유동성일수록 창 고저폭이 넓을 것",
    "cost": "낮음(1분)",
    "est_minutes": 2,
    "created_at": "2026-11-13T23:20:00+09:00",
    "finished_at": "2026-11-13T23:25:00+09:00",
    "summary_out": "/app/reports/overnight/cg259_liq_slip.json",
    "result": {
        "verdict": "저거래대금 특이적 체결 페널티는 없다(1.09배) — 다만 창 고저폭 0.95%가 신호의 79%",
        "detail": (
            "minute_bars 14거래일(2026-09-23~11-13)·301종목·3,337 (종목·일) 실측. "
            "15:01~15:30 고저폭/종가 중앙값: D1 0.954% · D2 0.856 · D3 0.872 · D10 0.845 · 전체 0.875% → **D1/전체 = 1.09배**(저유동성이 특별히 더 흔들리지 않는다). "
            "D1 의 거래대금 중앙값은 23.2억으로 CG253 의 하한(30억) **아래**이고, 실제 규칙 풀(≥30억 ≒ D2~D3)의 고저폭은 0.86~0.87%로 전체와 같다. "
            "→ '30억 하한'으로 걸러낸 종목군에 유동성 특이적 슬리피지 프리미엄은 없다. 단 30분 고저폭 중앙 0.95%는 신호 1.20%p 의 79%이므로 종가 집행 타이밍 오차가 이득을 상당 부분 소진할 수 있다(0.5%p 가정은 과대가 아니라 적정~과소)."
        ),
        "delta": None,
        "rc": 0,
    },
    "evidence": "scripts/liq_slippage_probe.py(신설) · /app/reports/overnight/cg259_liq_slip.json",
    "note": "⚠ 두 컬럼은 쓰지 말 것: ① slip_pct(=|창마지막종가−일봉종가|)가 전 분위 0.000 — 마지막 분봉 종가가 일봉 종가와 동일해 퇴화 ② winvalue_share 가 2,600~2,700% 로 나옴 — minute_bars.trading_value 와 market_data.trading_value 단위/커버리지 불일치(별도 정합 이슈). 고저폭(range)만 유효하다.",
})

# ---- 4) CG260 (pending): 기준선을 같은 도구·같은 청정 패널로 재측정 ----
items.append({
    "id": "CG260",
    "status": "pending",
    "priority": 1,
    "kind": "measurement",
    "owner": "engineer",
    "affects_model": True,
    "metric": "baseline_protocol_gap",
    "from_experiment": "CG256",
    "title": "[측정정합성·스코어보드] 무개선 카운터의 기준선(0.5406)을 같은 도구·같은 청정 패널에서 다시 잰다 — 19사이클 미달의 절반이 프로토콜 격차일 수 있다",
    "hypothesis": "스코어보드 기준선 0.5406 은 panel_420_asofpatch(이후 CG85 에서 **누수** 판정) · 구도구에서 나온 값이다. CG166 은 프로토콜 간 격차를 0.0394 로, CG64 는 as-of 수리 패널에서 게이트ON 대조군을 0.5350 으로 쟀다. 즉 '+0.02 미달' 판정의 분모가 현재 배포가능 프로토콜과 같은 척도인지 확인되지 않았다. 같은 런에서 ① 등록 기준선 arm(LS_quant_q30_h5) ② 게이트ON 대조군(CO_core30_h5) ③ 역대 최고 두 arm(TR_rank_h5·CO_rank_smooth_d1_h5)을 나란히 재면 '기준선 자체의 값'과 'arm 델타'를 한 프레임에서 분리할 수 있다.",
    "counterfactual": "같은 패널·같은 폴드·같은 시드에서 기준선 arm 과 후보 arm 을 동시 측정(짝 Δ)",
    "command": "docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 7200 python -u scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_prod200.npz --folds 5 --seeds 5 --only LS_quant_q30_h5,CO_core30_h5,TR_rank_h5,CO_rank_smooth_d1_h5 --out /app/reports/overnight/cg260_baseline.jsonl --summary-out /app/reports/overnight/cg260_baseline_summary.json'",
    "arm": "TR_rank_h5 · CO_rank_smooth_d1_h5",
    "baseline": "LS_quant_q30_h5 · CO_core30_h5 (같은 런)",
    "success": "같은 런 기준선 값과 등록 기준선(0.5406)의 격차를 수치로 기록하고, 후보 arm 의 짝 Δ 가 +0.02 를 넘는지 판정. 격차 ≥ 0.02 면 스코어보드 기준선 교체를 리뷰보드 승인 항목으로 올린다(교체 자체는 이 루프에서 하지 않는다).",
    "expected": "청정 패널 기준선이 0.535 부근이면 TR_rank(+0.0107)·CO_rank_smooth_d1(+0.0227)의 의미가 달라진다 — 다만 AUC↔돈 상관이 없다는 실측(2026-10-04)을 함께 보고할 것.",
    "cost": "중(4 arm × 5폴드 × 5시드, 실측 30~60분)",
    "est_minutes": 90,
    "created_at": now,
    "note": "⚠ 승격 아님 — 측정이다. 기준선 '값'을 바꾸는 것은 리뷰보드 승인 대상(헌장 §3-B)이므로 이 항목은 실측·기록까지만 한다.",
})

b["updated_at"] = now
json.dump(b, open(PATH, "w"), ensure_ascii=False, indent=1)
print("updated", PATH, "items:", len(items))
print("pending:", [it["id"] for it in items if it.get("status") == "pending"])

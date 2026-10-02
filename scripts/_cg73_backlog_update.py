#!/usr/bin/env python3
"""2026-10-03 06:0x 틱 백로그 갱신 — XR13 종결(실측 분해) + CG73(데이터 원천 확보) + CG74(중복 열 정리).

근거(이번 틱 실측, panel_prod200 = 생산 유니버스 200종목·54,800행·2025-08-04~2026-09-23):
  ① 전행 0 컬럼 24/213. 그중 원천 부재: short_interest_ratio·days_to_cover(short_interest 테이블 없음),
     margin_balance_change·credit_balance_change(해당 테이블 없음), etf_flow_5d(테이블 없음),
     institution_ownership_pct(소스 미발굴·XR2).
  ② 커버리지 제한(원천은 있으나 패널 유니버스가 아님):
     sns_posts 384,821행/309종목 중 **패널 200종목 교집합 22종목**(27,127행) → sns_* 비영 ~2%.
     news_events 7,133행/211종목 중 **패널 교집합 12종목** → news_count_*/sentiment_* 0%.
     stock_sentiment 118행·**analysis_date 2일치(2026-10-01·10-02)** → 과거 패널 구간 전부 0.
  ③ XR13 의 '부활가능 11개' 는 **현 패널에서 이미 살아 있다**: atr_pct 1.000·quality_beta 0.903·
     quality_price_volatility_60d 0.998·similarity_std 1.000·twin_count 0.745·bayes_* 1.000.
     즉 파이프라인 결함이 아니라 exp_panel(별도 실험 패널) 기준의 진단이었다.
  ④ 단일피처 edge 최고는 전부 full-coverage 변동성군(bayes_volatility 0.0453 · atr_pct 0.0365 ·
     rank_volatility_20d 0.0356) 으로 선별 문턱(0.044) 근처 — 신규 정보가 없다.
"""
import json
import os

P = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "docs", "QUANT_MODEL_BACKLOG.json")
with open(P, encoding="utf-8") as f:
    doc = json.load(f)
items = doc["items"]
by_id = {i.get("id"): i for i in items}

# ── 1) XR13 종결 ────────────────────────────────────────────────────────────────
xr13 = by_id.get("XR13")
if xr13 is not None:
    xr13["status"] = "closed_rejected"
    prev = xr13.get("note") or ""
    xr13["note"] = (prev + "\n| 2026-10-03 06:0x **엔지니어 종결(closed_rejected)** — "
                    "'파이프라인 값이 전부 0' 전제가 현 패널에서 성립하지 않는다. 생산 유니버스 패널"
                    "(panel_prod200·54,800행) 실측: atr_pct 비영 1.000 · quality_beta 0.903 · "
                    "quality_price_volatility_60d 0.998 · similarity_std 1.000 · twin_count 0.745 · "
                    "twin_avg_correlation 0.745 · bayes_momentum_1d/5d·bayes_volatility·"
                    "bayes_gain_uncertainty 1.000. 즉 부활가능 11개는 **이미 모델 입력에 들어와 있고** "
                    "단일피처 edge 도 atr_pct 0.0365(선별 문턱 0.044 미달) 수준이다 → '부활'할 것이 없다. "
                    "②의 시장레벨 7개는 계약 #6 대로 횡단면 투입 금지(현행 유지). "
                    "진짜 죽은 열 24개의 원인은 **원천 부재**(short_interest·days_to_cover·"
                    "margin_balance_change·credit_balance_change·etf_flow_5d 테이블 없음, "
                    "institution_ownership_pct 소스 미발굴=XR2) 또는 **원천이 패널 유니버스를 덮지 않음**"
                    "(sns_posts 22/200종목 · news_events 12/200종목 · stock_sentiment 2일치)이다 → "
                    "다음 단계는 재측정이 아니라 원천 확보(CG73)다.")

# ── 2) CG73 신설(데이터 원천 확보) ───────────────────────────────────────────────
if "CG73" not in by_id:
    items.append({
        "id": "CG73",
        "title": "데이터 축 재개 조건 — 패널 유니버스(200종목)를 덮는 원천 확보 후 신규 피처 ≥5종 측정",
        "status": "needs_setup",
        "priority": 2,
        "hypothesis": (
            "모델 입력(패널)에 쓸 수 있는 **신규 정보가 남아 있는 유일한 곳은 원천 커버리지**다. "
            "현재 패널 213피처 중 full-coverage 시간가변 최고 edge 는 0.0453(변동성군)으로 선별 "
            "문턱(0.044) 근처뿐이고, 조정 축(변환·HP·유니버스·라벨·창·k·선별규칙·표본가중·파생)은 "
            "17사이클 실측으로 전부 종결됐다. 반면 SNS·뉴스·수급·공매도는 **원천은 살아 있으나 "
            "패널 유니버스를 덮지 않는다**(sns_posts 22/200종목 · news_events 12/200종목 · "
            "stock_sentiment 2일치 · short_interest/margin/credit/etf 테이블 부재). "
            "커버리지를 패널 유니버스로 확장하면 가격·수급과 독립인 신호가 처음으로 학습에 들어온다."),
        "evidence": (
            "이번 틱 실측(2026-10-03, panel_prod200): ① 전행 0 컬럼 24/213 ② 누수 0 · 종목상수 34 · "
            "시장레벨 56 · 무정보 120/213 ③ sns_posts 384,821행/309종목 → 패널 교집합 22종목(27,127행, "
            "sns_* 비영 ~2%, 단일피처 AUC 0.6071 은 'SNS 보유 종목' 식별에 가까움) ④ news_events "
            "7,133행/211종목 → 패널 교집합 12종목 ⑤ stock_sentiment 118행·2일치 ⑥ 선별 문턱 0.044 대비 "
            "full-coverage 최고 edge 0.0453(bayes_volatility) ⑦ 종결 축: CG71(선별규칙)·CG72(생산 "
            "유니버스 게이트 ON)·CG9c(rank+스무딩+depth1 승격 OOS −0.0384)."),
        "command": None,
        "metric": None,
        "success": ("패널 유니버스 200종목을 덮는 신규 원천으로 만든 피처 중 "
                    "단일피처 |AUC−0.5| ≥ 0.044(= 현 선별 문턱) 가 **5종 이상**이고, "
                    "그중 1종 이상이 시간가변(종목별 유니크값 ≥ 3)이며, 게이트 ON 스윕에서 "
                    "확장창 다중폴드 평균이 대조군 대비 +0.02 이상."),
        "expected": ("원천 확보는 리서처/사람 결정(수집 범위·API 한도·자격증명)이 필요하다. "
                     "화이트리스트 통과 전에는 스윕을 돌리지 않는다(과거 4개 독립 구성이 0.534~0.548 로 "
                     "수렴한 실패를 반복하지 않기 위함)."),
        "cost": "수집 배선 1~2일 + 패널 재빌드(prod200 실측 0.92 pair/s·54,800페어 ≈ 16.5h)",
        "counterfactual": "현행 패널(213피처) — 같은 유니버스·같은 게이트 ON 대조군 CO_core30_h5(0.5305±0.0199, CG72)",
        "note": ("2026-10-03 06:0x 신설. 계기: 규칙 4(실행 가능 pending 0) + 점수판 '16사이클 연속 "
                 "기준선 대비 +0.02 미달'. **모델 측 축은 포화**로 판정 — 이 항목은 원천 확보가 "
                 "선행돼야 command 를 채울 수 있어 needs_setup 으로 둔다. "
                 "선행 작업: ① SNS 수집 유니버스를 패널 200종목으로 확장(sns_posts 는 2018~2026 누적·"
                 "384k행이 이미 있으므로 '수집 범위' 문제) ② 뉴스/SNS 감성의 종목 커버리지 확대 "
                 "③ 공매도·신용·ETF 테이블 신설 여부 결정(원천 자체가 없음)."),
        "attempts": 0,
        "result": None,
        "arm": None,
        "finding": None,
    })

# ── 3) CG74 신설(중복 열 정리 — 정답성 수리, 실험 아님) ──────────────────────────
if "CG74" not in by_id:
    items.append({
        "id": "CG74",
        "title": "패널 피처명 중복 14쌍 제거 — 동일값 열이 top30 선별 슬롯을 낭비한다",
        "status": "backlog",
        "priority": 7,
        "hypothesis": ("`wf_wave.dedupe_names` 는 중복 라벨을 `__dupN` 로 **개명만** 하므로 동일값 열이 "
                       "두 번 학습행렬에 남는다. top30 선별이 같은 피처를 두 슬롯에 넣으면 실질 28~29개만 "
                       "쓰게 된다."),
        "evidence": ("이번 틱 실측(panel_prod200): 피처명 213개 중 14쌍이 **비트 동일**(cross_trend·"
                     "momentum_vs_volatility·price_volume·return_5d_mean_10d·short_medium_term_momentum·"
                     "target_ma_5/10/20·trend_confirmation·trend_interaction·volatility_20d_mean_10d·"
                     "volatility_volume·volume_price_trend·volume_ratio_5_mean_10d). edge 상위 30개를 세어 "
                     "보면 3쌍(volatility_20d_mean_10d·volume_price_trend·target_ma_5)이 들어가 "
                     "**고유 피처 27개**만 선별된다."),
        "command": None,
        "metric": None,
        "success": ("패널 빌드 산출물의 `feature_names` 가 유일하고, 같은 유니버스·같은 config 스윕의 "
                    "게이트 ON 폴드 평균이 악화되지 않음(Δ ≥ −0.005)."),
        "expected": "정답성 수리(기대 AUC 이득 ≈0 — 대체 슬롯의 edge 가 중복과 비슷). 실험보다 빌드 수리로 처리.",
        "cost": "빌드 경로 수정 + 기존 패널은 열 제거 후 재사용(A/B 는 스윕 로더에서 열 drop 플래그 필요)",
        "counterfactual": None,
        "note": ("2026-10-03 06:0x 신설. RB2('중복명 index 붕괴') 후속. 우선순위 낮음(정답성). "
                 "prod200 재빌드는 16.5h 이므로 **스윕 로더 측 drop 플래그**로 기존 패널에서 A/B 하는 것이 "
                 "비용이 낮다(단일 런 안에서 arm/cf 를 만들려면 config 필드로 넣어야 한다 — 전역 CLI 플래그는 "
                 "한 런에 두 arm 을 못 만든다)."),
        "attempts": 0,
        "result": None,
        "arm": None,
        "finding": None,
    })

doc["items"] = items
doc["updated_at"] = "2026-10-03T06:05:00+09:00"
with open(P, "w", encoding="utf-8") as f:
    json.dump(doc, f, ensure_ascii=False, indent=2)
print("updated: XR13 closed_rejected · CG73/CG74 신설 · items=", len(items))

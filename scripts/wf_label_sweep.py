#!/usr/bin/env python3
"""wf_label_sweep — 라벨 설계만 바꿔가며 확장창 walk-forward 를 돌린다.

배경 (왜 라벨인가)
  ─ 스킬 실측: AUC 를 올리는 순서는 **라벨 → 데이터 → 피처**이고, 같은 피처셋에서 라벨만
    바꿔 +0.06 이 나온 사례가 있다(절대 1일방향 0.5133 → 시장상대 0.5270 → 분위+호라이즌).
  ─ 그런데 `wf_wave.py` 의 `make_labels` 에는 ``kind="relative"``(시장상대 이진: 횡단면 중앙값
    초과=1) 가 **구현돼 있는데 한 번도 호출되지 않는다**. main 은 ``"quantile"`` 만 쓴다.
  ─ 즉 "시장상대 라벨"은 확장창 walk-forward 에서 **측정된 적이 없다**. 또 q(분위 비율)도 0.3 고정이다.

이 스크립트는 `wf_wave` 의 패널·폴드·purge·피처선별·학습을 **그대로 재사용**해서(=결과 비교 가능)
라벨 변형만 갈아끼운다. 패널 캐시를 공유하므로 재빌드 비용이 없다.

실행(컨테이너):
  cd /app && OMP_NUM_THREADS=4 python -u scripts/wf_label_sweep.py
  cd /app && OMP_NUM_THREADS=4 python -u scripts/wf_label_sweep.py --folds 5 --seeds 3
  cd /app && OMP_NUM_THREADS=4 python -u scripts/wf_label_sweep.py --wait-for-panel 60
결과: /app/reports/overnight/wf_label_sweep.jsonl + wf_label_sweep_summary.json

판정: **폴드 평균 AUC 의 평균**과 폴드 간 표준편차. 단일 분할/단일 폴드 값은 쓰지 않는다.
"""
import argparse
import json
import os
import re
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

import wf_wave as W  # noqa: E402  (패널 빌드·라벨·선별·폴드 로직 재사용)

ml = W.ml


CONFIGS = [
    {"id": "LS_quant_q30_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "desc": "기준(현 최고 실험과 동일): 분위0.3 h5 top30"},
    {"id": "LS_rel_h5",       "kind": "relative", "horizon": 5, "q": None, "select": "top30",
     "desc": "시장상대 이진(중앙값 초과=1) h5 top30 — 미측정 레버"},
    {"id": "LS_rel_h5_t40",   "kind": "relative", "horizon": 5, "q": None, "select": "top40",
     "desc": "시장상대 이진 h5 top40"},
    {"id": "LS_rel_h3",       "kind": "relative", "horizon": 3, "q": None, "select": "top30",
     "desc": "시장상대 이진 h3 top30"},





    {"id": "LS_quant_q30_h8",  "kind": "quantile", "horizon": 8, "q": 0.30, "select": "top30",
     "desc": "분위0.3 h8 top30 — wave5 최고 설정을 동일 프로토콜로 검증"},
    {"id": "LS_quant_q30_h10", "kind": "quantile", "horizon": 10, "q": 0.30, "select": "top30",
     "desc": "분위0.3 h10 top30 — 호라이즌 상단(과확장 여부 확인)"},
    {"id": "LS_rel_h8",        "kind": "relative", "horizon": 8, "q": None, "select": "top30",
     "desc": "시장상대 이진 h8 top30 — 분위 대신 중앙값 기준"},
    {"id": "LS_quant_q25_h5", "kind": "quantile", "horizon": 5, "q": 0.25, "select": "top30",
     "desc": "분위0.25 h5 top30 (표본 +)"},
    {"id": "LS_quant_q20_h5", "kind": "quantile", "horizon": 5, "q": 0.20, "select": "top30",
     "desc": "분위0.20 h5 top30 (표본 ++)"},








    {"id": "LS_quant_q15_h5", "kind": "quantile", "horizon": 5, "q": 0.15, "select": "top30",
     "desc": "분위0.15 h5 top30 — 꼬리 두께 축(넓은 유니버스에서 22 positive/일)"},
    {"id": "LS_quant_q10_h5", "kind": "quantile", "horizon": 5, "q": 0.10, "select": "top30",
     "desc": "분위0.10 h5 top30 — 49종목 q0.30 과 같은 '15 positive/일' 밀도"},
    {"id": "LS_quant_q05_h5", "kind": "quantile", "horizon": 5, "q": 0.05, "select": "top30",
     "desc": "분위0.05 h5 top30 — 극단 꼬리(7 positive/일). 표본 급감 주의"},








    {"id": "LB_smooth_q30_h5", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "top30",
     "desc": "라벨=선행 1~5일 수익률 평균(스무딩) — 5일 보유와 정합, 라벨 잡음 축소"},
    {"id": "LB_voladj_q30_h5", "kind": "voladj", "horizon": 5, "q": 0.30, "select": "top30",
     "desc": "라벨=선행 5일 수익률 ÷ 후행 20일 실현변동성 — 위험조정(시점정합)"},



    {"id": "TR_rank_LBsmooth_h5", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "top30",
     "transform": "rank",
     "desc": "횡단면 rank 변환 × 라벨 스무딩 — 두 약한 양(+) 방향의 가산성 측정"},

    {"id": "TR_rank_h5",    "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "transform": "rank",
     "desc": "피처 날짜별 횡단면 rank(pct) — 시장레벨 성분 제거"},






    {"id": "TR_rank_d1_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "transform": "rank",
     "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "desc": "횡단면 rank + depth1·lr0.05 — 두 양(+) 방향의 결합"},
    {"id": "TR_zscore_h5",  "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "transform": "zscore",
     "desc": "피처 날짜별 횡단면 z-score"},
    {"id": "TR_rank_h5_t60", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top60",
     "transform": "rank",
     "desc": "횡단면 rank + top60 (정규화하면 더 많은 피처를 쓸 여지)"},
    {"id": "TR_rank_h3",     "kind": "quantile", "horizon": 3, "q": 0.30, "select": "top30",
     "transform": "rank",
     "desc": "횡단면 rank + h3"},
    {"id": "TR_rank_h6",     "kind": "quantile", "horizon": 6, "q": 0.30, "select": "top30",
     "transform": "rank",
     "desc": "횡단면 rank + h6"},
    {"id": "TR_rank_h5_t40", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top40",
     "transform": "rank",
     "desc": "횡단면 rank + h5 + top40"},

    {"id": "PO_timevary_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "pool": "timevary",
     "desc": "시간가변 피처만 — 종목-상수(최신 스냅샷) 제외"},
    {"id": "PO_const_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "pool": "const",
     "desc": "종목-상수 피처만 — 룩어헤드 의심군 단독 성능"},
    {"id": "PO_timevary_rank_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "transform": "rank", "pool": "timevary",
     "desc": "시간가변 + 횡단면 rank"},








    {"id": "MK_nomkt_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "exclude_market_level": True,
     "desc": "분위0.3 h5 top30 + 시장레벨(날짜내 종목간 동일값) 피처 제외"},
    {"id": "MK_timevary_nomkt_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "pool": "timevary", "exclude_market_level": True,
     "desc": "시간가변 + 시장레벨 제외 (계약 #3·#6 동시 준수)"},







    {"id": "TV_all_nomkt_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "all",
     "pool": "timevary", "exclude_market_level": True,
     "desc": "시간가변 전량(선별 없음) + 시장레벨 제외 — CG255 의 |t|≥2 후보를 버리지 않는 arm"},





    {"id": "IC30_nomkt_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "ic30",
     "pool": "timevary", "exclude_market_level": True,
     "desc": "시간가변 + 날짜별 IC top30 선별 (풀링 edge top30 대조) + 시장레벨 제외"},









    {"id": "CO_core30_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True,
     "desc": "core48 안에서 edge top30 — 프로덕션 게이트를 켠 상태의 대조군"},
    {"id": "CO_core_all_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "all",
     "core_only": True,
     "desc": "core48 전체(선별 없음) — 게이트 적용 시 프로덕션 피처셋과 동일 규모"},






    {"id": "CO_core15_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top15",
     "core_only": True,
     "desc": "게이트 ON + edge top15 — 게이트 ON 선별 크기 곡선의 하단(미측정)"},
    {"id": "CO_core20_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top20",
     "core_only": True,
     "desc": "게이트 ON + edge top20 — 게이트 ON 선별 크기 곡선의 하단(미측정)"},
    {"id": "CO_core40_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top40",
     "core_only": True,
     "desc": "게이트 ON + edge top40 — core30(0.5355)과 core48(0.5263) 사이 확인점"},





    {"id": "CO_q10_h5", "kind": "quantile", "horizon": 5, "q": 0.10, "select": "top30",
     "core_only": True,
     "desc": "게이트 ON + 분위0.10 — 49종목 q0.30 과 같은 15 pos/일 밀도"},
    {"id": "CO_q05_h5", "kind": "quantile", "horizon": 5, "q": 0.05, "select": "top30",
     "core_only": True,
     "desc": "게이트 ON + 분위0.05 — 극단 꼬리(게이트 OFF 실측 Δ+0.0420)"},
    {"id": "CO_core30_h8", "kind": "quantile", "horizon": 8, "q": 0.30, "select": "top30",
     "core_only": True,
     "desc": "core30 + h8 — 게이트×호라이즌 상호작용 확인"},








    {"id": "CO_rank_smooth_h5", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "top30",
     "transform": "rank", "core_only": True,
     "desc": "core48 게이트 ON + rank 변환 + 라벨 스무딩 — 스윕 최고 설정의 프로덕션 경로 전이 검정"},




    {"id": "CO_rank_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "transform": "rank", "core_only": True,
     "desc": "core48 게이트 ON + rank 변환(스무딩 없음) — 게이트 안에서 rank 단독 효과"},




    {"id": "CO_smooth_h5", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True,
     "desc": "게이트 ON + 라벨 스무딩만(rank 없음) — 스무딩 단독 기여 분리"},
    {"id": "CO_d1_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "desc": "게이트 ON + depth1·lr0.05 — 유일하게 재현된 HP 방향의 게이트 내 효과"},
    {"id": "CO_rank_smooth_d1_h5", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "top30",
     "transform": "rank", "core_only": True, "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "desc": "게이트 ON + rank + 스무딩 + depth1 — 생산경로에서 가진 방향 전부 결합"},








    {"id": "CO_smooth_d1_h5", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "desc": "게이트 ON + 라벨 스무딩 + depth1·lr0.05 (rank 없음) — 추론 계약 무변경 배포 가능 후보"},





    {"id": "CG276_h1", "kind": "smooth", "horizon": 1, "q": 0.30, "select": "top30",
     "core_only": True, "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "desc": "CG276 — 지평 h1 · 스무딩 불가(1일)라 원형 라벨 · depth1 (지평 사다리)"},
    {"id": "CG276_h3", "kind": "smooth", "horizon": 3, "q": 0.30, "select": "top30",
     "core_only": True, "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "desc": "CG276 — 지평 h3 · 스무딩 + depth1 (지평 사다리)"},
    {"id": "CG276_h10", "kind": "smooth", "horizon": 10, "q": 0.30, "select": "top30",
     "core_only": True, "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "desc": "CG276 — 지평 h10 · 스무딩 + depth1 (지평 사다리)"},








    {"id": "SD1s_00_30", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [0, 30],
     "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "desc": "구간 [0:30) · 게이트 ON + 스무딩 + depth1 (rank 없음·배포 가능) — 짝 대조군 US_00_30"},
    {"id": "SD1s_30_60", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [30, 60],
     "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "desc": "구간 [30:60) · 게이트 ON + 스무딩 + depth1 (rank 없음·배포 가능) — 짝 대조군 US_30_60"},
    {"id": "SD1s_60_90", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [60, 90],
     "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "desc": "구간 [60:90) · 게이트 ON + 스무딩 + depth1 (rank 없음·배포 가능) — 짝 대조군 US_60_90"},
    {"id": "SD1s_90_120", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [90, 120],
     "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "desc": "구간 [90:120) · 게이트 ON + 스무딩 + depth1 (rank 없음·배포 가능) — 짝 대조군 US_90_120"},
    {"id": "SD1s_120_150", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [120, 150],
     "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "desc": "구간 [120:150) · 게이트 ON + 스무딩 + depth1 (rank 없음·배포 가능) — 짝 대조군 US_120_150"},












    {"id": "SL_00_30", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "ic30",
     "core_only": True, "codes_slice": [0, 30],
     "desc": "구간 [0:30) · 게이트 ON + 날짜별 IC top30 선별 — 짝 대조군 US_00_30(풀링 edge top30)"},
    {"id": "SL_30_60", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "ic30",
     "core_only": True, "codes_slice": [30, 60],
     "desc": "구간 [30:60) · 게이트 ON + 날짜별 IC top30 선별 — 짝 대조군 US_30_60(풀링 edge top30)"},
    {"id": "SL_60_90", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "ic30",
     "core_only": True, "codes_slice": [60, 90],
     "desc": "구간 [60:90) · 게이트 ON + 날짜별 IC top30 선별 — 짝 대조군 US_60_90(풀링 edge top30)"},
    {"id": "SL_90_120", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "ic30",
     "core_only": True, "codes_slice": [90, 120],
     "desc": "구간 [90:120) · 게이트 ON + 날짜별 IC top30 선별 — 짝 대조군 US_90_120(풀링 edge top30)"},
    {"id": "SL_120_150", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "ic30",
     "core_only": True, "codes_slice": [120, 150],
     "desc": "구간 [120:150) · 게이트 ON + 날짜별 IC top30 선별 — 짝 대조군 US_120_150(풀링 edge top30)"},
















    {"id": "CO_core30_g11_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True,
     "gate_add": ["atr_pct", "authenticity_avg", "bayes_gain_uncertainty", "bayes_momentum_1d",
                  "bayes_momentum_5d", "bayes_volatility", "quality_beta",
                  "quality_price_volatility_60d", "similarity_std", "twin_avg_correlation",
                  "twin_count"],
     "desc": "게이트 ON + 부활후보 11개 강제투입(core30 동일 top30 + 11컬럼) — 데이터 축 L3"},
    {"id": "CO_core30_p11_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True,
     "gate_add": ["rsi", "bb_position", "target_ma_20", "ma_position_20", "return_20d",
                  "momentum_1m_reverse", "volume_price_trend", "rank_return_5d", "target_ma_5",
                  "atr", "ma_position_5"],
     "desc": "게이트 ON + 무정보(placebo) 11개 강제투입 — 용량 교란 통제(같은 수·다른 정보량)"},




















    {"id": "CO_core30_ev10_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True,
     "gate_add": ["disclosure_count_5d", "event_stake_change_5d", "event_exec_change_5d",
                  "event_realized_5d", "event_contract_5d", "event_treasury_5d",
                  "event_capital_increase_5d", "event_delisting_5d", "event_mna_5d",
                  "event_cb_bw_5d"],
     "desc": "게이트 ON + 백필 이벤트 10개 강제투입(core30 동일 top30 + 10컬럼) — CG29 데이터 축"},
    {"id": "CO_core30_pv10_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True,
     "gate_add": ["sentiment_avg_db", "sentiment_momentum", "ma_position_20",
                  "institution_net_buy_5d", "rank_volume_ratio_20", "bb_position",
                  "volume_ratio_5", "rsi", "similar_stocks_return_std", "rank_volume_ratio_5"],
     "desc": "게이트 ON + 무정보(placebo) 10개 강제투입 — 용량 교란 통제(같은 수·다른 정보량)"},









    {"id": "UNg_25", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_limit": 25,
     "desc": "게이트 ON 대조군 · 유동성 상위 25종목 (중첩 부분집합 최소)"},
    {"id": "UNg_49", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_limit": 49,
     "desc": "게이트 ON 대조군 · 49종목 (등록 기준선과 같은 크기)"},
    {"id": "UNg_75", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_limit": 75,
     "desc": "게이트 ON 대조군 · 75종목"},
    {"id": "UNg_100", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_limit": 100,
     "desc": "게이트 ON 대조군 · 100종목"},
    {"id": "UNg_150", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_limit": 150,
     "desc": "게이트 ON 대조군 · 150종목 전체 (대조군 끝점)"},


    {"id": "RSg_25", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "top30",
     "transform": "rank", "core_only": True, "codes_limit": 25,
     "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "desc": "게이트 ON + rank + 스무딩 + depth1 · 25종목"},
    {"id": "RSg_49", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "top30",
     "transform": "rank", "core_only": True, "codes_limit": 49,
     "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "desc": "게이트 ON + rank + 스무딩 + depth1 · 49종목 (CG4 재현을 같은 패널에서)"},
    {"id": "RSg_75", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "top30",
     "transform": "rank", "core_only": True, "codes_limit": 75,
     "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "desc": "게이트 ON + rank + 스무딩 + depth1 · 75종목"},
    {"id": "RSg_150", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "top30",
     "transform": "rank", "core_only": True, "codes_limit": 150,
     "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "desc": "게이트 ON + rank + 스무딩 + depth1 · 150종목 (CG11 재측정·같은 런 짝)"},







    {"id": "US_00_30", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [0, 30],
     "desc": "게이트 ON 대조군 · 서로소 구간 [0:30)"},
    {"id": "US_30_60", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [30, 60],
     "desc": "게이트 ON 대조군 · 서로소 구간 [30:60)"},
    {"id": "US_60_90", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [60, 90],
     "desc": "게이트 ON 대조군 · 서로소 구간 [60:90)"},
    {"id": "US_90_120", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [90, 120],
     "desc": "게이트 ON 대조군 · 서로소 구간 [90:120)"},
    {"id": "US_120_150", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [120, 150],
     "desc": "게이트 ON 대조군 · 서로소 구간 [120:150)"},
    {"id": "US_all150", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_limit": 150,
     "desc": "게이트 ON 대조군 · 150종목 전체(같은 런 기준점)"},






    {"id": "Q5s_00_30", "kind": "quantile", "horizon": 5, "q": 0.05, "select": "top30",
     "core_only": True, "codes_slice": [0, 30],
     "desc": "게이트 ON + 분위0.05 · 서로소 구간 [0:30) (짝 대조군 US_00_30)"},
    {"id": "Q5s_30_60", "kind": "quantile", "horizon": 5, "q": 0.05, "select": "top30",
     "core_only": True, "codes_slice": [30, 60],
     "desc": "게이트 ON + 분위0.05 · 서로소 구간 [30:60) (짝 대조군 US_30_60)"},
    {"id": "Q5s_60_90", "kind": "quantile", "horizon": 5, "q": 0.05, "select": "top30",
     "core_only": True, "codes_slice": [60, 90],
     "desc": "게이트 ON + 분위0.05 · 서로소 구간 [60:90) (짝 대조군 US_60_90)"},
    {"id": "Q5s_90_120", "kind": "quantile", "horizon": 5, "q": 0.05, "select": "top30",
     "core_only": True, "codes_slice": [90, 120],
     "desc": "게이트 ON + 분위0.05 · 서로소 구간 [90:120) (짝 대조군 US_90_120)"},
    {"id": "Q5s_120_150", "kind": "quantile", "horizon": 5, "q": 0.05, "select": "top30",
     "core_only": True, "codes_slice": [120, 150],
     "desc": "게이트 ON + 분위0.05 · 서로소 구간 [120:150) (짝 대조군 US_120_150)"},







    {"id": "RSs_00_30", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "top30",
     "transform": "rank", "core_only": True, "codes_slice": [0, 30],
     "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "desc": "구간 [0:30) · 게이트 ON + rank + 스무딩 + depth1 (CG4/CG8 후보)"},
    {"id": "RSs_30_60", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "top30",
     "transform": "rank", "core_only": True, "codes_slice": [30, 60],
     "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "desc": "구간 [30:60) · 게이트 ON + rank + 스무딩 + depth1 (CG4/CG8 후보)"},
    {"id": "RSs_60_90", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "top30",
     "transform": "rank", "core_only": True, "codes_slice": [60, 90],
     "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "desc": "구간 [60:90) · 게이트 ON + rank + 스무딩 + depth1 (CG4/CG8 후보)"},
    {"id": "RSs_90_120", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "top30",
     "transform": "rank", "core_only": True, "codes_slice": [90, 120],
     "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "desc": "구간 [90:120) · 게이트 ON + rank + 스무딩 + depth1 (CG4/CG8 후보)"},
    {"id": "RSs_120_150", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "top30",
     "transform": "rank", "core_only": True, "codes_slice": [120, 150],
     "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "desc": "구간 [120:150) · 게이트 ON + rank + 스무딩 + depth1 (CG4/CG8 후보)"},







    {"id": "REl_00_30", "kind": "relative", "horizon": 5, "q": None, "select": "top30",
     "core_only": True, "codes_slice": [0, 30],
     "desc": "구간 [0:30) · 게이트 ON + 시장상대 라벨(중앙값 초과)"},
    {"id": "REl_30_60", "kind": "relative", "horizon": 5, "q": None, "select": "top30",
     "core_only": True, "codes_slice": [30, 60],
     "desc": "구간 [30:60) · 게이트 ON + 시장상대 라벨"},
    {"id": "REl_60_90", "kind": "relative", "horizon": 5, "q": None, "select": "top30",
     "core_only": True, "codes_slice": [60, 90],
     "desc": "구간 [60:90) · 게이트 ON + 시장상대 라벨"},
    {"id": "REl_90_120", "kind": "relative", "horizon": 5, "q": None, "select": "top30",
     "core_only": True, "codes_slice": [90, 120],
     "desc": "구간 [90:120) · 게이트 ON + 시장상대 라벨"},
    {"id": "REl_120_150", "kind": "relative", "horizon": 5, "q": None, "select": "top30",
     "core_only": True, "codes_slice": [120, 150],
     "desc": "구간 [120:150) · 게이트 ON + 시장상대 라벨"},
    {"id": "REl_all150", "kind": "relative", "horizon": 5, "q": None, "select": "top30",
     "core_only": True, "codes_limit": 150,
     "desc": "150종목 전체 · 시장상대 라벨(같은 런 기준점)"},






    {"id": "DSs_00_30", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [0, 30],
     "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "desc": "구간 [0:30) · 게이트 ON + depth1·lr0.05 (CG8 HP 후보)"},
    {"id": "DSs_30_60", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [30, 60],
     "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "desc": "구간 [30:60) · 게이트 ON + depth1·lr0.05 (CG8 HP 후보)"},
    {"id": "DSs_60_90", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [60, 90],
     "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "desc": "구간 [60:90) · 게이트 ON + depth1·lr0.05 (CG8 HP 후보)"},
    {"id": "DSs_90_120", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [90, 120],
     "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "desc": "구간 [90:120) · 게이트 ON + depth1·lr0.05 (CG8 HP 후보)"},
    {"id": "DSs_120_150", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [120, 150],
     "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "desc": "구간 [120:150) · 게이트 ON + depth1·lr0.05 (CG8 HP 후보)"},








    {"id": "EV_all_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "desc": "부활 이벤트 피처 포함(227컬럼) — 데이터 축 A/B 실험군"},
    {"id": "EV_none_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "exclude_names": ["event_*", "disclosure_count_5d"],
     "desc": "같은 패널에서 이벤트 피처 17개만 제외(210컬럼) — 동일 런 대조군"},



    {"id": "CG376_roll_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "desc": "CG376 arm — event_rolling 집계 8종 포함(221피처)"},
    {"id": "CG376_noroll_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "exclude_names": ["event_level_20d", "event_trend_5v20", "event_any_60d",
                       "funding_level_20d", "governance_level_20d", "business_level_20d",
                       "risk_level_20d", "disclosure_level_20d"],
     "desc": "CG376 대조군 — 같은 패널에서 event_rolling 집계 8종만 제외(213피처)"},





    {"id": "UN_150_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "desc": "150종목 전체 — 유니버스 A/B 실험군(선별 = 상위 20%)"},
    {"id": "UN_49_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "codes_limit": 49,
     "desc": "같은 150종목 패널에서 49종목(패널 순서=유동성 상위)만 — 동일 런 대조군(선별 = 상위 61%)"},






    {"id": "HP_d2_lr05_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "recipe": {"lr": 0.05, "depth": 2, "n_estimators": 2000},
     "desc": "얕은 트리(depth2)·lr0.05 — 소표본 과적합 억제 가설"},
    {"id": "HP_d3_lr03_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "recipe": {"lr": 0.03, "depth": 3, "n_estimators": 2000},
     "desc": "depth3·lr0.03 (WF5 계열 레시피를 h5·동일 프로토콜로)"},
    {"id": "HP_d6_lr02_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "recipe": {"lr": 0.02, "depth": 6, "n_estimators": 1200},
     "desc": "깊은 트리(depth6)·lr0.02 — 상호작용 포착 가설"},





    {"id": "HP_d2_reg_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "recipe": {"lr": 0.05, "depth": 2, "n_estimators": 2000},
     "recipe_extra": {"subsample": 0.6, "colsample_bytree": 0.5, "min_child_weight": 10,
                      "reg_lambda": 5.0, "lambda_l2": 5.0, "l2_leaf_reg": 5.0},
     "desc": "depth2·lr0.05 + 강정규화(서브샘플0.6·열샘플0.5·mcw10·L2=5)"},
    {"id": "HP_d1_lr05_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "desc": "depth1(스텀프)·lr0.05 — 과적합 상한 확인"},
    {"id": "HP_d2_lr08_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "recipe": {"lr": 0.08, "depth": 2, "n_estimators": 2000},
     "desc": "depth2·lr0.08 — 학습률 상향"},
    {"id": "HP_d3_reg_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "recipe": {"lr": 0.03, "depth": 3, "n_estimators": 2000},
     "recipe_extra": {"subsample": 0.6, "colsample_bytree": 0.5, "min_child_weight": 10,
                      "reg_lambda": 5.0, "lambda_l2": 5.0, "l2_leaf_reg": 5.0},
     "desc": "depth3·lr0.03 + 강정규화 — depth 축과 정규화 축의 상호작용"},




    {"id": "EN_drop_cat_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "ens": {"skip": ["catboost"]},
     "desc": "catboost 제외(xgb+lgbm, val AUC 가중) — 최약 모델 제거 가설"},
    {"id": "EN_equal_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "ens": {"equal_weights": True},
     "desc": "3종 균등가중(val AUC 가중 대신) — 가중이 노이즈인지 확인"},
    {"id": "EN_d2_equal_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "recipe": {"lr": 0.05, "depth": 2, "n_estimators": 2000},
     "ens": {"equal_weights": True},
     "desc": "depth2(최고 HP) + 3종 균등가중 — 두 축 결합"},
    {"id": "EN_d1_dropcat_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "ens": {"skip": ["catboost"]},
     "desc": "depth1(현 최고) + catboost 제외 — 두 축 결합"},





    {"id": "F3_base_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "folds": 3,
     "desc": "기준 설정을 3폴드(최소 학습창 약 3배)로 측정 — 프로토콜 민감도"},
    {"id": "F3_d1_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "folds": 3, "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "desc": "3폴드 + depth1(현 최고 HP) — 표본이 늘면 얕은 트리 이점이 사라지는가"},




    {"id": "SEL_top10_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top10",
     "desc": "edge 상위 10개만 — 소표본에서 피처 축소 가설"},
    {"id": "SEL_top15_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top15",
     "desc": "edge 상위 15개 — 사전등록 arm"},
    {"id": "SEL_top20_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top20",
     "desc": "edge 상위 20개"},










    {"id": "S1_top1_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top1",
     "desc": "train 폴드 edge 최상위 **1개 피처만** — 선별 크기 곡선 하한(풀링 최강축 = OOS 최강축인가)"},
    {"id": "S3_top3_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top3",
     "desc": "train 폴드 edge 상위 3개 — 하한 근처(중복 피처 제거 효과 포함)"},
    {"id": "S5_top5_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top5",
     "desc": "train 폴드 edge 상위 5개 — top10(0.5364)과 top1 사이 중간점"},










    {"id": "WDn_00_30", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [0, 30],
     "desc": "구간 [0:30) · 게이트 ON · 가중 없음(원 경로) — CG23 대조군"},
    {"id": "WDu_00_30", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [0, 30], "weight": {"kind": "uniform"},
     "desc": "구간 [0:30) · 게이트 ON · 균등 가중(가중 배관 통제, Δ≈0 기대)"},
    {"id": "WDw_h60_00_30", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [0, 30], "weight": {"kind": "time_decay", "hl": 60},
     "desc": "구간 [0:30) · 게이트 ON · 시간 감쇠 hl60 (사전등록 arm)"},
    {"id": "WDn_30_60", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [30, 60],
     "desc": "구간 [30:60) · 게이트 ON · 가중 없음(원 경로) — CG23 대조군"},
    {"id": "WDu_30_60", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [30, 60], "weight": {"kind": "uniform"},
     "desc": "구간 [30:60) · 게이트 ON · 균등 가중(가중 배관 통제, Δ≈0 기대)"},
    {"id": "WDw_h60_30_60", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [30, 60], "weight": {"kind": "time_decay", "hl": 60},
     "desc": "구간 [30:60) · 게이트 ON · 시간 감쇠 hl60 (사전등록 arm)"},
    {"id": "WDn_60_90", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [60, 90],
     "desc": "구간 [60:90) · 게이트 ON · 가중 없음(원 경로) — CG23 대조군"},
    {"id": "WDu_60_90", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [60, 90], "weight": {"kind": "uniform"},
     "desc": "구간 [60:90) · 게이트 ON · 균등 가중(가중 배관 통제, Δ≈0 기대)"},
    {"id": "WDw_h60_60_90", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [60, 90], "weight": {"kind": "time_decay", "hl": 60},
     "desc": "구간 [60:90) · 게이트 ON · 시간 감쇠 hl60 (사전등록 arm)"},
    {"id": "WDn_90_120", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [90, 120],
     "desc": "구간 [90:120) · 게이트 ON · 가중 없음(원 경로) — CG23 대조군"},
    {"id": "WDu_90_120", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [90, 120], "weight": {"kind": "uniform"},
     "desc": "구간 [90:120) · 게이트 ON · 균등 가중(가중 배관 통제, Δ≈0 기대)"},
    {"id": "WDw_h60_90_120", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [90, 120], "weight": {"kind": "time_decay", "hl": 60},
     "desc": "구간 [90:120) · 게이트 ON · 시간 감쇠 hl60 (사전등록 arm)"},
    {"id": "WDn_120_150", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [120, 150],
     "desc": "구간 [120:150) · 게이트 ON · 가중 없음(원 경로) — CG23 대조군"},
    {"id": "WDu_120_150", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [120, 150], "weight": {"kind": "uniform"},
     "desc": "구간 [120:150) · 게이트 ON · 균등 가중(가중 배관 통제, Δ≈0 기대)"},
    {"id": "WDw_h60_120_150", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [120, 150], "weight": {"kind": "time_decay", "hl": 60},
     "desc": "구간 [120:150) · 게이트 ON · 시간 감쇠 hl60 (사전등록 arm)"},









    {"id": "ABw_00_30", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [0, 30], "weight": {"kind": "absret", "clip": 0.05},
     "desc": "구간 [0:30) · 게이트 ON · |선행 5일 수익| 규모 가중 (CG77 arm)"},
    {"id": "ABw_30_60", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [30, 60], "weight": {"kind": "absret", "clip": 0.05},
     "desc": "구간 [30:60) · 게이트 ON · |선행 5일 수익| 규모 가중 (CG77 arm)"},
    {"id": "ABw_60_90", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [60, 90], "weight": {"kind": "absret", "clip": 0.05},
     "desc": "구간 [60:90) · 게이트 ON · |선행 5일 수익| 규모 가중 (CG77 arm)"},
    {"id": "ABw_90_120", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [90, 120], "weight": {"kind": "absret", "clip": 0.05},
     "desc": "구간 [90:120) · 게이트 ON · |선행 5일 수익| 규모 가중 (CG77 arm)"},
    {"id": "ABw_120_150", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [120, 150], "weight": {"kind": "absret", "clip": 0.05},
     "desc": "구간 [120:150) · 게이트 ON · |선행 5일 수익| 규모 가중 (CG77 arm)"},


    {"id": "CO_core30_150", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_limit": 150,
     "desc": "게이트 ON 대조군 · 150종목 전체(같은 런 기준점)"},









    {"id": "DDn_00_30", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [0, 30],
     "desc": "구간 [0:30) · 게이트 ON · 파생 없음 — CG25 대조군"},
    {"id": "DDd_00_30", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [0, 30],
     "derived": {"sources": ["volatility_20d", "volatility_60d", "atr_pct", "volume_ratio_5",
                             "rsi", "ma_position_20"], "lags": [1, 5]},
     "desc": "구간 [0:30) · 게이트 ON + Δ1·Δ5 파생 12컬럼 (사전등록 arm)"},
    {"id": "DDn_30_60", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [30, 60],
     "desc": "구간 [30:60) · 게이트 ON · 파생 없음 — CG25 대조군"},
    {"id": "DDd_30_60", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [30, 60],
     "derived": {"sources": ["volatility_20d", "volatility_60d", "atr_pct", "volume_ratio_5",
                             "rsi", "ma_position_20"], "lags": [1, 5]},
     "desc": "구간 [30:60) · 게이트 ON + Δ1·Δ5 파생 12컬럼 (사전등록 arm)"},
    {"id": "DDn_60_90", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [60, 90],
     "desc": "구간 [60:90) · 게이트 ON · 파생 없음 — CG25 대조군"},
    {"id": "DDd_60_90", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [60, 90],
     "derived": {"sources": ["volatility_20d", "volatility_60d", "atr_pct", "volume_ratio_5",
                             "rsi", "ma_position_20"], "lags": [1, 5]},
     "desc": "구간 [60:90) · 게이트 ON + Δ1·Δ5 파생 12컬럼 (사전등록 arm)"},
    {"id": "DDn_90_120", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [90, 120],
     "desc": "구간 [90:120) · 게이트 ON · 파생 없음 — CG25 대조군"},
    {"id": "DDd_90_120", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [90, 120],
     "derived": {"sources": ["volatility_20d", "volatility_60d", "atr_pct", "volume_ratio_5",
                             "rsi", "ma_position_20"], "lags": [1, 5]},
     "desc": "구간 [90:120) · 게이트 ON + Δ1·Δ5 파생 12컬럼 (사전등록 arm)"},
    {"id": "DDn_120_150", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [120, 150],
     "desc": "구간 [120:150) · 게이트 ON · 파생 없음 — CG25 대조군"},
    {"id": "DDd_120_150", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [120, 150],
     "derived": {"sources": ["volatility_20d", "volatility_60d", "atr_pct", "volume_ratio_5",
                             "rsi", "ma_position_20"], "lags": [1, 5]},
     "desc": "구간 [120:150) · 게이트 ON + Δ1·Δ5 파생 12컬럼 (사전등록 arm)"},








    {"id": "CO_core30_tsz_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "transform": "zscore_ts", "core_only": True,
     "desc": "게이트 ON + 종목별 시계열 z-score(직전 20행, shift=1) — 배포 가능 정규화 arm"},
    {"id": "CO_core30_csz_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "transform": "zscore", "core_only": True,
     "desc": "게이트 ON + 날짜별 횡단면 z-score — '배포 불가 정규화' 통제(tsz 와 같은 런 비교)"},















    {"id": "LF_snapfree_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "exclude_names": ["authenticity_avg", "avg_similarity_top10", "max_similarity", "negative_ratio",
                       "news_count_20d", "news_count_5d", "positive_ratio", "sentiment_avg",
                       "sentiment_avg_20d", "sentiment_avg_5d", "sentiment_trend", "sentiment_volatility",
                       "similarity_std", "theme_count", "theme_max_relevance", "twin_avg_correlation",
                       "twin_count"],
     "desc": "기준선 경로에서 스냅샷(종목상수·비영) 17컬럼 제외 — 기록 기준선의 누수 성분 측정"},
    {"id": "LF_nanfree_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "exclude_names": ["kalman_activity", "kalman_attention", "kalman_momentum", "kalman_sentiment",
                       "sns_*"],
     "desc": "플라시보: NaN 99% 컬럼 26개만 제외 — 선별에 안 들어가므로 모델이 비트 동일해야 한다"},












    {"id": "UQa_00_30", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "drop_dup": True, "codes_slice": [0, 30],
     "desc": "구간 [0:30) · 게이트 OFF + 중복라벨 제거 + edge top30 — 짝 대조군 LU_00_30"},
    {"id": "UQa_30_60", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "drop_dup": True, "codes_slice": [30, 60],
     "desc": "구간 [30:60) · 게이트 OFF + 중복라벨 제거 + edge top30 — 짝 대조군 LU_30_60"},
    {"id": "UQa_60_90", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "drop_dup": True, "codes_slice": [60, 90],
     "desc": "구간 [60:90) · 게이트 OFF + 중복라벨 제거 + edge top30 — 짝 대조군 LU_60_90"},
    {"id": "UQa_90_120", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "drop_dup": True, "codes_slice": [90, 120],
     "desc": "구간 [90:120) · 게이트 OFF + 중복라벨 제거 + edge top30 — 짝 대조군 LU_90_120"},
    {"id": "UQa_120_150", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "drop_dup": True, "codes_slice": [120, 150],
     "desc": "구간 [120:150) · 게이트 OFF + 중복라벨 제거 + edge top30 — 짝 대조군 LU_120_150"},
    {"id": "LU_00_30", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "codes_slice": [0, 30],
     "desc": "게이트 OFF 대조군(중복 포함) · 서로소 구간 [0:30)"},
    {"id": "LU_30_60", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "codes_slice": [30, 60],
     "desc": "게이트 OFF 대조군(중복 포함) · 서로소 구간 [30:60)"},
    {"id": "LU_60_90", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "codes_slice": [60, 90],
     "desc": "게이트 OFF 대조군(중복 포함) · 서로소 구간 [60:90)"},
    {"id": "LU_90_120", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "codes_slice": [90, 120],
     "desc": "게이트 OFF 대조군(중복 포함) · 서로소 구간 [90:120)"},
    {"id": "LU_120_150", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "codes_slice": [120, 150],
     "desc": "게이트 OFF 대조군(중복 포함) · 서로소 구간 [120:150)"},
















    {"id": "MIs_00_30", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "codes_slice": [0, 30], "derived": {"kind": "miss", "top_k": 10},
     "desc": "구간 [0:30) · 게이트 OFF + 결측 지시자 10개 강제투입 — 짝 대조군 LU_00_30"},
    {"id": "MIs_30_60", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "codes_slice": [30, 60], "derived": {"kind": "miss", "top_k": 10},
     "desc": "구간 [30:60) · 게이트 OFF + 결측 지시자 10개 강제투입 — 짝 대조군 LU_30_60"},
    {"id": "MIs_60_90", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "codes_slice": [60, 90], "derived": {"kind": "miss", "top_k": 10},
     "desc": "구간 [60:90) · 게이트 OFF + 결측 지시자 10개 강제투입 — 짝 대조군 LU_60_90"},
    {"id": "MIs_90_120", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "codes_slice": [90, 120], "derived": {"kind": "miss", "top_k": 10},
     "desc": "구간 [90:120) · 게이트 OFF + 결측 지시자 10개 강제투입 — 짝 대조군 LU_90_120"},
    {"id": "MIs_120_150", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "codes_slice": [120, 150], "derived": {"kind": "miss", "top_k": 10},
     "desc": "구간 [120:150) · 게이트 OFF + 결측 지시자 10개 강제투입 — 짝 대조군 LU_120_150"},







    {"id": "CN_00_30", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "cnd30",
     "codes_slice": [0, 30],
     "desc": "구간 [0:30) · 게이트 OFF + 조건부(비영) edge top30 — 짝 대조군 LU_00_30"},
    {"id": "CN_30_60", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "cnd30",
     "codes_slice": [30, 60],
     "desc": "구간 [30:60) · 게이트 OFF + 조건부(비영) edge top30 — 짝 대조군 LU_30_60"},
    {"id": "CN_60_90", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "cnd30",
     "codes_slice": [60, 90],
     "desc": "구간 [60:90) · 게이트 OFF + 조건부(비영) edge top30 — 짝 대조군 LU_60_90"},
    {"id": "CN_90_120", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "cnd30",
     "codes_slice": [90, 120],
     "desc": "구간 [90:120) · 게이트 OFF + 조건부(비영) edge top30 — 짝 대조군 LU_90_120"},
    {"id": "CN_120_150", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "cnd30",
     "codes_slice": [120, 150],
     "desc": "구간 [120:150) · 게이트 OFF + 조건부(비영) edge top30 — 짝 대조군 LU_120_150"},







    {"id": "AT_00_30", "kind": "abs_thresh", "horizon": 5, "q": 0.30, "thresh": 0.02,
     "select": "top30", "core_only": True, "codes_slice": [0, 30],
     "desc": "구간 [0:30) · 게이트 ON + 절대 임계 라벨(선행5일 >= +2%) — 짝 대조군 US_00_30"},
    {"id": "AT_30_60", "kind": "abs_thresh", "horizon": 5, "q": 0.30, "thresh": 0.02,
     "select": "top30", "core_only": True, "codes_slice": [30, 60],
     "desc": "구간 [30:60) · 게이트 ON + 절대 임계 라벨(선행5일 >= +2%) — 짝 대조군 US_30_60"},
    {"id": "AT_60_90", "kind": "abs_thresh", "horizon": 5, "q": 0.30, "thresh": 0.02,
     "select": "top30", "core_only": True, "codes_slice": [60, 90],
     "desc": "구간 [60:90) · 게이트 ON + 절대 임계 라벨(선행5일 >= +2%) — 짝 대조군 US_60_90"},
    {"id": "AT_90_120", "kind": "abs_thresh", "horizon": 5, "q": 0.30, "thresh": 0.02,
     "select": "top30", "core_only": True, "codes_slice": [90, 120],
     "desc": "구간 [90:120) · 게이트 ON + 절대 임계 라벨(선행5일 >= +2%) — 짝 대조군 US_90_120"},
    {"id": "AT_120_150", "kind": "abs_thresh", "horizon": 5, "q": 0.30, "thresh": 0.02,
     "select": "top30", "core_only": True, "codes_slice": [120, 150],
     "desc": "구간 [120:150) · 게이트 ON + 절대 임계 라벨(선행5일 >= +2%) — 짝 대조군 US_120_150"},







    {"id": "AT150_200", "kind": "abs_thresh", "horizon": 5, "q": 0.30, "thresh": 0.02,
     "select": "top30", "core_only": True, "codes_slice": [150, 200],
     "desc": "블록 [150:200) 50종목 · 게이트 ON + 절대 임계 라벨(+2%, h5) — 돈 지표 재현(CG109)"},










    {"id": "RPb_00_30", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [0, 30], "objective": "binary:logistic",
     "ens": {"skip": ["lightgbm", "catboost"]},
     "desc": "구간 [0:30) · 게이트 ON + 정렬·무과샘플 경로 · objective=binary (CG80 대조군)"},
    {"id": "RPb_30_60", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [30, 60], "objective": "binary:logistic",
     "ens": {"skip": ["lightgbm", "catboost"]},
     "desc": "구간 [30:60) · 게이트 ON + 정렬·무과샘플 경로 · objective=binary (CG80 대조군)"},
    {"id": "RPb_60_90", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [60, 90], "objective": "binary:logistic",
     "ens": {"skip": ["lightgbm", "catboost"]},
     "desc": "구간 [60:90) · 게이트 ON + 정렬·무과샘플 경로 · objective=binary (CG80 대조군)"},
    {"id": "RPb_90_120", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [90, 120], "objective": "binary:logistic",
     "ens": {"skip": ["lightgbm", "catboost"]},
     "desc": "구간 [90:120) · 게이트 ON + 정렬·무과샘플 경로 · objective=binary (CG80 대조군)"},
    {"id": "RPb_120_150", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [120, 150], "objective": "binary:logistic",
     "ens": {"skip": ["lightgbm", "catboost"]},
     "desc": "구간 [120:150) · 게이트 ON + 정렬·무과샘플 경로 · objective=binary (CG80 대조군)"},
    {"id": "RPr_00_30", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [0, 30], "objective": "rank:pairwise",
     "ens": {"skip": ["lightgbm", "catboost"]},
     "desc": "구간 [0:30) · 게이트 ON + 정렬·무과샘플 경로 · objective=rank:pairwise (CG80 arm)"},
    {"id": "RPr_30_60", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [30, 60], "objective": "rank:pairwise",
     "ens": {"skip": ["lightgbm", "catboost"]},
     "desc": "구간 [30:60) · 게이트 ON + 정렬·무과샘플 경로 · objective=rank:pairwise (CG80 arm)"},
    {"id": "RPr_60_90", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [60, 90], "objective": "rank:pairwise",
     "ens": {"skip": ["lightgbm", "catboost"]},
     "desc": "구간 [60:90) · 게이트 ON + 정렬·무과샘플 경로 · objective=rank:pairwise (CG80 arm)"},
    {"id": "RPr_90_120", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [90, 120], "objective": "rank:pairwise",
     "ens": {"skip": ["lightgbm", "catboost"]},
     "desc": "구간 [90:120) · 게이트 ON + 정렬·무과샘플 경로 · objective=rank:pairwise (CG80 arm)"},
    {"id": "RPr_120_150", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_slice": [120, 150], "objective": "rank:pairwise",
     "ens": {"skip": ["lightgbm", "catboost"]},
     "desc": "구간 [120:150) · 게이트 ON + 정렬·무과샘플 경로 · objective=rank:pairwise (CG80 arm)"},










    {"id": "CG182_ctl_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True,
     "desc": "CG182 대조군 — 게이트 ON + top30 (현행 경로), 피처 제거 없음"},
    {"id": "CG182_momdrop_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True,
     "exclude_names": ["momentum_op", "momentum_ni", "momentum_3_12m"],
     "desc": "CG182 arm — 부호가 뒤집힌 모멘텀 3종 제거(같은 런·같은 행)"},










    {"id": "CG189_ctl_nog_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": False,
     "desc": "CG189 대조군 — 게이트 OFF + edge top30(전체 213피처 후보), 피처 제거 없음"},
    {"id": "CG189_momdrop_nog_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": False,
     "exclude_names": ["momentum_op", "momentum_ni", "momentum_3_12m"],
     "desc": "CG189 arm — 부호가 뒤집힌 모멘텀 3종 제거(게이트 OFF·같은 런·같은 행)"},








    {"id": "CG192_mask25_nog_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": False,
     "train_mask_daychg_gte": 0.25,
     "desc": "CG192 arm — 학습에서 당일등락 ≥ +25%(체결 불가) 행 제외, 테스트 행은 그대로"},



    {"id": "CG192_mask10_nog_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": False,
     "train_mask_daychg_gte": 0.10,
     "desc": "CG192 감도 arm — 학습에서 당일등락 ≥ +10%(4.17%) 행 제외, 테스트 그대로"},









    {"id": "CG193_ctl_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": False,
     "desc": "CG193 대조군 — 게이트 OFF·h5(기준선과 같은 과제), 피처 제거 없음"},
    {"id": "CG193_h3", "kind": "quantile", "horizon": 3, "q": 0.30, "select": "top30",
     "core_only": False,
     "desc": "CG193 arm — 호라이즌 3(라벨 잡음 ↑·표본 ↑)"},
    {"id": "CG193_h10", "kind": "quantile", "horizon": 10, "q": 0.30, "select": "top30",
     "core_only": False,
     "desc": "CG193 arm — 호라이즌 10(스무딩 연속판)"},
    {"id": "CG193_h20", "kind": "quantile", "horizon": 20, "q": 0.30, "select": "top30",
     "core_only": False,
     "desc": "CG193 arm — 호라이즌 20(과확장 상단 — 라벨 잡음 최소·신호 희석 위험)"},








    {"id": "CG195_dsns_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": False,
     "derived": {"sources": ["sns_attention_score", "sns_momentum_score", "sns_post_count",
                             "disclosure_count_5d", "economic_event_count_7d"],
                 "lags": [1, 5]},
     "desc": "CG195 arm — 게이트 OFF + Δ1·Δ5 파생 10컬럼 (SNS·공시·이벤트 흐름, 사전등록)"},
    {"id": "CG195_dsup_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": False,
     "derived": {"sources": ["foreign_net_buy_5d", "institution_net_buy_5d",
                             "short_selling_ratio", "program_trading_ratio"],
                 "lags": [1, 5]},
     "desc": "CG195 arm — 게이트 OFF + Δ1·Δ5 파생 8컬럼 (수급·공매도 흐름, 사전등록)"},













    {"id": "CG196_ctl_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": False,
     "desc": "CG196 대조군 — 게이트 OFF + edge top30(전체 213피처 후보), 제거 없음"},
    {"id": "CG196_noconst_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": False,
     "exclude_names": ["theme_count", "theme_max_relevance", "theme_exposure_5d",
                       "similarity_std", "similar_count", "twin_avg_correlation", "twin_count",
                       "avg_similarity_top10", "max_similarity", "sector_count",
                       "authenticity_avg", "negative_ratio", "positive_ratio",
                       "sentiment_avg", "sentiment_avg_5d", "sentiment_avg_20d", "sentiment_avg_db",
                       "sentiment_momentum", "sentiment_trend", "sentiment_volatility",
                       "news_count_5d", "news_count_20d", "short_interest_ratio", "days_to_cover",
                       "etf_flow_5d", "institution_ownership_pct", "margin_balance_change",
                       "credit_balance_change", "value_ncav"],
     "desc": "CG196 arm — 종목상수 29개(라벨 미사용 정의) 제거 · 같은 런·같은 행·같은 선별"},










    {"id": "CG198_dupdrop_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": False, "drop_dup": True,
     "desc": "CG198 arm — 중복라벨(__dupN) 선별 후보 제거(drop_dup) · 같은 런 대조군 CG196_ctl_h5"},









    {"id": "CG199_ctl_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": False,
     "desc": "CG199 대조군 — 앵커 2026-11-05 · 게이트 OFF · edge top30 · 제거/파생 없음"},
    {"id": "CG199_dsns_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": False,
     "derived": {"sources": ["sns_attention_score", "sns_momentum_score", "sns_post_count",
                             "disclosure_count_5d", "economic_event_count_7d"],
                 "lags": [1, 5]},
     "desc": "CG199 arm — CG195_dsns 설정 그대로(Δ1·Δ5 파생 10컬럼) · 앵커만 2거래일 앞(재현성 검정)"},











    {"id": "CG202_ctl_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": False,
     "desc": "CG202 대조군 — 게이트 OFF · edge top30 · 제거/중복/파생 없음"},
    {"id": "CG202_clean_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": False, "drop_dup": True,
     "exclude_names": ["theme_count", "theme_max_relevance", "theme_exposure_5d",
                       "similarity_std", "similar_count", "twin_avg_correlation", "twin_count",
                       "avg_similarity_top10", "max_similarity", "sector_count",
                       "authenticity_avg", "negative_ratio", "positive_ratio",
                       "sentiment_avg", "sentiment_avg_5d", "sentiment_avg_20d", "sentiment_avg_db",
                       "sentiment_momentum", "sentiment_trend", "sentiment_volatility",
                       "news_count_5d", "news_count_20d", "short_interest_ratio", "days_to_cover",
                       "etf_flow_5d", "institution_ownership_pct", "margin_balance_change",
                       "credit_balance_change", "value_ncav"],
     "desc": "CG202 arm1 — 종목상수 29개 제거 + 중복라벨 제거(청정 선별, 파생 없음)"},
    {"id": "CG202_clean_dsns_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": False, "drop_dup": True,
     "exclude_names": ["theme_count", "theme_max_relevance", "theme_exposure_5d",
                       "similarity_std", "similar_count", "twin_avg_correlation", "twin_count",
                       "avg_similarity_top10", "max_similarity", "sector_count",
                       "authenticity_avg", "negative_ratio", "positive_ratio",
                       "sentiment_avg", "sentiment_avg_5d", "sentiment_avg_20d", "sentiment_avg_db",
                       "sentiment_momentum", "sentiment_trend", "sentiment_volatility",
                       "news_count_5d", "news_count_20d", "short_interest_ratio", "days_to_cover",
                       "etf_flow_5d", "institution_ownership_pct", "margin_balance_change",
                       "credit_balance_change", "value_ncav"],
     "derived": {"sources": ["sns_attention_score", "sns_momentum_score", "sns_post_count",
                             "disclosure_count_5d", "economic_event_count_7d"],
                 "lags": [1, 5]},
     "desc": "CG202 arm2 — 청정 선별 + Δ1·Δ5 파생 10컬럼 (상호작용 검정의 처리군)"},









    {"id": "CG204_clean_smooth_d1_h5", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "drop_dup": True,
     "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "exclude_names": ["theme_count", "theme_max_relevance", "theme_exposure_5d",
                       "similarity_std", "similar_count", "twin_avg_correlation", "twin_count",
                       "avg_similarity_top10", "max_similarity", "sector_count",
                       "authenticity_avg", "negative_ratio", "positive_ratio",
                       "sentiment_avg", "sentiment_avg_5d", "sentiment_avg_20d", "sentiment_avg_db",
                       "sentiment_momentum", "sentiment_trend", "sentiment_volatility",
                       "news_count_5d", "news_count_20d", "short_interest_ratio", "days_to_cover",
                       "etf_flow_5d", "institution_ownership_pct", "margin_balance_change",
                       "credit_balance_change", "value_ncav"],
     "desc": "CG204 — CO_smooth_d1_h5 + 종목상수 29개 제거·중복라벨 제거 (배포가능 조정 arm 위 청정화)"},











    {"id": "CG205_ctl_nocore_h5", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": False,
     "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "desc": "CG205 대조군 — 게이트 OFF + 스무딩 + depth1 (스윕 기본 경로)"},
    {"id": "CG205_excl_h5", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": False,
     "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "exclude_names": ["theme_count", "theme_max_relevance", "similarity_std", "similar_count",
                       "twin_avg_correlation", "twin_count", "avg_similarity_top10", "max_similarity",
                       "sector_count"],
     "desc": "CG205 처리군 — 게이트 OFF 동일 조건 + 종목상수 9개(std>0) 제외 (누수게이트 ③ 실측)"},













    {"id": "CO_FW4_h5", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "gate_add": ["similar_stocks_return_std", "similar_stocks_return_avg", "debt_ratio"],
     "desc": "CG234 — 게이트 ON + 스크린 dense 후보 3종(similar_stocks 2·debt_ratio)을 선별 후 강제 포함"},






    {"id": "CG235_lin_h5", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "linear": True, "linear_C": 0.1,
     "desc": "CG235 — 같은 선별·같은 폴드·같은 시드에서 학습기만 표준화 로지스틱(C=0.1)로 교체"},
    {"id": "CG235_lin_c01_h5", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "linear": True, "linear_C": 0.01,
     "desc": "CG235b — 같은 경로·로지스틱 C=0.01(더 강한 L2) — 정규화 민감도 확인"},
    {"id": "CG235_lin_c10_h5", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "linear": True, "linear_C": 1.0,
     "desc": "CG235c — 같은 경로·로지스틱 C=1.0(약한 L2) — 정규화 민감도 확인"},


    {"id": "CG237_lin_all_h5", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "all",
     "core_only": True, "linear": True, "linear_C": 0.1,
     "desc": "CG237 — 선형(C=0.1) + core48 전체 사용(선별 top30 대신) — 피처 수 민감도"},














    {"id": "CG241_ctl_h5", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True,
     "desc": "CG241 대조군 — ffill 패널에서 ffill_* 미사용(원본과 동일 정보)"},
    {"id": "CG241_ff_h5", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True,
     "gate_add": ["ffill_sns_sentiment_score", "ffill_sns_attention_score",
                  "ffill_sns_momentum_score", "ffill_sns_stale_days"],
     "desc": "CG241 처리군 — 같은 선별 + as-of 전방채움 4종(상한 5거래일·경과일 지표) 강제 포함"},











    {"id": "CG240_ex_h5", "kind": "excess_smooth_thresh", "horizon": 5, "q": 0.30, "thresh": 0.0,
     "select": "top30",
     "core_only": True, "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "desc": "CG240 — 라벨을 '당일 횡단면 풀평균 초과(스무딩 동일)' 이진으로 (나머지 동일)"},














    {"id": "CG285_ctl_h5", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True,
     "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "desc": "CG285 대조군 — tv 패널에서 tv_* 미사용(= CO_smooth_d1_h5 와 동일 설정)"},
    {"id": "CG285_tv_h5", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True,
     "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "gate_add": ["tv_rank_cs_20", "tv_rank_cs_60", "tv_ratio_5_20", "tv_log"],
     "desc": "CG285 처리군 — 같은 선별 + as-of 종목별 거래대금 4종(20/60일 횡단면 rank·5/20 비율·log) 강제 포함"},








    {"id": "CG286_ctl_h5", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "desc": "CG286 대조군 — 전체 행 학습(현행), tv_log 는 마스크로도 쓰지 않음"},
    {"id": "CG286_liq1_h5", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "train_mask_col": "tv_log", "train_mask_gte": 20.72326583794641,
     "desc": "CG286 arm — 학습에서 거래대금 < 10억 행 제외(테스트 그대로)"},
    {"id": "CG286_liq3_h5", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "train_mask_col": "tv_log", "train_mask_gte": 21.821878125947855,
     "desc": "CG286 감도 arm — 학습에서 거래대금 < 30억 행 제외(마스크 강도 반응 확인)"},






    {"id": "CG287_ctl_h5", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "desc": "CG287 대조군 — 전체 행 동일 가중(CG286_ctl 과 동일 설정)"},
    {"id": "CG287_wfill_h5", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "weight": {"kind": "fillable", "col": "tv_log", "gte": 20.72326583794641,
                "w_high": 2.0, "w_low": 0.5},
     "desc": "CG287 arm — 같은 행 도메인, 체결가능 행 가중 2.0 / 불가 0.5 (평균 1 정규화)"},
    {"id": "CG287_wfill4_h5", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "weight": {"kind": "fillable", "col": "tv_log", "gte": 20.72326583794641,
                "w_high": 4.0, "w_low": 0.25},
     "desc": "CG287 감도 arm — 체결가능 가중 4.0 / 불가 0.25 (강한 이동)"},










    {"id": "CG323_ctl_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": False,
     "exclude_names": ["ss_*"],
     "desc": "CG323 대조군 — 같은 패널에서 공매도 4열만 제외(게이트 OFF·top30)"},
    {"id": "CG323_ss_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": False,
     "desc": "CG323 arm — 공매도 4열 가산(ss_ratio·ma5·ma20·z20, t-1 지연)"},










    {"id": "CG324_maskq_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "label_mask": {"daychg_lt": 25.0, "col": "tv_log", "col_gte": 20.72},
     "desc": "CG324 arm — 분위 기준모집단을 체결가능 행(등락<+25% & tv_log≥20.72)으로 (행 유지)"},




    {"id": "CG325_seedA_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "seed_offset": 100, "desc": "CG325 시드잡음 A — LS_quant_q30_h5 와 동일, 시드 100~104"},
    {"id": "CG325_seedB_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "seed_offset": 200, "desc": "CG325 시드잡음 B — LS_quant_q30_h5 와 동일, 시드 200~204"},










    {"id": "CG430_open_h5", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "entry": "next_open",
     "desc": "CG430 arm — 익일 시가 진입 라벨(close(t+k)/open(t+1)−1, 스무딩) · 대조군 CO_smooth_d1_h5"},














    {"id": "CG431_ctl_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "desc": "CG431 대조군 — v6 패널 221피처 현행(죽은 열 포함)"},
    {"id": "CG431_rmdead_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "exclude_names": ["authenticity_avg", "credit_balance_change", "days_to_cover", "etf_flow_5d",
                       "event_patent_5d", "event_recall_5d", "institution_ownership_pct",
                       "margin_balance_change", "sector_count", "sector_momentum",
                       "short_interest_ratio", "value_ncav", "cycle_up", "cycle_down",
                       "event_market_liquidity_5d", "interest_rate_change_1m",
                       "krx_total_trading_value"],
     "desc": "CG431 arm — 죽은 열 17개를 선별 후보에서 제외(같은 런·같은 폴드)"},

    {"id": "CG450_ctl_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "exclude_names": ["idc_*"],
     "desc": "CG450 대조군 — 패치 패널에서 인트라데이(idc_*) 열을 선별 후보에서 제외(같은 런·같은 행)"},
    {"id": "CG450_intraday_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "desc": "CG450 arm — Naver 전구간 분봉 close-only 피처 포함(같은 패널·같은 폴드·같은 시드)"},
]


def apply_train_domain_mask(tr, threshold):
    """학습 도메인 마스크(CG192, 2026-11-11) — 당일등락 ≥ threshold 인 행을 **학습에서만** 제거한다.

    왜: 돈 지표는 항상 체결성 필터(당일등락 < +25% = 살 수 있는 행)를 건다. 학습 라벨이 그런 행을
    포함하면 모델은 '살 수 없는 상승(상한가 연속)'을 예측하도록 학습되고, 그 예측은 평가 도메인에서
    재현되지 않는다(실측: close_gate_probe 필터 전 +1.191%/세션 → 필터 후 음수).

    계약:
      - threshold is None → 입력을 **그대로** 돌려준다(n_masked=0, 비트 동일 경로 = 기존 실험 무영향).
      - 당일등락 = 종목별 종가 pct_change(돈 지표 필터와 같은 정의).
      - NaN(그 종목의 첫 행, 폴드 절단면)은 **보존**한다 — '측정 불가'와 '체결 불가'는 다르다.
      - 테스트 행은 이 함수를 거치지 않는다 → 두 arm 의 폴드 AUC 행 도메인이 동일해 Δ 가 비교 가능.
    """
    if threshold is None:
        return tr, 0
    dc = np.asarray(tr.groupby("stock_code", sort=False)["price"].pct_change().values,
                    dtype=float)
    drop = np.greater_equal(dc, float(threshold))
    return tr[~drop], int(drop.sum())


def build_label_mask(df, spec):
    """CG324(2026-11-18): **분위 기준 모집단** 마스크 — 행은 버리지 않고 문턱 계산에서만 뺀다.

    왜: 돈 지표의 체결성 필터(당일등락 < +25% & 거래대금 ≥ 10억, CG283)가 실제로 버리는 행이
    패널의 71.6% 다. 그런데 quantile 라벨은 '살 수 없는 행'까지 모집단에 넣어 그날의 hi/lo 를
    정한다 → 학습 목표가 평가 도메인(체결가능)과 어긋난다. 여기서는 **문턱만** 체결가능 행으로
    계산하고 라벨 부여는 전행에 유지한다(행 도메인 보존 = 대조군과 폴드 AUC 비교 가능).

    spec: {"daychg_lt": 25.0, "col": "tv_log", "col_gte": 20.72}
      - daychg_lt: 종목별 종가 pct_change(%) ≥ 이 값이면 '못 사는 행' → 모집단 제외.
        NaN(종목 첫 행·절단면)은 **보존**한다(CG192 와 같은 계약: 측정 불가 ≠ 체결 불가).
      - col/col_gte: 지정 컬럼(예 tv_log=log1p(거래대금))이 임계 미만·결측이면 모집단 제외.
    반환: (mask: np.bool_ 배열 len(df), diag: dict)
    """
    if not spec:
        return None, {}
    mask = np.ones(len(df), dtype=bool)
    diag = {"rows": int(len(df))}
    if spec.get("daychg_lt") is not None:
        dc = df.groupby("stock_code", sort=False)["price"].pct_change().to_numpy(dtype=float) * 100.0
        drop = np.isfinite(dc) & np.greater_equal(dc, float(spec["daychg_lt"]))
        mask &= ~drop
        diag["daychg_lt"] = float(spec["daychg_lt"])
        diag["daychg_dropped"] = int(drop.sum())
    col, cgte = spec.get("col"), spec.get("col_gte")
    if col and cgte is not None:
        if col not in df.columns:
            raise RuntimeError(f"label_mask col={col} 이 패널에 없다")
        v = pd.to_numeric(df[col].to_numpy(dtype=float), errors="coerce")
        drop = ~(np.isfinite(v) & np.greater_equal(v, float(cgte)))
        mask &= ~drop
        diag.update({"col": col, "col_gte": float(cgte), "col_dropped": int(drop.sum())})
    diag["keep"] = int(mask.sum())
    diag["keep_frac"] = round(float(mask.mean()), 4)
    return mask, diag


def apply_train_mask_col(tr, col, threshold):
    """학습 행만 **지정 컬럼 임계값**으로 거른다 (CG286, 2026-11-15) — 테스트 행은 그대로.

    왜 필요한가(CG192 의 한계 실측): CG192 는 '당일등락 ≥ +25%'(체결 불가)를 학습에서 뺐지만
    그런 행이 패널의 **0.60%(325/54,497)뿐**이라 Δ+0.0003 으로 검정력이 없었다. 반면 돈 지표의
    체결성 필터가 실제로 버리는 것은 **거래대금 10억 미만 = 71.6%**(CG283 실측)이다 → 같은
    '학습 도메인 정합' 가설을 훨씬 큰 마스크로 다시 잰다.

    계약(apply_train_domain_mask 와 동일):
      - col 없음/threshold None → 입력 그대로(n=0, 비트 동일 경로).
      - 임계 미만·결측 행은 학습에서만 제거, **테스트 행은 손대지 않는다**(Δ 비교가능성).
      - 마스크 기준 컬럼은 CORE_FEATURES(48) 밖(tv_*)이어야 한다 — 후보를 바꾸면 교란된다.
    """
    if not col or threshold is None:
        return tr, 0
    if col not in tr.columns:
        raise RuntimeError(f"train_mask_col={col} 이 학습 프레임에 없다 — 패널에 그 컬럼이 필요하다")
    v = pd.to_numeric(tr[col].to_numpy(dtype=float), errors="coerce")
    keep = np.isfinite(v) & np.greater_equal(v, float(threshold))
    return tr[keep], int((~keep).sum())


def fold_window_indices(n, n_folds, step, right_anchored=False):
    """폴드별 (cut_idx, lo, hi) — dd(라벨 유효 날짜 목록) 인덱스.

    왼쪽(현행): fold i 테스트 = dd[step*i : min(n, step*(i+1))] (cut = lo-1)
    오른쪽(앵커, --asof-date 경로): 마지막 폴드 테스트가 dd[n-1] 에서 끝나도록 역산
      → hi = n - (n_folds - i)*step, lo = hi - step. 앵커가 하루 움직이면 **전 폴드가 함께 이동**한다.

    왜 두 경로인가(CG199 실측 2026-11-12): 왼쪽 정렬은 n 이 step*(folds+1) 보다 크면 끝 여분날을
    마지막 폴드에 몰아 넣어 컷이 고정된다 → 끝 1~2일을 잘라도 테스트 날짜 집합이 비트 동일했다
    (폴드 AUC 4자리 동일). '앵커 이동'이라는 주장은 오른쪽 정렬에서만 성립한다.
    """
    out = []
    for i in range(1, int(n_folds) + 1):
        if right_anchored:
            hi = int(n) - (int(n_folds) - i) * int(step)
            lo = hi - int(step)
            if lo < 1 or hi > int(n):
                raise RuntimeError(f"앵커 격자 범위 밖: fold{i} lo={lo} hi={hi} n={n} step={step}")
        else:
            lo = int(step) * i
            hi = min(int(n), int(step) * (i + 1))
        out.append((lo - 1, lo, hi))
    return out


def transform_matrix(Xdf, dates, kind, codes=None):
    """피처 행렬의 **날짜별 횡단면 변환**(rank/zscore) 또는 **종목별 시계열 변환**(zscore_ts).

    근거(실측): 패널 210피처 중 34개가 날짜 내 종목간 분산이 0(시장 전체 동일값)이다.
    또 스케일이 피처마다 제각각(원/비율/지수)이다. 날짜별 rank/z-score 로 바꾸면
    시장레벨 성분이 사라지고 종목간 비교 가능한 형태가 된다 — 이 스택에서 측정된 적 없다.
    각 조회일의 정보만 쓰므로(같은 날 다른 종목) 미래 정보 누수가 아니다.

    zscore_ts(2026-09-29, CG32): 종목 **자기 과거**만 쓰는 롤링 z-score. 횡단면 rank/zscore 는
    그날 **다른 종목**의 값이 필요해 단일 종목 스트리밍 추론 경로에서 재현 불가였지만,
    자기 과거는 그 종목의 과거 행만 조회하면 되므로 배포 가능하다. level/추세를 종목별로
    제거하고 '자기 역사 대비 오늘의 위치'만 남긴다. 창은 shift(1) 기준(직전 20행)이라
    당일 값이 자기 기준을 오염시키지 않고 미래정보도 쓰지 않는다.
    """
    if kind in (None, "none"):
        return Xdf.values
    if kind == "rank":
        return Xdf.groupby(dates).rank(pct=True).values
    if kind == "zscore":
        g = Xdf.groupby(dates)
        mu = g.transform("mean")
        sd = g.transform("std").replace(0.0, np.nan)
        return ((Xdf - mu) / sd).fillna(0.0).values
    if kind == "zscore_ts":
        if codes is None:
            raise ValueError("zscore_ts 변환에는 codes 가 필요하다(종목별 시계열)")
        _TS_WIN, _TS_MIN = 20, 10
        df = Xdf.copy()
        df["_c"] = list(map(str, codes))
        df["_d"] = list(map(str, dates))
        df = df.sort_values(["_c", "_d"], kind="mergesort")
        cols = [c for c in df.columns if c not in ("_c", "_d")]
        g = df.groupby("_c", sort=False)[cols]
        mu = g.transform(lambda s: s.shift(1).rolling(_TS_WIN, min_periods=_TS_MIN).mean())
        sd = g.transform(lambda s: s.shift(1).rolling(_TS_WIN, min_periods=_TS_MIN).std())
        z = (df[cols] - mu) / sd.replace(0.0, np.nan)

        z = z.replace([np.inf, -np.inf], np.nan).fillna(0.0)
        return df.assign(**{c: z[c] for c in cols})[cols].values
    raise ValueError(f"unknown transform: {kind}")


def make_weights(spec, dates_tr, fwd_tr=None, day_pos=None, t0_pos=None, col_vals=None):
    """CG23 표본 가중 — 학습 행별 가중치 벡터(평균 1 정규화). None 이면 '가중 없음'.

    spec=None            → None  (원 경로 그대로: sample_weight 를 아예 넘기지 않는다)
    {"kind":"uniform"}   → 1.0 벡터 — **가중 배관 자체의 통제**(Δ≈0 이어야 귀속이 성립)
    {"kind":"time_decay","hl":60} → 0.5 ** (경과 거래일 / hl). 가장 최근 학습일이 가중 1.
    {"kind":"absret","clip":0.05} → |h일 선행수익| 기반(상한 clip). h5 보유에서 '크게 움직인'
                                    표본에 집중하는 가설.
    {"kind":"fillable","col":"tv_log","gte":20.72,"w_high":2.0,"w_low":0.5}
                         → CG287(2026-11-15): **행을 버리지 않고** '살 수 있는 행'(거래대금 ≥ 임계)에
                           만 가중을 올린다. CG286(행 제거)은 AUC 를 −0.055 붕괴시켰다 — 정보 손실이
                           원인이라면, 가중은 정보를 유지한 채 배포 모집단 쪽으로 학습 신호만 옮긴다.

    거래일 축을 쓰는 이유: 패널은 거래일만 있고 캘린더 결측(휴장)이 있어 달력일 기준 감쇠는
    구간에 따라 다르게 깎인다. day_pos 는 폴드가 쓰는 dd(정렬된 날짜 목록)의 위치다.
    """
    if not spec:
        return None
    kind = str(spec.get("kind") or "").lower()
    n = len(dates_tr)
    if kind == "uniform":
        w = np.ones(n, dtype=float)
    elif kind == "fillable":
        if col_vals is None:
            raise RuntimeError(f"fillable 가중에 컬럼 값이 필요하다(spec={spec})")
        v = np.asarray(col_vals, dtype=float)
        if len(v) != n:
            raise RuntimeError(f"fillable 가중 길이 불일치({len(v)} vs {n}) — tr 순서 보존 실패")
        hi = float(spec.get("w_high") or 2.0)
        lo = float(spec.get("w_low") or 0.5)
        gte = float(spec.get("gte") or 20.72326583794641)
        fill = np.isfinite(v) & (v >= gte)
        w = np.where(fill, hi, lo)
    elif kind == "time_decay":
        hl = float(spec.get("hl") or 60.0)
        if day_pos is None or t0_pos is None:
            raise RuntimeError("time_decay 가중에 day_pos/t0_pos 가 필요하다")
        _t0 = float(t0_pos)
        pos = np.array([day_pos.get(str(d), _t0) for d in dates_tr], dtype=float)
        age = np.maximum(_t0 - pos, 0.0)
        w = 0.5 ** (age / hl)
    elif kind == "absret":
        if fwd_tr is None:
            raise RuntimeError("absret 가중에 선행수익(_fwd) 이 필요하다")
        a = np.abs(np.asarray(fwd_tr, dtype=float))
        clip = float(spec.get("clip") or 0.05)
        a = np.minimum(a, clip)
        a = np.where(np.isfinite(a), a, np.nan)
        med = float(np.nanmedian(a)) if np.isfinite(a).any() else 0.0
        a = np.where(np.isfinite(a), a, med)
        mx = float(a.max()) if a.size and a.max() > 0 else 1.0
        w = 0.25 + 0.75 * (a / mx)
    else:
        raise RuntimeError(f"unknown weight kind: {kind}")
    if not np.all(np.isfinite(w)) or float(w.sum()) <= 0:
        raise RuntimeError("가중치 계산 실패(비유한 또는 합 0)")
    return w / w.mean()


_orig_train_seed = ml.train_seed


def train_seed_weighted(X_train, X_val, X_test, y_train, y_val, y_test, feature_names,
                        out_dir, seed, lr, depth, n_estimators, allow_sentiment,
                        scale_pos_weight, w=None):
    """ml.train_seed 의 **가중 확장판**(CG23). w=None 이면 원본 함수를 그대로 호출한다.

    왜 복제가 필요한가: train_seed 는 내부에서 oversample_balance(양성 복제 + 셔플)와
    split_train_val(0.67 시간순) 을 거치는데, 이 두 함수는 가중치를 돌려주지 않는다.
    가중치를 행과 함께 옮기려면 같은 순서(rng → choice → shuffle)를 그대로 재현해야 한다.
    재현이 틀리면 '가중 효과'가 아니라 '다른 표본'을 재게 되므로, 회귀 테스트
    (scripts/_sample_weight_test.py)로 균등 가중이 원 경로와 동일함을 먼저 증명한다.
    """
    if w is None:
        return _orig_train_seed(X_train, X_val, X_test, y_train, y_val, y_test,
                                feature_names, out_dir, seed, lr, depth, n_estimators,
                                allow_sentiment, scale_pos_weight)
    _tc = ml.tc
    curated = _tc.select_curated_features(feature_names, allow_sentiment)
    if not curated:
        raise RuntimeError("no curated features selected")
    idx = [feature_names.index(f) for f in curated]
    X_train_c = X_train[:, idx]
    X_test_c = X_test[:, idx]

    y = np.asarray(y_train).astype(int)
    w = np.asarray(w, dtype=float).reshape(-1)
    if len(w) != len(y):
        raise RuntimeError(f"가중치 길이 불일치({len(w)} vs {len(y)}) — 행 순서가 어긋났다")

    rng = np.random.default_rng(seed)
    n_pos = int(y.sum())
    n_neg = len(y) - n_pos
    if n_pos > 0 and n_pos < n_neg:
        pos_idx = np.where(y == 1)[0]
        oversampled = rng.choice(pos_idx, size=n_neg - n_pos, replace=True)
        balanced = np.concatenate([np.arange(len(y)), oversampled])
        rng.shuffle(balanced)
        X_bal, y_bal, w_bal = X_train_c[balanced], y[balanced], w[balanced]
    else:
        X_bal, y_bal, w_bal = X_train_c, y, w
    cut = int(len(X_bal) * 0.67)
    Xc_t, yc_t, wc_t = X_bal[:cut], y_bal[:cut], w_bal[:cut]
    Xc_v, yc_v = X_bal[cut:], y_bal[cut:]

    ensemble = ml.EnsembleModel(model_dir=out_dir)
    _tc.apply_hyperparams(ensemble, lr, depth, n_estimators, seed)
    if scale_pos_weight is not None:
        for model in ensemble.models:
            p = getattr(model, "params", None)
            if p is not None and "scale_pos_weight" in p:
                p["scale_pos_weight"] = float(scale_pos_weight)
    ensemble.train(Xc_t, yc_t, Xc_v, yc_v, sample_weight=wc_t)

    test_probs = ensemble.predict(X_test_c)
    ens_auc = ml._safe_auc(y_test, test_probs)
    model_aucs = {}
    for name, model in zip(ensemble.model_names, ensemble.models):
        try:
            model_aucs[name] = ml._safe_auc(y_test, model.predict(X_test_c))
        except Exception:
            model_aucs[name] = 0.5
    return ens_auc, model_aucs, curated, ensemble


def _rank_order_and_groups(dates, split=0.67):
    """랭킹 학습용 정렬·그룹 분할(CG80).

    반환: (order, cut, gc_t, gc_v)
      order : 날짜 오름차순(안정 정렬) 인덱스 — group 은 **연속 블록**이어야 한다.
      cut   : train/val 경계(그룹 경계에 스냅) — 같은 날짜가 양쪽에 걸치지 않게 한다.
      gc_t/gc_v : 각 구간의 날짜별 행 수(합 == 구간 행 수).
    왜 그룹 경계에 스냅하는가: xgboost 랭킹 objective 는 group 이 학습행을 정확히 분할할 것을
    요구하고, 같은 날짜가 train/val 에 걸치면 그 날짜의 횡단면 비교가 반쪽이 되어 의미가 깨진다.
    """
    d = np.asarray([str(x) for x in dates])
    if d.size == 0:
        raise RuntimeError("빈 dates — 랭킹 그룹을 만들 수 없다")
    order = np.argsort(d, kind="stable")
    _, counts = np.unique(d[order], return_counts=True)
    ends = np.cumsum(counts)
    cut = int(ends[np.searchsorted(ends, int(len(d) * split), side="left")])
    cut = max(1, min(cut, len(d) - 1))
    _, gc_t = np.unique(d[order][:cut], return_counts=True)
    _, gc_v = np.unique(d[order][cut:], return_counts=True)
    return order, cut, gc_t, gc_v


def train_seed_rank(X_train, X_test, y_train, y_test, feature_names, out_dir,
                    seed, lr, depth, n_estimators, train_dates, objective):
    """CG80(2026-10-03): **학습 목적함수(objective) 축** — 날짜별 횡단면 랭킹.

    왜 이 축인가: 이 스택의 70+ 사이클 모든 arm 이 `binary:logistic`(행 독립) 하나를
    공유한다. 그런데 라벨은 '날짜내 분위'(횡단면 상대)라, 학습은 날짜 경계를 전혀 모른다.
    `rank:pairwise` + group(날짜별 행 수)은 **날짜 안에서** 양성을 음성 위로 올리도록 직접
    최적화한다. 추론은 행별 점수 하나라 계약 변경이 없다(배포 가능 — rank 변환과 다른 점).

    프로토콜(두 arm 이 **완전히 동일**하고 objective 문자열만 다르다):
      · 과샘플(양성 복제)·셔플을 **하지 않는다** — group 이 깨지기 때문. 정렬만 한다.
      · xgboost 단일 모델(config 의 ens.skip 으로 lgb/cat 제외) — 앙상블이 랭킹점수와 확률을
        섞으면 스케일이 어긋난다.
      · 폴드 경계를 날짜 그룹 경계에 맞춘다(같은 날짜가 train/val 에 걸치지 않게).
    ⚠ 대조군(RPb_*)은 같은 경로에 objective="binary:logistic" 만 준 것이다 — 정렬·분할·
      모델이 동일하므로 Δ 는 objective 하나로 귀속된다.
    """
    _tc = ml.tc
    curated = _tc.select_curated_features(feature_names, True)
    if not curated:
        raise RuntimeError("no curated features selected")
    idx = [feature_names.index(f) for f in curated]
    Xtr = X_train[:, idx]
    Xte = X_test[:, idx]

    dts = np.asarray([str(x) for x in train_dates])
    if len(dts) != len(Xtr):
        raise RuntimeError(
            f"train_dates 길이 불일치({len(dts)} vs {len(Xtr)}) — 학습행 순서가 어긋났다")
    order, cut, gc_t, gc_v = _rank_order_and_groups(dts, 0.67)
    Xs = Xtr[order]
    ys = np.asarray(y_train).astype(int)[order]
    ds = dts[order]
    if int(gc_t.sum()) != cut or int(gc_v.sum()) != len(Xs) - cut:
        raise RuntimeError("group 합이 행 수와 다르다 — 랭킹 그룹 계산 버그")
    Xc_t, yc_t = Xs[:cut], ys[:cut]
    Xc_v, yc_v = Xs[cut:], ys[cut:]

    _is_rank = str(objective).lower().startswith("rank")

    ens = ml.EnsembleModel(model_dir=out_dir)
    _tc.apply_hyperparams(ens, lr, depth, n_estimators, seed)
    if not getattr(ens, "models", None):
        raise RuntimeError("모델이 없다(ens.skip 설정 확인)")
    if _is_rank and len(ens.models) != 1:
        raise RuntimeError(
            f"랭킹 arm 은 단일 모델이어야 한다 — 현재 {len(ens.models)}개(ens.skip 미설정?)")
    xm = ens.models[0]



    _p = getattr(xm, "params", None)
    if isinstance(_p, dict):
        _p["scale_pos_weight"] = 1.0
    xm.train(Xc_t, yc_t, Xc_v, yc_v,
             group=(gc_t if _is_rank else None),
             val_group=(gc_v if _is_rank else None),
             objective=objective)
    ens.val_weights = {n: 1.0 for n in ens.model_names}
    test_probs = np.asarray(ens.predict(Xte), dtype=float)
    ens_auc = ml._safe_auc(y_test, test_probs)
    model_aucs = {}
    for name, model in zip(ens.model_names, ens.models):
        try:
            model_aucs[name] = ml._safe_auc(y_test, model.predict(Xte))
        except Exception:
            model_aucs[name] = 0.5
    return ens_auc, model_aucs, curated, ens


def _add_miss_ind(df, base_names, spec, srcs, log=None):
    """결측 지시자 파생(CG78): `na_<src>` = 1.0 if 원본이 NaN else 0.0.

    왜 이 축인가: 학습 직전 `np.nan_to_num(nan=0.0)`(이 파일 L1377/1381)이 결측을 **0 으로
    대체**한다 → 트리는 '값이 0'과 '값이 없음'을 구분할 수 없다. 결측 자체가 정보일 수 있다
    (신규 상장·공시 지연·수급 미집계 기간). 지시자를 별도 열로 남겨 선별이 후보로 볼 수 있게 한다.

    누수 방지:
      · 소스를 지정하지 않으면 **사전 등록 규칙**으로 고른다(패널 전체 결측률 1~90% 중 내림차순
        top_k). 선별/스윙 결과를 보고 소스를 고르면 선택 누수다.
      · 지시자는 원본과 같은 시점의 결측 여부만 쓴다(미래 참조 없음).
      · 값은 위치 기반으로 계산한다(중복 라벨 pandas 정렬 사고 방지).
    반환: (지시자 열이 추가된 새 프레임, 확장된 이름 목록).
    """
    mat = df[base_names].values.astype(float)
    if mat.shape[1] != len(base_names):
        raise RuntimeError(
            f"열 수 불일치({mat.shape[1]} vs {len(base_names)}) — 패널 중복 라벨로 매핑이 깨졌다")
    pos = {n: i for i, n in enumerate(base_names)}
    if not srcs:
        k = int(spec.get("top_k") or 10)
        frac = np.isnan(mat).mean(axis=0)
        order = np.argsort(-frac)
        picked = [base_names[j] for j in order if 0.01 <= frac[j] <= 0.995][:k]
        if not picked:
            raise RuntimeError(
                "결측 지시자: 사전규칙(결측률 1~99.5%)에 해당하는 피처가 없다 — 축 측정 불가")
        srcs = picked
        if log:
            log("  결측 지시자 소스(사전규칙 top%d): %s"
                % (k, ", ".join(f"{s}({frac[pos[s]]:.3f})" for s in srcs)))
    out = df.copy()
    for s in srcs:
        name = f"na_{s}"
        if name in out.columns:
            raise RuntimeError(f"결측 지시자 이름 충돌: {name}")
        out[name] = np.isnan(mat[:, pos[s]]).astype(float)
    if log:
        log(f"  결측 지시자 {len(srcs)}개 = na_{{src}} ({srcs})")
    return out, list(base_names) + [f"na_{s}" for s in srcs]


def add_derived(df, base_names, spec, log=None):
    """CG25: 사전 등록된 소스 컬럼의 **시간 차분(Δk)** 을 파생 피처로 추가한다.

    왜 이 축인가: 33사이클 동안 시험한 축은 라벨·변환·HP·유니버스·앙상블·표본가중이고,
    **피처의 시간 변화율(모멘텀/변동성의 기울기)** 은 한 번도 만들지 않았다. 패널 스크린 실측
    (2026-09-29, panel_150u 210피처)에서 시간가변 최고군이 변동성·거래량비 계열이고
    |IC| t 가 대부분 3 미만이었다 → **수준(level)보다 변화율이 더 예측적일 수 있다**는 가설.

    누수 방지:
      · 소스는 이름으로 **사전 등록**한다(스크린 결과에서 소스를 고르면 선택 누수가 생긴다).
      · 값은 종목별 **과거 k행**만 쓴다(shift(+k), 미래 참조 없음).
      · 라벨 결측(NaN) 행을 지우기 **전에** 계산한다 — 라벨 NaN 은 중간 분위에도 생기므로
        필터 후에 shift 하면 Δ1 이 실제로는 2일 차이가 된다(조용한 오정의).
    반환: (파생 컬럼이 추가된 프레임, 확장된 이름 목록). 반환 프레임은 새 객체다.
    """
    srcs = [s for s in (spec.get("sources") or []) if s in base_names]
    missing = [s for s in (spec.get("sources") or []) if s not in base_names]
    if missing and log:
        log(f"  파생: 소스 누락 {missing} (패널에 없음)")
    _kind = str(spec.get("kind") or "delta")
    if _kind == "miss":
        return _add_miss_ind(df, base_names, spec, srcs, log=log)
    if _kind != "delta":
        raise RuntimeError(f"derived.kind 알 수 없음: {_kind!r} (delta | miss)")
    if not srcs:
        raise RuntimeError("derived.sources 가 패널 컬럼과 하나도 일치하지 않는다")
    lags = [int(k) for k in (spec.get("lags") or [1, 5])]
    if not lags:
        raise RuntimeError("derived.lags 가 비었다")

    mat = df[base_names].values.astype(float)
    if mat.shape[1] != len(base_names):
        raise RuntimeError(
            f"열 수 불일치({mat.shape[1]} vs {len(base_names)}) — 패널 중복 라벨로 매핑이 깨졌다")
    pos = {n: i for i, n in enumerate(base_names)}
    src_idx = [pos[s] for s in srcs]
    grp = df["stock_code"].astype(str)
    base_mat = df[base_names]
    out = df.copy()
    n_new = 0
    for k in lags:
        prev = base_mat.groupby(grp, sort=False).shift(k).values.astype(float)
        diff = mat[:, src_idx] - prev[:, src_idx]
        for j, s in enumerate(srcs):
            name = f"d{k}_{s}"
            if name in out.columns:
                raise RuntimeError(f"파생 이름이 기존 컬럼과 충돌: {name}")
            out[name] = diff[:, j]
            n_new += 1
    if log:
        log(f"  파생 피처 {n_new}개 = Δ{lags} × {len(srcs)}소스 {srcs}")
    return out, list(base_names) + [f"d{k}_{s}" for k in lags for s in srcs]


def main():
    ap = argparse.ArgumentParser(description="라벨 설계 스윕 (확장창 walk-forward)")
    ap.add_argument("--panel", default="/app/app/models/wf/panel_420.npz")
    ap.add_argument("--days", type=int, default=420)

    ap.add_argument("--end-date", default=None,
                    help="패널 빌드 구간 끝 날짜 YYYY-MM-DD (기본: 실행 시각)")





    ap.add_argument("--asof-date", default=None,
                    help="평가 앵커 YYYY-MM-DD — 이 날짜 이후 행 제외 + 폴드 격자를 앵커에서 역산"
                         "(앵커 잡음 측정용. 미지정 = 현행 왼쪽 정렬 격자와 비트 동일)")
    ap.add_argument("--limit", type=int, default=50)


    ap.add_argument("--market", default=None,
                    help="'KOSPI' | 'KOSDAQ' | 'all'(둘 다) | 미지정(현행 기본 KOSDAQ)")
    ap.add_argument("--since", default=None, help="유니버스 기준 시작일(YYYY-MM-DD)")
    ap.add_argument("--min-days", type=int, default=None, help="최소 거래일수")
    ap.add_argument("--min-value", type=float, default=None, help="일평균 거래대금 하한(원)")
    ap.add_argument("--order", default=None, choices=["code", "value"],
                    help="code(현행 알파벳순) | value(거래대금 상위)")



    ap.add_argument("--universe", default=None, choices=["curated", "prod"],
                    help="curated(현행 기본) | prod(프로덕션 규칙 — --market/--since/--order 무시)")
    ap.add_argument("--universe-seed", type=int, default=0,
                    help="prod 유니버스 셔플 시드(프로덕션과 동일하게 0)")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--wait-for-panel", type=int, default=0,
                    help="패널 캐시가 없으면 최대 N분 대기(다른 러너가 빌드 중일 때)")
    ap.add_argument("--only", default=None,
                    help="쉼표 구분 실험 id 만 실행 (예: LS_quant_q30_h5,LS_rel_h3)")
    ap.add_argument("--out", default="/app/reports/overnight/wf_label_sweep.jsonl",
                    help="원장(JSONL) 경로 — 스모크/검증 실행은 반드시 다른 파일로 분리하라")
    ap.add_argument("--summary-out", default="/app/reports/overnight/wf_label_sweep_summary.json",
                    help="요약 JSON 경로 — ⚠ 기본값을 덮으면 진행 중 사이클의 판정 요약이 오염된다")
    ap.add_argument("--dry-run", action="store_true")





    ap.add_argument("--dump-preds", default=None,
                    help="폴드 앙상블 확률 jsonl 저장 경로 (예: /app/scripts/_preds_CG21.jsonl)")







    ap.add_argument("--dump-all-codes", action="store_true",
                    help="--dump-preds 와 함께: 전 유니버스(라벨 결측 포함) 예측도 덤프(돈 지표 정합)")




    ap.add_argument("--report-train-auc", action="store_true",
                    help="폴드별 학습셋(in-sample) AUC 를 함께 기록(요약 folds[].train_auc)")





    ap.add_argument("--force-features", default=None,
                    help="선별 후 강제로 덧붙일 피처 이름 목록(쉼표 구분). 후보 풀에 없으면 측정 전 실패")






    ap.add_argument("--force-paired", action="store_true",
                    help="--force-features 와 함께: 각 config 를 <id>(대조군) + <id>__ff(강제) 로 펼친다")
    args = ap.parse_args()
    _ff_names = [x.strip() for x in (args.force_features or "").split(",") if x.strip()]
    _ff_set = set(_ff_names)

    if args.dry_run:
        for c in CONFIGS:
            print(c["id"], "|", c["desc"])
        return 0

    waited = 0
    while args.wait_for_panel and not os.path.exists(args.panel):
        if waited >= args.wait_for_panel * 60:
            print(f"패널 캐시 대기 초과({args.wait_for_panel}분): {args.panel}", flush=True)
            return 1
        time.sleep(30)
        waited += 30
        if waited % 300 == 0:
            print(f"  패널 대기 중... {waited // 60}분", flush=True)

    ml.log(f"label_sweep start KST={ml.now_kst().isoformat(timespec='seconds')} "
           f"panel={args.panel} folds={args.folds} seeds={args.seeds}")

    uni = {}
    if args.market is not None:
        uni["market"] = None if args.market == "all" else args.market
    if args.since is not None:
        uni["since"] = args.since
    if args.min_days is not None:
        uni["min_days"] = args.min_days
    if args.min_value is not None:
        uni["min_value"] = args.min_value
    if args.order is not None:
        uni["order"] = args.order
    if uni:
        ml.log(f"universe 옵션: {uni}")
    if args.universe:
        ml.log(f"universe 경로: {args.universe} (seed={args.universe_seed})")
    df, names = W.build_panel(args.panel, args.limit, args.days, log=ml.log,
                              end_date=args.end_date, universe=args.universe,
                              universe_seed=args.universe_seed, **uni)
    base_names = [n for n in names if n in df.columns]
    all_dates = sorted(df["date"].astype(str).unique())
    n_codes_eff = int(df["stock_code"].nunique()) if len(df) else 0
    ml.log(f"panel rows={len(df)} dates={len(all_dates)} ({all_dates[0]} ~ {all_dates[-1]}) "
           f"codes={n_codes_eff}")
    if args.limit and n_codes_eff != args.limit:
        ml.log(f"⚠ --limit {args.limit} 무시됨 — 완성 패널(npz) 재사용 경로는 종목을 자르지 않는다"
               f"(실제 {n_codes_eff}종목). 폭 실험은 codes_limit/codes_slice 또는 다른 패널 파일을 써라. "
               f"(실측 2026-11-25: 같은 패널에서 --limit 50/100/200 이 비트동일 0.5475 로 나왔다)")

    import train_curated as tc
    tc.select_curated_features = lambda n, a=False: list(n)









    _orig_apply = tc.apply_hyperparams
    _extra: dict = {}

    def _apply_with_extra(ensemble, lr, depth, n_estimators, seed):
        _orig_apply(ensemble, lr, depth, n_estimators, seed)
        if not _extra:
            return
        for _m in getattr(ensemble, "models", []):
            _p = getattr(_m, "params", None)
            if _p is None:
                continue
            for _k, _v in _extra.items():
                if _k in _p:
                    _p[_k] = _v

    tc.apply_hyperparams = _apply_with_extra








    _OrigEns = getattr(ml, "EnsembleModel", None)
    _ens_cfg: dict = {"skip": set(), "equal_weights": False}

    if _OrigEns is not None:
        class _EnsembledForSweep(_OrigEns):
            def __init__(self, model_dir="models"):
                super().__init__(model_dir)
                _skip = set(_ens_cfg.get("skip") or ())
                if _skip:
                    keep = [i for i, n in enumerate(self.model_names) if n not in _skip]
                    self.models = [self.models[i] for i in keep]
                    self.model_names = [self.model_names[i] for i in keep]

            def train(self, X_train, y_train, X_val=None, y_val=None, feature_names=None,
                      sample_weight=None):

                _m = super().train(X_train, y_train, X_val, y_val,
                                   feature_names=feature_names, sample_weight=sample_weight)
                if _ens_cfg.get("equal_weights"):
                    self.val_weights = {n: 1.0 for n in self.model_names}
                return _m

        ml.EnsembleModel = _EnsembledForSweep








    class _LinearModel:
        def __init__(self, model_dir="models"):
            self.model_dir = model_dir
            self.model_names = ["logit"]
            self.models = []
            self.val_weights = {}

        def train(self, X_train, y_train, X_val=None, y_val=None, feature_names=None,
                  sample_weight=None):
            from sklearn.linear_model import LogisticRegression
            from sklearn.pipeline import make_pipeline
            from sklearn.preprocessing import StandardScaler
            C = float(_ens_cfg.get("linear_C") or 0.1)
            clf = make_pipeline(StandardScaler(),
                                LogisticRegression(C=C, max_iter=3000, solver="lbfgs"))
            kw = {}
            if sample_weight is not None:
                kw["logisticregression__sample_weight"] = np.asarray(sample_weight, dtype=float)
            clf.fit(X_train, y_train, **kw)
            self.models = [clf]
            try:
                _cv = np.asarray(clf.predict_proba(X_train)[:, 1], dtype=float)
                _coef = np.abs(np.asarray(clf[-1].coef_, dtype=float)).sum()
                ml.log(f"    [linear] 학습행 {X_train.shape[0]}x{X_train.shape[1]} · |coef|합 {_coef:.4f} · "
                       f"train p std {_cv.std():.5f} (min {_cv.min():.4f} max {_cv.max():.4f})")
            except Exception as _e:
                ml.log(f"    [linear] 진단 실패 {type(_e).__name__}: {_e}")
            return self

        def predict(self, X):



            return np.asarray(self.models[0].predict_proba(X), dtype=float)[:, 1]

    _EnsGBDT = ml.EnsembleModel

    out_path = args.out

    for _p in (out_path, args.summary_out):
        _d = os.path.dirname(_p)
        if _d:
            os.makedirs(_d, exist_ok=True)
    results = []
    dump_recs: list = []

    cfgs = CONFIGS
    if args.only:
        want = {s.strip() for s in args.only.split(",") if s.strip()}
        cfgs = [c for c in CONFIGS if c["id"] in want]
        if not cfgs:
            print(f"--only 에 해당하는 실험 없음: {sorted(want)}", flush=True)
            return 1

    if args.force_paired:
        if not _ff_names:
            print("--force-paired 는 --force-features 와 함께 써야 한다(강제 목록 비어 있음)", flush=True)
            return 1
        _pairs = []
        for c in cfgs:
            ctl = dict(c, _force=[])
            arm = dict(c, id=f"{c['id']}__ff", _force=list(_ff_names),
                       desc=f"{c.get('desc', '')} + 강제 {len(_ff_names)}개")
            _pairs += [ctl, arm]
        cfgs = _pairs
    ml.log(f"실행 실험 {len(cfgs)}개: {[c['id'] for c in cfgs]}")


    _need_fwd = any(str((c.get("weight") or {}).get("kind") or "") == "absret" for c in cfgs)

    for cfg in cfgs:
        exp_id = cfg["id"]


        _ffn = list(cfg.get("_force", _ff_names))
        _ffs = set(_ffn)
        rec = {"exp": exp_id, "desc": cfg["desc"], "kind": cfg["kind"],
               "horizon": cfg["horizon"], "q": cfg["q"], "select": cfg["select"],
               "transform": cfg.get("transform"),
               "pool": cfg.get("pool"),
               "exclude_market_level": bool(cfg.get("exclude_market_level")),
               "weight": cfg.get("weight"),
               "train_mask_daychg_gte": cfg.get("train_mask_daychg_gte"),
               "train_mask_col": cfg.get("train_mask_col"),
               "train_mask_gte": cfg.get("train_mask_gte"),
               "label_mask": cfg.get("label_mask"),
               "ts": ml.now_iso(), "status": "failed", "folds": {}}
        ml.log(f"=== {exp_id}: {cfg['desc']} ===")
        try:
            _lm, _lmd = build_label_mask(df, cfg.get("label_mask"))
            if _lm is not None:
                rec["label_mask_diag"] = _lmd
                ml.log(f"  {exp_id}: 라벨 기준모집단 마스크 {_lmd} → 유지 "
                       f"{_lmd['keep']}/{_lmd['rows']} ({_lmd['keep_frac']:.1%}), 행은 버리지 않음")
            y = W.make_labels(df, cfg["kind"], cfg["horizon"], cfg["q"],
                              thresh=cfg.get("thresh"), label_mask=_lm,
                              entry=cfg.get("entry"))
            d = df.copy()



            bn = base_names
            dv_names: list = []
            if cfg.get("derived"):
                d, bn = add_derived(d, base_names, cfg["derived"], log=ml.log)

                dv_names = [f for f in bn if f not in set(base_names)]






            ga_names: list = []
            if cfg.get("gate_add"):
                if not cfg.get("core_only"):
                    raise RuntimeError("gate_add 는 게이트가 켜진 config(core_only)에서만 의미가 있다")
                _want = [str(x) for x in cfg["gate_add"]]
                _present = {str(x) for x in base_names}
                _missing = [x for x in _want if x not in _present]
                if _missing:
                    raise RuntimeError(f"gate_add 이름이 패널에 없다: {_missing}")
                ga_names = _want
                dv_names = list(dv_names) + _want
            d["_y"] = y




            if args.dump_preds or _need_fwd:
                try:
                    d["_fwd"] = df.groupby("stock_code", sort=False)["price"].transform(
                        lambda s: s.shift(-int(cfg["horizon"])) / s - 1.0).values
                except Exception as e:
                    ml.log(f"  {exp_id}: 선행수익 계산 실패({type(e).__name__}: {e}) — fwd_ret 결측")
                    d["_fwd"] = np.nan




            try:
                d["_ref"] = df.groupby("stock_code", sort=False)["date"].shift(-cfg["horizon"])
            except Exception as e:
                ml.log(f"  {exp_id}: 라벨 참조일 계산 실패({type(e).__name__}: {e}) — 달력 purge 유지")
                d["_ref"] = None



            d_all = d if (args.dump_preds and getattr(args, "dump_all_codes", False)) else None
            d = d[~pd.isna(d["_y"])]




            if args.asof_date:
                _n_before_asof = len(d)
                d = d[d["date"].astype(str) <= str(args.asof_date)]
                ml.log(f"  {exp_id}: 앵커 {args.asof_date} — 이후 행 제외 "
                       f"{_n_before_asof} → {len(d)}")
                if len(d) < 200:
                    raise RuntimeError(f"앵커 절단 후 행이 {len(d)} — 측정 불가")





            _cfp = cfg.get("codes_from_panel")
            _clim = int(cfg.get("codes_limit") or 0)
            _cslic = cfg.get("codes_slice")
            if _cfp or _clim or _cslic:
                if _cfp:
                    _zp = np.load(_cfp, allow_pickle=True)
                    keep, _src = {str(c) for c in _zp["codes"]}, os.path.basename(str(_cfp))
                else:





                    _seen: list = []
                    for _c in d["stock_code"].astype(str):
                        if _c not in _seen:
                            _seen.append(_c)
                    if _clim:
                        keep, _src = set(_seen[:_clim]), f"패널 순서 첫 {_clim}종목"
                    else:
                        _a, _b = int(_cslic[0]), int(_cslic[1])
                        keep = set(_seen[_a:_b])
                        _src = f"패널 순서 [{_a}:{_b}) = {len(keep)}종목"
                before = len(d)
                d = d[d["stock_code"].astype(str).isin(keep)]
                ml.log(f"  {exp_id}: 종목 필터 {before} → {len(d)} 행 ({len(keep)}종목: {_src})")
                if len(d) < 200:
                    raise RuntimeError(f"codes_from_panel 필터 후 행이 {len(d)} — 측정 불가")
            dd = sorted(d["date"].astype(str).unique())
            n = len(dd)
            step = n // (args.folds + 1)
            fold_means, fold_sizes = [], []
            n_eff_all = set()




            n_folds = int(cfg.get("folds") or args.folds)
            if n_folds != args.folds:
                step = n // (n_folds + 1)



            _win = fold_window_indices(n, n_folds, step, right_anchored=bool(args.asof_date))
            ml.log(f"  {exp_id}: 폴드 격자 {'앵커(오른쪽) 역산' if args.asof_date else '왼쪽 정렬'} "
                   f"n_dates={n} folds={n_folds} step={step} "
                   f"windows={[(dd[a[1]], dd[a[2] - 1]) for a in _win]}")
            for i, (cut_idx, lo, hi) in enumerate(_win, start=1):
                cut = dd[cut_idx]
                nxt = dd[hi - 1]
                h = cfg["horizon"]
                purge = set(dd[max(0, cut_idx + 1 - h):cut_idx + 1])
                tr = d[(d["date"] <= cut) & (~d["date"].isin(purge))]
                n_ref_purged = 0
                if "_ref" in tr.columns:
                    _bad = np.greater_equal(np.asarray(tr["_ref"].astype(str).values),
                                            np.asarray(dd[lo]))
                    n_ref_purged = int(_bad.sum())
                    if n_ref_purged:
                        tr = tr[np.logical_not(_bad)]
                te = d[(d["date"] > cut) & (d["date"] <= nxt)]






                _mg = cfg.get("train_mask_daychg_gte")
                _n_before_mask = len(tr)
                tr, n_masked = apply_train_domain_mask(tr, _mg)
                if _mg is not None:
                    ml.log(f"  {exp_id} fold{i}: 학습 도메인 마스크 ≥{float(_mg):+.2f} → "
                           f"{n_masked}행 제외 ({_n_before_mask} → {len(tr)})")
                    if len(tr) < 100:
                        ml.log(f"  {exp_id} fold{i}: 마스크 후 학습행 {len(tr)} — 건너뜀")
                        continue




                _mcol = cfg.get("train_mask_col")
                _mgte = cfg.get("train_mask_gte")
                if _mcol and _mgte is not None:
                    _n0 = len(tr)
                    tr, _nm = apply_train_mask_col(tr, _mcol, _mgte)
                    ml.log(f"  {exp_id} fold{i}: 학습 도메인 마스크 {_mcol} ≥ {float(_mgte):.4f} → "
                           f"{_nm}행 제외 ({_n0} → {len(tr)})")
                if min(len(tr), len(te)) < 100:
                    ml.log(f"  {exp_id} fold{i}: 표본 부족(tr={len(tr)} te={len(te)}), 건너뜀")
                    continue
                trd = tr["date"].astype(str).values
                ted = te["date"].astype(str).values



                w_tr = None
                if cfg.get("weight"):
                    _dpos = {str(x): j for j, x in enumerate(dd)}
                    _wspec = cfg["weight"]
                    _wcol = _wspec.get("col") if str(_wspec.get("kind")).lower() == "fillable" else None
                    if _wcol and _wcol not in tr.columns:
                        raise RuntimeError(
                            f"weight.col={_wcol} 이 학습 프레임에 없다 — 패널에 그 컬럼이 필요하다")
                    w_tr = make_weights(
                        _wspec, trd,
                        fwd_tr=(np.asarray(tr["_fwd"].values, dtype=float)
                                if "_fwd" in tr.columns else None),
                        day_pos=_dpos, t0_pos=_dpos.get(str(cut)),
                        col_vals=(tr[_wcol].to_numpy(dtype=float) if _wcol else None))
                    ml.log(f"  {exp_id} fold{i}: 가중 {cfg['weight']} — "
                           f"min={w_tr.min():.4f} max={w_tr.max():.4f} "
                           f"유니크={len(np.unique(np.round(w_tr, 6)))}")
                tkind = cfg.get("transform")
                Xtr = np.nan_to_num(
                    transform_matrix(tr[bn], trd, tkind, codes=tr["stock_code"].values)
                    .astype(np.float32), nan=0.0)
                ytr = tr["_y"].values.astype(int)
                Xte = np.nan_to_num(
                    transform_matrix(te[bn], ted, tkind, codes=te["stock_code"].values)
                    .astype(np.float32), nan=0.0)
                yte = te["_y"].values.astype(int)


                te_all = ted_all = codes_all = None
                Xte_all = None
                if d_all is not None:
                    te_all = d_all[(d_all["date"] > cut) & (d_all["date"] <= nxt)]
                    if len(te_all):
                        ted_all = te_all["date"].astype(str).values
                        codes_all = (te_all["stock_code"].astype(str).values
                                     if "stock_code" in te_all.columns else [" " * 0] * len(te_all))
                    else:
                        te_all = None




                if Xtr.shape[1] != len(bn) or Xte.shape[1] != len(bn):
                    raise RuntimeError(
                        f"피처 열 수 불일치(Xtr={Xtr.shape[1]}, Xte={Xte.shape[1]}, "
                        f"names={len(bn)}) — 패널 중복 라벨로 이름↔열 매핑이 깨졌다")
                cols = np.std(Xtr, axis=0) > 0




                pool = cfg.get("pool")
                if pool in ("timevary", "const"):
                    nun = tr[bn].groupby(tr["stock_code"].values).nunique()
                    is_const = (nun.max(axis=0).values <= 1)
                    pool_mask = is_const if pool == "const" else ~is_const
                    cols = cols & pool_mask



                n_mkt_excluded = 0
                mkt_excluded_names = []
                if cfg.get("exclude_market_level"):







                    try:
                        _mat = tr[bn].values.astype(float)
                        _tmp = pd.DataFrame(
                            _mat, columns=[f"_f{j}" for j in range(_mat.shape[1])])
                        _tmp["_d"] = trd
                        _g = _tmp.groupby("_d")
                        _nun = _g.nunique(dropna=True)
                        _cnt = _g.count()
                        _nun = _nun.drop(columns=["_d"], errors="ignore").values
                        _cnt = _cnt.drop(columns=["_d"], errors="ignore").values
                        _judged = _cnt >= 2
                        _den = _judged.sum(axis=0)
                        _rate = np.divide(
                            np.logical_and(_nun <= 1, _judged).sum(axis=0).astype(float),
                            _den.astype(float),
                            out=np.zeros(_cnt.shape[1], dtype=float),
                            where=_den > 0)
                        is_mkt = _rate >= 0.9
                    except Exception as e:
                        ml.log(f"  {exp_id} fold{i}: 시장레벨 판정 실패({type(e).__name__}: {e}) — 제외 없음")
                        is_mkt = np.zeros(len(bn), dtype=bool)
                    n_mkt_excluded = int((cols & is_mkt).sum())
                    mkt_excluded_names = [f for f, m in zip(bn, cols & is_mkt) if m]
                    cols = cols & ~is_mkt



                if cfg.get("core_only"):
                    core_set = {str(f) for f in getattr(W.tc, "CORE_FEATURES", []) or []}
                    if not core_set:
                        raise RuntimeError("core_only 요청인데 CORE_FEATURES 를 읽지 못했다")
                    _pre_core = cols.copy()
                    cols = cols & np.array([f in core_set for f in bn], dtype=bool)
                    if dv_names:




                        _dset0 = set(dv_names)
                        cols = cols | (_pre_core & np.array([f in _dset0 for f in bn], dtype=bool))
                        if ga_names:
                            _gm = np.array([f in set(ga_names) for f in bn], dtype=bool)
                            _kept = [f for f, m in zip(bn, cols & _gm) if m]
                            ml.log(f"  {exp_id}: gate_add 통과 {len(_kept)}/{len(ga_names)}개 {_kept}")
                            if len(_kept) < len(ga_names):
                                ml.log(f"  {exp_id}: ⚠ gate_add 누락 "
                                       f"{sorted(set(ga_names) - set(_kept))} — 사전필터(std·pool·시장레벨) 탈락")






                xnames = [str(x) for x in (cfg.get("exclude_names") or [])]
                if xnames:
                    hit = np.array(
                        [any(n == b or (n.endswith("*") and b.startswith(n[:-1]))
                             for n in xnames) for b in bn], dtype=bool)
                    n_ex = int((cols & hit).sum())
                    ex_kept = [f for f, m in zip(bn, cols & hit) if m]
                    cols = cols & np.logical_not(hit)
                    ml.log(f"  {exp_id}: 피처군 제외 {n_ex}개 {ex_kept[:6]}")
                    if n_ex == 0:
                        raise RuntimeError(
                            f"exclude_names={xnames} 가 아무 컬럼도 제외하지 않았다 — "
                            "이름 표기가 틀렸다(측정 전에 실패시킨다)")







                if cfg.get("drop_dup"):
                    _dupm = np.array([bool(re.search(r"__dup\d+$", f)) for f in bn], dtype=bool)
                    n_dup = int((cols & _dupm).sum())
                    cols = cols & np.logical_not(_dupm)
                    ml.log(f"  {exp_id}: 중복 라벨 제거 {n_dup}개 (후보 {int(cols.sum())})")





                _ff_info = []
                if _ffn:
                    _fmiss = sorted(_ffs - set(bn))
                    if _fmiss:
                        raise RuntimeError(
                            f"force_features={_fmiss} 가 패널 피처 이름에 없다 — 표기 오류")
                    for j, f in enumerate(bn):
                        if f in _ffs and not cols[j]:
                            cols[j] = True
                    _mat_tr = tr[bn].values
                    for j, f in enumerate(bn):
                        if f in _ffs:
                            _nz = float((_mat_tr[:, j] != 0).mean())
                            _ff_info.append(f"{f}(비영 {_nz * 100:.1f}%)")
                    ml.log(f"  {exp_id} fold{i}: 강제 피처 학습창 " + " · ".join(_ff_info))
                fn = [f for f, m in zip(bn, cols) if m]
                Xtr, Xte = Xtr[:, cols], Xte[:, cols]





                if dv_names:
                    _dset = set(dv_names)
                    _keep = [j for j, f in enumerate(fn) if f not in _dset]
                    _idx_k, sel_desc = W.subset([fn[j] for j in _keep], cfg["select"],
                                                Xtr[:, _keep], ytr, trd)
                    _der = [j for j, f in enumerate(fn) if f in _dset]
                    if not _der:
                        raise RuntimeError("파생 피처가 필터를 통과하지 못했다 — 측정 무효")
                    idx = [_keep[j] for j in _idx_k] + _der
                    sel_desc = f"{sel_desc} + Δ파생 {len(_der)}개(강제)"
                    ml.log(f"  {exp_id}: 선별 {len(idx)}개 = edge top{len(_idx_k)} + Δ강제 {len(_der)}개"
                           f"{' (gate_add 포함)' if ga_names else ''}")
                else:
                    idx, sel_desc = W.subset(fn, cfg["select"], Xtr, ytr, trd)




                if _ffn:
                    _have = {fn[j] for j in idx}
                    _add = [j for j, f in enumerate(fn) if f in _ffs and f not in _have]
                    _in_pool = sorted(_ffs & set(fn))
                    _base_n = len(idx)
                    if _add:
                        idx = list(idx) + _add
                        sel_desc = f"{sel_desc} + 강제 {len(_add)}개"
                    ml.log(f"  {exp_id} fold{i}: 강제 피처 후보 {len(_in_pool)}/{len(_ffs)} · "
                           f"선별 기진입 {len(_have & _ffs)} · append {len(_add)} "
                           f"→ n_features {len(idx)} (대조군 {_base_n})")
                sel = [fn[j] for j in idx]





                if ga_names:
                    _ga_sel = sorted(set(ga_names) & set(sel))
                    ml.log(f"  {exp_id}: gate_add 선별 진입 {len(_ga_sel)}/{len(ga_names)}개 {_ga_sel}")



                _ndup_sel = sum(1 for f in sel if re.search(r"__dup\d+$", f))



                ml.log(f"  {exp_id}: 선별 {len(idx)}개 (중복열 {_ndup_sel}개·실질 고유 "
                       f"{len(idx) - _ndup_sel}개) {sel_desc}")
                recipe = cfg.get("recipe") or W.BASE["recipe"]
                _extra.clear()
                _extra.update(cfg.get("recipe_extra") or {})
                _ens_cfg["skip"] = set((cfg.get("ens") or {}).get("skip") or ())
                _ens_cfg["equal_weights"] = bool((cfg.get("ens") or {}).get("equal_weights"))
                _ens_cfg["linear"] = bool(cfg.get("linear"))
                _ens_cfg["linear_C"] = cfg.get("linear_C")

                ml.EnsembleModel = _LinearModel if cfg.get("linear") else _EnsGBDT
                aucs = []
                probs = []
                probs_all = []
                if te_all is not None:
                    try:
                        Xte_all = np.nan_to_num(
                            transform_matrix(te_all[bn], ted_all, tkind, codes=codes_all)
                            .astype(np.float32), nan=0.0)
                    except Exception as e:
                        ml.log(f"  {exp_id} fold{i}: 전 유니버스 행렬 실패({type(e).__name__}: {e})")
                        Xte_all, te_all = None, None
                tr_probs = []
                n_eff_seen = set()
                for seed in range(int(cfg.get("seed_offset") or 0),
                                  int(cfg.get("seed_offset") or 0) + args.seeds):
                    _obj = cfg.get("objective")
                    if _obj:

                        a, _m, _c, _e = train_seed_rank(
                            Xtr[:, idx], Xte[:, idx], ytr, yte, sel,
                            f"/app/app/models/wf/labelsweep_{exp_id}", seed,
                            recipe["lr"], recipe["depth"],
                            recipe["n_estimators"], trd, _obj)
                    else:
                        a, _m, _c, _e = train_seed_weighted(
                            Xtr[:, idx], None, Xte[:, idx], ytr, None, yte, sel,
                            f"/app/app/models/wf/labelsweep_{exp_id}", seed,
                            recipe["lr"], recipe["depth"],
                            recipe["n_estimators"], True, None, w_tr)
                    aucs.append(float(a))


                    n_eff_seen.add(len(_c) if _c is not None else -1)
                    n_eff_all |= n_eff_seen
                    try:
                        _p = np.asarray(_e.predict(Xte[:, idx]), dtype=float)
                        probs.append(_p[:, -1] if _p.ndim > 1 else _p)
                    except Exception:
                        pass

                    if te_all is not None:
                        try:
                            _pa = np.asarray(_e.predict(Xte_all[:, idx]), dtype=float)
                            probs_all.append(_pa[:, -1] if _pa.ndim > 1 else _pa)
                        except Exception:
                            pass


                    if getattr(args, "report_train_auc", False):
                        try:
                            _q = np.asarray(_e.predict(Xtr[:, idx]), dtype=float)
                            tr_probs.append(_q[:, -1] if _q.ndim > 1 else _q)
                        except Exception:
                            pass




                ens_pooled_auc = None
                daily_auc_mean = None
                n_dates_scored = 0
                train_auc = None
                if probs:
                    from sklearn.metrics import roc_auc_score
                    ens_p = np.mean(np.vstack(probs), axis=0)
                    try:
                        ens_pooled_auc = float(roc_auc_score(yte, ens_p))
                    except Exception:
                        pass
                    try:
                        _df = pd.DataFrame({"d": np.asarray(ted), "y": yte, "p": ens_p})
                        _da = [float(roc_auc_score(g["y"].values, g["p"].values))
                               for _d, g in _df.groupby("d") if g["y"].nunique() > 1]
                        if _da:
                            daily_auc_mean = float(np.mean(_da))
                            n_dates_scored = len(_da)
                    except Exception as e:
                        ml.log(f"  {exp_id} fold{i}: 날짜별 AUC 계산 실패({type(e).__name__}: {e})")


                    if tr_probs:
                        try:
                            _tp = np.mean(np.vstack(tr_probs), axis=0)
                            train_auc = float(roc_auc_score(ytr, _tp))
                            ml.log(f"  {exp_id} fold{i}: train AUC {train_auc:.4f} "
                                   f"(in-sample) vs OOS {ens_pooled_auc}")
                        except Exception as e:
                            ml.log(f"  {exp_id} fold{i}: 학습셋 AUC 계산 실패({type(e).__name__}: {e})")

                    if args.dump_preds:
                        _codes = (te["stock_code"].astype(str).values
                                  if "stock_code" in te.columns else [""] * len(ted))
                        _fwd = (np.asarray(te["_fwd"].values, dtype=float)
                                if "_fwd" in te.columns else np.full(len(ted), np.nan))
                        for _j in range(len(ted)):
                            dump_recs.append(
                                {"exp": exp_id, "fold": int(i), "date": str(ted[_j]),
                                 "code": str(_codes[_j]), "y_true": int(yte[_j]),
                                 "y_pred": float(ens_p[_j]), "fwd_ret": float(_fwd[_j])})


                        if te_all is not None and probs_all:
                            _ens_pa = np.mean(np.vstack(probs_all), axis=0)
                            _fwd_a = (np.asarray(te_all["_fwd"].values, dtype=float)
                                      if "_fwd" in te_all.columns
                                      else np.full(len(ted_all), np.nan))




                            _seen_all = {(str(ted[_j]), str(_codes[_j])) for _j in range(len(ted))}
                            _n_skip_dup = 0
                            for _j in range(len(ted_all)):
                                _key = (str(ted_all[_j]), str(codes_all[_j]))
                                if _key in _seen_all:
                                    _n_skip_dup += 1
                                    continue
                                _seen_all.add(_key)
                                dump_recs.append(
                                    {"exp": exp_id, "fold": int(i), "date": str(ted_all[_j]),
                                     "code": str(codes_all[_j]), "y_true": None,
                                     "y_pred": float(_ens_pa[_j]), "fwd_ret": float(_fwd_a[_j])})
                            ml.log(f"  {exp_id} fold{i}: 전 유니버스 덤프 {len(ted_all)}행 중 "
                                   f"{_n_skip_dup}행 중복/라벨중복 제외")
                fold_means.append(float(np.mean(aucs)))
                fold_sizes.append((len(tr), len(te)))
                rec["folds"][f"fold{i}"] = {"train_rows": len(tr), "test_rows": len(te),
                                            "test_from": dd[lo], "test_to": nxt,
                                            "anchor": args.asof_date,
                                            "mean": float(np.mean(aucs)),
                                            "n_features": len(sel),
                                            "n_effective_features": sorted(n_eff_seen),
                                            "sel_desc": sel_desc,
                                            "n_market_level_excluded": n_mkt_excluded,
                            "n_label_ref_purged": n_ref_purged,
                                            "market_level_excluded_names": mkt_excluded_names,
                                            "weight_stats": (None if w_tr is None else {
                                                "min": float(w_tr.min()), "max": float(w_tr.max()),
                                                "mean": float(w_tr.mean()),
                                                "n_unique": int(len(np.unique(np.round(w_tr, 6))))}),
                                            "ens_pooled_auc": ens_pooled_auc,
                                            "daily_auc_mean": daily_auc_mean,
                                            "train_auc": train_auc,
                                            "n_test_dates_scored": n_dates_scored,
                                            "selected_features": sel}
            if fold_means:
                rec["status"] = "ok"
                rec["auc_mean"] = float(np.mean(fold_means))
                rec["auc_std"] = float(np.std(fold_means))
                rec["n_rows_used"] = int(len(d))
                rec["n_effective_features"] = sorted(n_eff_all)
                _dm = [v["daily_auc_mean"] for v in rec["folds"].values()
                       if v.get("daily_auc_mean") is not None]
                rec["daily_auc_mean"] = float(np.mean(_dm)) if _dm else None
                ml.log(f"  → {exp_id} AUC mean={rec['auc_mean']:.4f} "
                       f"std={rec['auc_std']:.4f} folds={len(fold_means)} rows={len(d)} "
                       f"| 실효피처={sorted(n_eff_all) if n_eff_all else '?'} "
                       f"| pooled AUC 평균={rec['auc_mean']:.4f} · 날짜별 AUC 평균="
                       f"{rec['daily_auc_mean'] if rec['daily_auc_mean'] is None else round(rec['daily_auc_mean'], 4)}")
        except Exception as e:  # noqa: BLE001
            rec["error"] = f"{type(e).__name__}: {e}"
            ml.log(f"  !! {exp_id} 실패: {rec['error']}")
        results.append(rec)
        with open(out_path, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


    if args.dump_preds:
        try:
            _dd = os.path.dirname(args.dump_preds)
            if _dd:
                os.makedirs(_dd, exist_ok=True)
            with open(args.dump_preds, "w") as f:
                for _r in dump_recs:
                    f.write(json.dumps(_r, ensure_ascii=False) + "\n")
            print(f"preds dumped: {args.dump_preds} ({len(dump_recs)} rows)", flush=True)
        except Exception as e:
            print(f"preds dump 실패({type(e).__name__}: {e})", flush=True)

    ok = [r for r in results if r.get("status") == "ok"]
    ok.sort(key=lambda r: -r["auc_mean"])
    print("\n=== 라벨 스윕 결과 (폴드 평균 기준) ===")
    for r in ok:
        print(f"  {r['exp']:18s} AUC {r['auc_mean']:.4f} ± {r['auc_std']:.4f} "
              f"| {r['desc']}")
    if ok:
        with open(args.summary_out, "w") as f:
            json.dump({"finished_at": ml.now_iso(), "config": vars(args),
                       "effective_n_codes": n_codes_eff,
                       "effective_n_dates": len(all_dates),
                       "best": ok[0], "results": results}, f,
                      ensure_ascii=False, indent=2)
        print(f"\nbest: {ok[0]['exp']} ({ok[0]['desc']}) AUC {ok[0]['auc_mean']:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

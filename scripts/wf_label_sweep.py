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
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

import wf_wave as W  # noqa: E402  (패널 빌드·라벨·선별·폴드 로직 재사용)

ml = W.ml

# 라벨 변형만 바꾼다. select/recipe 는 기준 러너와 동일하게 고정해 라벨 효과만 본다.
CONFIGS = [
    {"id": "LS_quant_q30_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "desc": "기준(현 최고 실험과 동일): 분위0.3 h5 top30"},
    {"id": "LS_rel_h5",       "kind": "relative", "horizon": 5, "q": None, "select": "top30",
     "desc": "시장상대 이진(중앙값 초과=1) h5 top30 — 미측정 레버"},
    {"id": "LS_rel_h5_t40",   "kind": "relative", "horizon": 5, "q": None, "select": "top40",
     "desc": "시장상대 이진 h5 top40"},
    {"id": "LS_rel_h3",       "kind": "relative", "horizon": 3, "q": None, "select": "top30",
     "desc": "시장상대 이진 h3 top30"},
    # ── 호라이즌 축 (장외 자율 루프가 추가) ─────────────────────────────────
    # 근거: label_wave5 실측 — h8 + 분위0.3 + top30 이 3분할 평균 0.5727±0.0277
    # (최고 분할 0.6015 / 최저 0.5463). 그러나 그 실험은 150종목·단일분할 프로토콜이라
    # 이 스윕(49종목·5폴드 확장창)과 직접 비교가 불가하다 → 같은 프로토콜로 h8 을 채워
    # "라벨 호라이즌 효과"와 "유니버스 효과"를 분리한다.
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
    # ── 피처 변환 축 (라벨은 최고 설정 고정, 횡단면 정규화만 바꾼다) ──────────────
    {"id": "TR_rank_h5",    "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "transform": "rank",
     "desc": "피처 날짜별 횡단면 rank(pct) — 시장레벨 성분 제거"},
    # ── TR2: 두 양(+) 방향의 결합 (2026-09-26 06:08 실측) ──────────────────────
    # 같은 날 같은 프로토콜에서 ① 횡단면 rank 변환 Δ+0.0107 (0.5519±0.0333 vs 0.5412)
    # ② depth1·lr0.05 Δ+0.0065 (0.5478±0.0527 vs 0.5413, seeds=10) 로 **둘 다 양**이었다.
    # 각각 단독으로는 +0.02 문턱 미달이지만 서로 다른 축이다(전처리 / 모델 용량).
    # rank 는 최악 폴드를 0.5075→0.5207 로 올렸고 depth1 은 폴드 std 를 키우는 쪽이다 —
    # 결합이 가산적이면 문턱에 근접하는지, 아니면 서로 상쇄되는지 측정한다.
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
    # ── 피처 풀 분리 (룩어헤드 검증) ─────────────────────────────────────────
    {"id": "PO_timevary_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "pool": "timevary",
     "desc": "시간가변 피처만 — 종목-상수(최신 스냅샷) 제외"},
    {"id": "PO_const_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "pool": "const",
     "desc": "종목-상수 피처만 — 룩어헤드 의심군 단독 성능"},
    {"id": "PO_timevary_rank_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "transform": "rank", "pool": "timevary",
     "desc": "시간가변 + 횡단면 rank"},
    # ── 시장레벨 제외 (횡단면 계약 #6) ──────────────────────────────────────
    # 근거(실측 2026-09-25, scripts/wf_leak_select_check.py): 현재 기준선 패널
    # (panel_420_asofpatch, 49종목)의 폴드별 top30 안에 **시장레벨 피처가 1~3개** 들어간다
    # (program_trading_ratio 4/5폴드, oil_change_1m/3m·yield_spread·krx_advance_decline_ratio·
    #  derivatives_volume·pbr_current 각 1폴드). 시장레벨 = 같은 날짜에 종목간 값이 하나뿐인 피처
    # (패널 210개 중 27개; 리서처 메트릭 dq_feature_market_level_count=26 과 독립 교차확인).
    # 그런데 라벨은 날짜내 분위(횡단면 상대)이므로, 날짜 상수 피처는 날짜별 점수를 통째로
    # 밀어 pooled AUC 를 부풀릴 수 있다 → 제외하고 같은 프로토콜로 재측정한다.
    {"id": "MK_nomkt_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "exclude_market_level": True,
     "desc": "분위0.3 h5 top30 + 시장레벨(날짜내 종목간 동일값) 피처 제외"},
    {"id": "MK_timevary_nomkt_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "pool": "timevary", "exclude_market_level": True,
     "desc": "시간가변 + 시장레벨 제외 (계약 #3·#6 동시 준수)"},
    # ── curated 게이트 대조 (2026-09-26 실측 — 처음엔 내 해석이 틀렸다) ──────────
    # ⚠ 정정: 이 스크립트는 L223~224 에서 **tc.select_curated_features 를 항등함수로
    # 몽키패치**한다 → 프로덕션 트레이너의 CORE_FEATURES(48) 게이트가 여기서는 꺼져 있다.
    # 계측 실측: 선별 30개 → 실효피처 [30], core 전체 → [48] (게이트 0개 탈락).
    # 따라서 ① 스윕 AUC 는 '게이트 없는 210피처 풀' 에서 측정된 값이고 ② 프로덕션
    # 챔피언 경로(48피처 게이트)와 직접 비교할 수 없다. 이 사실을 모르고 세운 가설
    # "선별 30개 중 실효는 22개" 는 **틀렸다**(이름집합으로 센 값 12는 게이트를 꺼 둔
    # 이 런에는 적용되지 않는다 — scripts/_rb3_curated_gate_probe.py 머리말 참고).
    # 아래 CO_* 는 그래서 "게이트를 켜면 성능이 어떻게 되는가"를 재는 대조군이다.
    {"id": "CO_core30_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True,
     "desc": "core48 안에서 edge top30 — 프로덕션 게이트를 켠 상태의 대조군"},
    {"id": "CO_core_all_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "all",
     "core_only": True,
     "desc": "core48 전체(선별 없음) — 게이트 적용 시 프로덕션 피처셋과 동일 규모"},
    {"id": "CO_core30_h8", "kind": "quantile", "horizon": 8, "q": 0.30, "select": "top30",
     "core_only": True,
     "desc": "core30 + h8 — 게이트×호라이즌 상호작용 확인"},
    # ── 데이터 축 A/B: 부활 이벤트 피처 가산효과 (2026-09-26, L3 준비) ────────────
    # panel_420_asofpatch_ev.npz 는 기준선 패널과 **행이 비트 동일**하고(13,609행·날짜·종목·
    # 가격 동일, 공유 210컬럼 최대 절대차 0.0) 뒤에 event_* 17개만 덧붙은 패널이다 →
    # 같은 런에서 '17개 포함(EV_all) vs 제외(EV_none)' 를 재면 추가 피처의 가산효과가
    # 패널·행·폴드·시드 모두 통제된 상태로 분리된다.
    # 사전 스크린(scripts/_ev_feature_screen.py, data/reports/ev_feature_screen_20260926.json):
    # 17개 전부 단일피처 AUC 0.4972~0.5007(평균 0.4995) · 종목상수 비율 13/17 이 50% 초과
    # → 누수 게이트는 통과(>0.75 없음)하지만 단변량 edge 는 사실상 0. 기대 Δ ≈ 0.
    {"id": "EV_all_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "desc": "부활 이벤트 피처 포함(227컬럼) — 데이터 축 A/B 실험군"},
    {"id": "EV_none_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "exclude_names": ["event_*", "disclosure_count_5d"],
     "desc": "같은 패널에서 이벤트 피처 17개만 제외(210컬럼) — 동일 런 대조군"},
    # ── 유니버스 축 in-run A/B (2026-09-26 신설) ────────────────────────────────
    # 배경: 기준선 패널은 49종목인데 select=top30 이면 유니버스의 61%를 사는 셈이라
    # '선별'이 거의 없다. U1 은 150종목을 교차패널로 비교해 Δ-0.0266(악화)이었지만
    # 스냅샷이 달라 확증이 아니었다. panel_150u.npz(150종목·41,893행·210컬럼) 안에서
    # 기준선 49종목 부분집합과 전체 150종목을 같은 폴드·같은 피처로 직접 대조한다.
    {"id": "UN_150_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "desc": "150종목 전체 — 유니버스 A/B 실험군(선별 = 상위 20%)"},
    {"id": "UN_49_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "codes_limit": 49,
     "desc": "같은 150종목 패널에서 49종목(패널 순서=유동성 상위)만 — 동일 런 대조군(선별 = 상위 61%)"},
    # ── 하이퍼파라미터 축 (이 스택에서 한 번도 스윕된 적 없음) ──────────────────
    # 근거: 학습 표본이 폴드당 1,230행(49종목×약25일)뿐인데 depth=4·1500트리·lr=0.03 이
    # 고정값이었다(wf_wave.BASE.recipe). 소표본에서는 얕은 트리/큰 학습률이 더 나을 수 있고,
    # 반대로 표본이 늘어난 축에서는 깊은 트리가 필요할 수 있다 — 어느 쪽인지 미측정.
    # 주의: apply_hyperparams 는 n_estimators 키가 있는 모델(lightgbm)만 덮어쓴다
    # (xgboost 는 기본 800·catboost 는 iterations 300 유지). 따라서 lr·depth 중심 비교다.
    {"id": "HP_d2_lr05_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "recipe": {"lr": 0.05, "depth": 2, "n_estimators": 2000},
     "desc": "얕은 트리(depth2)·lr0.05 — 소표본 과적합 억제 가설"},
    {"id": "HP_d3_lr03_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "recipe": {"lr": 0.03, "depth": 3, "n_estimators": 2000},
     "desc": "depth3·lr0.03 (WF5 계열 레시피를 h5·동일 프로토콜로)"},
    {"id": "HP_d6_lr02_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "recipe": {"lr": 0.02, "depth": 6, "n_estimators": 1200},
     "desc": "깊은 트리(depth6)·lr0.02 — 상호작용 포착 가설"},
    # ── HP2: depth 단조 실측의 후속 (2026-09-26) ────────────────────────────────
    # 실측(5폴드×3시드): d2 0.5477 > d3 0.5469 > d4 0.5412(기준) > d6 0.5344 — depth 가 얕을수록
    # 좋아지는 **단조** 패턴. 폴드 std(±0.03~0.05) 때문에 단일 비교는 노이즈지만, 4수준 단조는
    # 소표본 과적합 신호다. → depth 를 더 낮추고(d1), 학습률을 올리고(lr0.08), 정규화를 직접
    # 세게 걸어(recipe_extra) 같은 방향이 재현되는지 본다. arm 은 사전등록 HP_d2_reg_h5.
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
    # ── HP3: 앙상블 구성 축 (미검증) ────────────────────────────────────────────
    # 실측 로그: 3종 소프트보팅에서 catboost val AUC 0.578·가중 0.078 (xgboost 0.79·0.29).
    # arm 사전등록 = EN_equal_h5: 가중치는 **작은 검증분할**에서 계산된 (val AUC − 0.5) 비례값이라
    # 그 자체가 노이즈원이다 → 가중을 걷어내는 쪽이 1차 가설. 나머지는 탐색(개선 판정에 쓰지 않음).
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
    # ── F3: 프로토콜 민감도(최소 학습창) ────────────────────────────────────────
    # 5폴드 프로토콜은 fold1 학습행이 1,230행(49종목×약25일)뿐이다. depth 단조 실측은 그 소표본
    # 과적합의 증상으로 읽힌다 → 폴드 수를 3으로 줄여 최소 학습창을 약 3배로 키운 프로토콜을
    # **같은 런 안에서** 대조한다. 채택하려면 기준선 재설정(승인 대상)이 필요하다 — 여기서는
    # '소표본 페널티가 실재하는가'만 측정한다.
    {"id": "F3_base_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "folds": 3,
     "desc": "기준 설정을 3폴드(최소 학습창 약 3배)로 측정 — 프로토콜 민감도"},
    {"id": "F3_d1_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "folds": 3, "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "desc": "3폴드 + depth1(현 최고 HP) — 표본이 늘면 얕은 트리 이점이 사라지는가"},
    # ── SEL1: 선별 크기(topN) 축 (정렬 버그 수정 후 미측정) ──────────────────────
    # 근거: 학습행이 폴드당 1,230행뿐인데 피처는 30개를 쓴다. depth 단조 실측(과적합 신호)과
    # 같은 맥락에서 '피처 수를 줄이면 일반화가 좋아지는가'는 수정 후 프로토콜에서 미측정이다.
    # arm 사전등록 = SEL_top15_h5 (30 → 절반, 중간값). 나머지는 탐색.
    {"id": "SEL_top10_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top10",
     "desc": "edge 상위 10개만 — 소표본에서 피처 축소 가설"},
    {"id": "SEL_top15_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top15",
     "desc": "edge 상위 15개 — 사전등록 arm"},
    {"id": "SEL_top20_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top20",
     "desc": "edge 상위 20개"},
]


def transform_matrix(Xdf, dates, kind):
    """피처 행렬의 **날짜별 횡단면 변환**.

    근거(실측): 패널 210피처 중 34개가 날짜 내 종목간 분산이 0(시장 전체 동일값)이다.
    또 스케일이 피처마다 제각각(원/비율/지수)이다. 날짜별 rank/z-score 로 바꾸면
    시장레벨 성분이 사라지고 종목간 비교 가능한 형태가 된다 — 이 스택에서 측정된 적 없다.
    각 조회일의 정보만 쓰므로(같은 날 다른 종목) 미래 정보 누수가 아니다.
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
    raise ValueError(f"unknown transform: {kind}")


def main():
    ap = argparse.ArgumentParser(description="라벨 설계 스윕 (확장창 walk-forward)")
    ap.add_argument("--panel", default="/app/app/models/wf/panel_420.npz")
    ap.add_argument("--days", type=int, default=420)
    ap.add_argument("--limit", type=int, default=50)
    # ── 유니버스 확장 옵션 (비우면 현행 기본값: KOSDAQ·코드순·최소 50일) ──
    # 유니버스를 바꿀 때는 --panel 파일명도 새로 줘라(캐시가 파일명으로만 구분된다).
    ap.add_argument("--market", default=None,
                    help="'KOSPI' | 'KOSDAQ' | 'all'(둘 다) | 미지정(현행 기본 KOSDAQ)")
    ap.add_argument("--since", default=None, help="유니버스 기준 시작일(YYYY-MM-DD)")
    ap.add_argument("--min-days", type=int, default=None, help="최소 거래일수")
    ap.add_argument("--min-value", type=float, default=None, help="일평균 거래대금 하한(원)")
    ap.add_argument("--order", default=None, choices=["code", "value"],
                    help="code(현행 알파벳순) | value(거래대금 상위)")
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
    args = ap.parse_args()

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
    # 'all' → None(시장 필터 해제). 미지정(None)이면 build_panel 이 현행 기본값을 쓴다.
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
    df, names = W.build_panel(args.panel, args.limit, args.days, log=ml.log, **uni)
    base_names = [n for n in names if n in df.columns]
    all_dates = sorted(df["date"].astype(str).unique())
    ml.log(f"panel rows={len(df)} dates={len(all_dates)} ({all_dates[0]} ~ {all_dates[-1]})")

    import train_curated as tc
    tc.select_curated_features = lambda n, a=False: list(n)

    # ── 실험용 추가 정규화 파라미터 주입 (2026-09-26 HP2) ────────────────────────
    # 공유 트레이너(train_curated.py·overnight_ml_loop.py)는 손대지 않는다. 이 스크립트
    # 프로세스 안에서만 tc.apply_hyperparams 를 감싸, cfg["recipe_extra"] 의 키를 모델별
    # params 에 덮어쓴다(키가 없는 모델은 그대로 — xgboost/lightgbm/catboost 의 정규화 키
    # 이름이 서로 다르므로 각 모델이 아는 것만 적용된다).
    # 근거: depth 단조 실측(d2 0.5477 > d3 0.5469 > d4 0.5412 > d6 0.5344, 5폴드×3시드) =
    # 소표본 과적합 신호 → 정규화(서브샘플·열샘플·min_child_weight·L2)를 직접 세게 걸어본다.
    # 프로덕션 경로에는 전혀 영향이 없다(여기서만 유효).
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

    # ── 실험용 앙상블 구성 제어 (2026-09-26 HP3 축) ─────────────────────────────
    # EnsembleModel(내 소유: services/xgboost-ml/app/models/ensemble_model.py)은
    # xgboost·lightgbm·catboost 3종을 val AUC 가중(weight = max(auc-0.5, 0.01))으로 평균한다.
    # 이 구성(모델 집합·가중 방식)은 이 스택에서 한 번도 검증된 적이 없다 — 실측 로그에서
    # catboost 가중이 0.078 로 가장 낮았다(val AUC 0.578 vs xgboost 0.79).
    # 여기서는 ml(=overnight_ml_loop)의 EnsembleModel 이름만 서브클래스로 바꾼다 →
    # 공유 모듈·프로덕션 경로는 무변경.
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

            def train(self, X_train, y_train, X_val=None, y_val=None, feature_names=None):
                _m = super().train(X_train, y_train, X_val, y_val, feature_names)
                if _ens_cfg.get("equal_weights"):
                    self.val_weights = {n: 1.0 for n in self.model_names}
                return _m

        ml.EnsembleModel = _EnsembledForSweep

    out_path = args.out
    # dirname 이 빈 문자열(파일명만 준 경우)이면 makedirs("") 가 크래시한다 → 있을 때만 만든다.
    for _p in (out_path, args.summary_out):
        _d = os.path.dirname(_p)
        if _d:
            os.makedirs(_d, exist_ok=True)
    results = []

    cfgs = CONFIGS
    if args.only:
        want = {s.strip() for s in args.only.split(",") if s.strip()}
        cfgs = [c for c in CONFIGS if c["id"] in want]
        if not cfgs:
            print(f"--only 에 해당하는 실험 없음: {sorted(want)}", flush=True)
            return 1
    ml.log(f"실행 실험 {len(cfgs)}개: {[c['id'] for c in cfgs]}")

    for cfg in cfgs:
        exp_id = cfg["id"]
        rec = {"exp": exp_id, "desc": cfg["desc"], "kind": cfg["kind"],
               "horizon": cfg["horizon"], "q": cfg["q"], "select": cfg["select"],
               "transform": cfg.get("transform"),
               "pool": cfg.get("pool"),
               "exclude_market_level": bool(cfg.get("exclude_market_level")),
               "ts": ml.now_iso(), "status": "failed", "folds": {}}
        ml.log(f"=== {exp_id}: {cfg['desc']} ===")
        try:
            y = W.make_labels(df, cfg["kind"], cfg["horizon"], cfg["q"])
            d = df.copy()
            d["_y"] = y
            # 라벨 참조일(그 종목의 h번째 미래 '행'의 날짜) — purge 를 달력 h일이 아니라
            # **라벨이 실제로 참조하는 날짜** 기준으로 하기 위해 미리 계산한다.
            # 근거(실측 2026-09-25): 달력 h일 purge 만으로는 거래 갭이 있는 종목의 학습 행이
            # 테스트 구간 가격을 라벨로 참조한다(폴드4 8행 · 폴드5 14행 = 학습행의 0.09~0.13%).
            try:
                d["_ref"] = df.groupby("stock_code", sort=False)["date"].shift(-cfg["horizon"])
            except Exception as e:      # 계산 실패 시 기존(달력) purge 로 계속
                ml.log(f"  {exp_id}: 라벨 참조일 계산 실패({type(e).__name__}: {e}) — 달력 purge 유지")
                d["_ref"] = None
            d = d[~pd.isna(d["_y"])]
            # ── 종목 필터(유니버스 in-run 대조, 2026-09-26 신설) ─────────────────
            # 왜: 유니버스 효과(49 vs 150종목)를 재려면 같은 패널·같은 피처·같은 폴드에서
            # **행 집합만** 바꿔야 한다. U1(교차패널 비교)은 스냅샷이 달라 Δ-0.0266 이
            # 확증이 못 됐다. codes_from_panel 로 기준선 패널의 종목 목록을 그대로 가져와
            # 부분집합을 만들면 두 arm 이 같은 날짜·같은 폴드·같은 피처가 된다.
            _cfp = cfg.get("codes_from_panel")
            _clim = int(cfg.get("codes_limit") or 0)
            if _cfp or _clim:
                if _clim:
                    _seen: list = []
                    for _c in d["stock_code"].astype(str):
                        if _c not in _seen:
                            _seen.append(_c)
                            if len(_seen) >= _clim:
                                break
                    keep, _src = set(_seen), f"패널 순서 첫 {_clim}종목"
                else:
                    _zp = np.load(_cfp, allow_pickle=True)
                    keep, _src = {str(c) for c in _zp["codes"]}, os.path.basename(str(_cfp))
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
            # cfg 별 폴드 수 override (2026-09-26 F3): 학습 표본이 폴드당 1,230행뿐이라
            # depth 를 낮출수록 좋아지는 단조 패턴이 나왔다 → 최소 학습창을 늘린 프로토콜
            # (3폴드 = fold1 학습 약 3,400행)을 **같은 런 안에서** 5폴드와 대조한다.
            # ⚠ 프로토콜이 다르면 기록 기준선과 직접 비교하지 말 것(구동기는 arm/cf 쌍으로 판정).
            n_folds = int(cfg.get("folds") or args.folds)
            if n_folds != args.folds:
                step = n // (n_folds + 1)
            for i in range(1, n_folds + 1):
                cut = dd[step * i - 1]
                nxt = dd[min(n - 1, step * (i + 1) - 1)]
                h = cfg["horizon"]
                purge = set(dd[max(0, step * i - h):step * i])
                tr = d[(d["date"] <= cut) & (~d["date"].isin(purge))]
                n_ref_purged = 0
                if "_ref" in tr.columns:
                    _bad = np.greater_equal(np.asarray(tr["_ref"].astype(str).values),
                                            np.asarray(dd[step * i]))
                    n_ref_purged = int(_bad.sum())
                    if n_ref_purged:
                        tr = tr[np.logical_not(_bad)]
                te = d[(d["date"] > cut) & (d["date"] <= nxt)]
                if min(len(tr), len(te)) < 100:
                    ml.log(f"  {exp_id} fold{i}: 표본 부족(tr={len(tr)} te={len(te)}), 건너뜀")
                    continue
                trd = tr["date"].astype(str).values
                ted = te["date"].astype(str).values
                tkind = cfg.get("transform")
                Xtr = np.nan_to_num(
                    transform_matrix(tr[base_names], trd, tkind).astype(np.float32), nan=0.0)
                ytr = tr["_y"].values.astype(int)
                Xte = np.nan_to_num(
                    transform_matrix(te[base_names], ted, tkind).astype(np.float32), nan=0.0)
                yte = te["_y"].values.astype(int)
                # ── 하드 가드: 이름↔열 매핑이 깨지면 **즉시 실패**시킨다.
                # 왜: 패널 피처명에 중복 라벨이 있으면(=있었다) `df[list]` 가 열을 부풀려
                # (210→238) 선별 인덱스가 다른 열을 가리키고, 이름 기반 판정이 조용히 무효가 된다
                # (실측 2026-09-25). 이름 수와 열 수가 다르면 그 실험은 보고할 수 없다.
                if Xtr.shape[1] != len(base_names) or Xte.shape[1] != len(base_names):
                    raise RuntimeError(
                        f"피처 열 수 불일치(Xtr={Xtr.shape[1]}, Xte={Xte.shape[1]}, "
                        f"names={len(base_names)}) — 패널 중복 라벨로 이름↔열 매핑이 깨졌다")
                cols = np.std(Xtr, axis=0) > 0
                # ── 피처 풀 필터 (종목-상수 vs 시간가변) ─────────────────────────
                # 실측: top30 을 지배하는 피처(net_income, op_margin, roa, debt_ratio…)가
                # **종목당 값이 1개**(14개월 패널 전체에서 상수)다 → 최신 스냅샷을 과거 날짜에
                # 적용한 것이라면 룩어헤드이고, 측정 AUC 가 부풀려진다. 이 필터로 분리 측정한다.
                pool = cfg.get("pool")
                if pool in ("timevary", "const"):
                    nun = tr[base_names].groupby(tr["stock_code"].values).nunique()
                    is_const = (nun.max(axis=0).values <= 1)
                    pool_mask = is_const if pool == "const" else ~is_const
                    cols = cols & pool_mask
                # ── 시장레벨 제외 (계약 #6): **그 폴드의 학습 구간에서만** 판정한다.
                # 시장레벨 = 관측 2개 이상인 날짜에서 종목간 유니크값이 1인 비율 ≥ 0.9.
                # 결측 지배 컬럼을 '시장레벨'로 오분류하지 않도록 그런 날짜만 분모로 센다.
                n_mkt_excluded = 0
                mkt_excluded_names = []
                if cfg.get("exclude_market_level"):
                    # ⚠ 패널에는 **중복 컬럼명**이 있다(실측: 210개 중 14개 중복 —
                    # cross_trend·price_volume·target_ma_5 … ). 중복 라벨이 있는 DataFrame 에
                    # `&` 같은 pandas 연산을 걸면 **라벨 정렬**이 일어나 결과가 라벨 정렬 순서로
                    # 나오고, 그 `.values` 를 위치 기반 배열(cols/base_names)과 AND 하면
                    # 엉뚱한 컬럼이 제외된다(실측 2026-09-25: op_margin·price_volume·
                    # rank_volatility_20d 가 제외되고 정작 시장레벨인 program_trading_ratio 는 남았다)
                    # → 위치 기반(numpy)으로만 계산하고, 합성 유니크 이름으로 프레임을 만든다.
                    try:
                        _mat = tr[base_names].values.astype(float)
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
                    except Exception as e:      # 판정 실패 시 제외하지 않는다(측정은 계속)
                        ml.log(f"  {exp_id} fold{i}: 시장레벨 판정 실패({type(e).__name__}: {e}) — 제외 없음")
                        is_mkt = np.zeros(len(base_names), dtype=bool)
                    n_mkt_excluded = int((cols & is_mkt).sum())
                    mkt_excluded_names = [f for f, m in zip(base_names, cols & is_mkt) if m]
                    cols = cols & ~is_mkt
                # ── core48 게이트 정합: 선별을 **실제 모델 입력 후보 안에서** 수행 ──────
                # train_seed 가 내부에서 CORE_FEATURES ∩ 선별 로 다시 거르므로(실측:
                # 선별 30개 → 실효 22개), 게이트를 먼저 적용해야 '선별 = 실효' 가 된다.
                if cfg.get("core_only"):
                    core_set = {str(f) for f in getattr(W.tc, "CORE_FEATURES", []) or []}
                    if not core_set:
                        raise RuntimeError("core_only 요청인데 CORE_FEATURES 를 읽지 못했다")
                    cols = cols & np.array([f in core_set for f in base_names], dtype=bool)
                # ── 특정 피처군 제외(A/B 가산효과 측정, 2026-09-26 신설) ──────────
                # 왜: 같은 패널·같은 행에서 "이 피처군을 넣었을 때 vs 뺐을 때"를 재려면
                # 컬럼 단위 제외가 필요하다. 패널 단위 비교는 스냅샷(구간·피처코드)이 달라
                # 통제가 약하다 — U1(150종목)이 그래서 확증이 못 됐다(교차패널 Δ-0.0266).
                # exclude_names 는 정확한 이름 또는 접두어("event_*") 목록. 위치 기반(numpy)으로만
                # 계산한다(중복 라벨 pandas 정렬 사고 — 아래 시장레벨 제외 주석 참고).
                xnames = [str(x) for x in (cfg.get("exclude_names") or [])]
                if xnames:
                    hit = np.array(
                        [any(n == b or (n.endswith("*") and b.startswith(n[:-1]))
                             for n in xnames) for b in base_names], dtype=bool)
                    n_ex = int((cols & hit).sum())
                    ex_kept = [f for f, m in zip(base_names, cols & hit) if m]
                    cols = cols & np.logical_not(hit)
                    ml.log(f"  {exp_id}: 피처군 제외 {n_ex}개 {ex_kept[:6]}")
                    if n_ex == 0:
                        raise RuntimeError(
                            f"exclude_names={xnames} 가 아무 컬럼도 제외하지 않았다 — "
                            "이름 표기가 틀렸다(측정 전에 실패시킨다)")
                fn = [f for f, m in zip(base_names, cols) if m]
                Xtr, Xte = Xtr[:, cols], Xte[:, cols]
                idx, sel_desc = W.subset(fn, cfg["select"], Xtr, ytr)
                sel = [fn[j] for j in idx]
                recipe = cfg.get("recipe") or W.BASE["recipe"]
                _extra.clear()
                _extra.update(cfg.get("recipe_extra") or {})
                _ens_cfg["skip"] = set((cfg.get("ens") or {}).get("skip") or ())
                _ens_cfg["equal_weights"] = bool((cfg.get("ens") or {}).get("equal_weights"))
                aucs = []
                probs = []
                n_eff_seen = set()
                for seed in range(args.seeds):
                    a, _m, _c, _e = ml.train_seed(
                        Xtr[:, idx], None, Xte[:, idx], ytr, None, yte, sel,
                        f"/app/app/models/wf/labelsweep_{exp_id}", seed,
                        recipe["lr"], recipe["depth"],
                        recipe["n_estimators"], True, None)
                    aucs.append(float(a))
                    # 실효 피처 수 = train_seed 내부 curated 게이트를 통과한 개수.
                    # 선별 수와 다르면 그 실험은 '게이트 키홀'을 통해 측정된 것이다.
                    n_eff_seen.add(len(_c) if _c is not None else -1)
                    n_eff_all |= n_eff_seen
                    try:
                        _p = np.asarray(_e.predict(Xte[:, idx]), dtype=float)
                        probs.append(_p[:, -1] if _p.ndim > 1 else _p)
                    except Exception:
                        pass
                # ── 정직 지표: pooled AUC(현재 판정값) 옆에 **날짜별 횡단면 AUC 평균**을 함께 남긴다.
                # 라벨이 날짜내 분위(횡단면 상대)인데 판정은 날짜를 섞은 pooled AUC 라,
                # 날짜 상수(시장레벨) 피처는 날짜별 점수를 통째로 밀어 pooled 만 부풀릴 수 있다.
                # 두 값이 크게 벌어지면 "개선"은 종목간 실력이 아니라 날짜 구성의 산물이다.
                ens_pooled_auc = None
                daily_auc_mean = None
                n_dates_scored = 0
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
                fold_means.append(float(np.mean(aucs)))
                fold_sizes.append((len(tr), len(te)))
                rec["folds"][f"fold{i}"] = {"train_rows": len(tr), "test_rows": len(te),
                                            "test_from": dd[step * i], "test_to": nxt,
                                            "mean": float(np.mean(aucs)),
                                            "n_features": len(sel),
                                            "n_effective_features": sorted(n_eff_seen),
                                            "sel_desc": sel_desc,
                                            "n_market_level_excluded": n_mkt_excluded,
                            "n_label_ref_purged": n_ref_purged,
                                            "market_level_excluded_names": mkt_excluded_names,
                                            "ens_pooled_auc": ens_pooled_auc,
                                            "daily_auc_mean": daily_auc_mean,
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

    ok = [r for r in results if r.get("status") == "ok"]
    ok.sort(key=lambda r: -r["auc_mean"])
    print("\n=== 라벨 스윕 결과 (폴드 평균 기준) ===")
    for r in ok:
        print(f"  {r['exp']:18s} AUC {r['auc_mean']:.4f} ± {r['auc_std']:.4f} "
              f"| {r['desc']}")
    if ok:
        with open(args.summary_out, "w") as f:
            json.dump({"finished_at": ml.now_iso(), "config": vars(args),
                       "best": ok[0], "results": results}, f,
                      ensure_ascii=False, indent=2)
        print(f"\nbest: {ok[0]['exp']} ({ok[0]['desc']}) AUC {ok[0]['auc_mean']:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

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
    # ── 라벨 꼬리 두께 축 (2026-09-28 18:2x 신설 · CG18) ─────────────────────────
    # 왜: quantile 라벨은 날짜별 **양쪽 꼬리 q** 를 쓴다(위 q=1, 아래 q=0, 가운데 결측).
    # 그래서 q 는 비율이라 유니버스 크기와 무관해 보이지만, **절대 개수**는 종목수에 비례한다
    # (49종목 q0.30 → 15 positive/일, 150종목 q0.30 → 45 positive/일). 즉 넓은 유니버스의
    # 라벨은 '거의 동전던지기인 30% 경계 종목'을 대량 포함해 더 잡음이 많다.
    # 가설: 49종목 패널의 0.5406 은 '유니버스(소형주)' 효과가 아니라 **라벨 꼬리 두께** 효과이며,
    # 150종목 패널에서도 q 를 좁히면 AUC 가 올라 그쪽으로 수렴한다. (반대로 안 오르면
    # 소형주 고유의 예측성 → 유니버스 축 재개 근거.)
    {"id": "LS_quant_q15_h5", "kind": "quantile", "horizon": 5, "q": 0.15, "select": "top30",
     "desc": "분위0.15 h5 top30 — 꼬리 두께 축(넓은 유니버스에서 22 positive/일)"},
    {"id": "LS_quant_q10_h5", "kind": "quantile", "horizon": 5, "q": 0.10, "select": "top30",
     "desc": "분위0.10 h5 top30 — 49종목 q0.30 과 같은 '15 positive/일' 밀도"},
    {"id": "LS_quant_q05_h5", "kind": "quantile", "horizon": 5, "q": 0.05, "select": "top30",
     "desc": "분위0.05 h5 top30 — 극단 꼬리(7 positive/일). 표본 급감 주의"},
    # ── 라벨 '잡음' 축 (2026-09-28 신설 — 피처/HP/유니버스 축이 15사이클 전부 노이즈였음) ──
    # 근거: 단일피처 AUC 최고 0.5072·|IC| 최고 0.0202(_db_feature_screen, 38개 중 31개 채점)
    # → 피처·모델 조정으로는 안 움직인다. 같은 피처셋에서 라벨만 바꿔 +0.06 이 난 전례가 있고
    # (0.5133 → 0.5270 → 분위+호라이즌), 지금까지 시험한 라벨 변형은 '분위 비율(q)·호라이즌·중앙값'
    # 뿐이었다. 라벨 자체의 **잡음 구조**는 미측정이다:
    #   ① smooth: 선행 h일 점대점 수익률 대신 1~h일 수익률 평균(5일 보유와 정합) → 잡음 축소
    #   ② voladj: 수익률 ÷ 후행 20일 실현변동성(위험조정) — 횡단면 표준 관행
    # 두 arm 모두 대조군 LS_quant_q30_h5 와 **같은 패널·같은 런**에서 A/B 한다.
    {"id": "LB_smooth_q30_h5", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "top30",
     "desc": "라벨=선행 1~5일 수익률 평균(스무딩) — 5일 보유와 정합, 라벨 잡음 축소"},
    {"id": "LB_voladj_q30_h5", "kind": "voladj", "horizon": 5, "q": 0.30, "select": "top30",
     "desc": "라벨=선행 5일 수익률 ÷ 후행 20일 실현변동성 — 위험조정(시점정합)"},
    # LB2(2026-09-28 04:2x): 약한 양(+) 두 방향의 결합 — 전처리(횡단면 rank, TR1 Δ+0.0107)와
    # 라벨 스무딩(LB_smooth Δ+0.0053, 폴드 std 0.0308→0.0173). 두 축은 메커니즘이 다르므로
    # 가산적일 수 있다(TR2 의 rank×depth1 은 비가산이었다: Δ+0.0060 < rank 단독 +0.0107).
    {"id": "TR_rank_LBsmooth_h5", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "top30",
     "transform": "rank",
     "desc": "횡단면 rank 변환 × 라벨 스무딩 — 두 약한 양(+) 방향의 가산성 측정"},
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
    # ── CG26: 게이트 ON 경로의 '선별 크기' 최적점 (2026-09-29 신설) ──────────────
    # 왜: 게이트 ON 에서 선별 크기 곡선은 **점이 둘뿐**이다 — core30 0.5355/0.5363(같은 런 CG2)
    # / core48 전체 0.5263. 즉 48 은 30 보다 나쁘다. 반면 게이트 OFF 곡선(SEL1: top10 0.5364 ·
    # top15 0.5343 · top20 0.5345 · top30 0.5414)은 30 까지 상승이라 두 곡선의 모양이 다르다 →
    # **게이트 ON 의 최적점이 30 보다 작은지**가 미측정으로 남아 있었다. 여기서 양(+)이 나오면
    # k 를 낮추는 것만으로 승격 경로가 개선된다(추론 계약 변경 없음 — 순수 config 축).
    {"id": "CO_core15_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top15",
     "core_only": True,
     "desc": "게이트 ON + edge top15 — 게이트 ON 선별 크기 곡선의 하단(미측정)"},
    {"id": "CO_core20_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top20",
     "core_only": True,
     "desc": "게이트 ON + edge top20 — 게이트 ON 선별 크기 곡선의 하단(미측정)"},
    {"id": "CO_core40_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top40",
     "core_only": True,
     "desc": "게이트 ON + edge top40 — core30(0.5355)과 core48(0.5263) 사이 확인점"},
    # ── CG19: 게이트 ON 경로에서 '라벨 꼬리 두께' 축이 살아남는가 (2026-09-28 18:3x) ──
    # CG18 실측(panel_150u·게이트 OFF·5폴드×5시드): q0.30 0.5189 → q0.20 0.5198 →
    # q0.15 0.5216 → q0.10 0.5355 → q0.05 0.5609(폴드 짝 Δ+0.0420, 5/5 승).
    # 게이트는 평범 경로에서 −0.005~−0.010 을 물리므로(CG1/CG2), 이득이 게이트를 통과하는지
    # 생산 경로 그대로(core48) 확인해야 한다.
    {"id": "CO_q10_h5", "kind": "quantile", "horizon": 5, "q": 0.10, "select": "top30",
     "core_only": True,
     "desc": "게이트 ON + 분위0.10 — 49종목 q0.30 과 같은 15 pos/일 밀도"},
    {"id": "CO_q05_h5", "kind": "quantile", "horizon": 5, "q": 0.05, "select": "top30",
     "core_only": True,
     "desc": "게이트 ON + 분위0.05 — 극단 꼬리(게이트 OFF 실측 Δ+0.0420)"},
    {"id": "CO_core30_h8", "kind": "quantile", "horizon": 8, "q": 0.30, "select": "top30",
     "core_only": True,
     "desc": "core30 + h8 — 게이트×호라이즌 상호작용 확인"},
    # ── 프로덕션 게이트(core48) 전이 검정 (2026-09-28 신설) ─────────────────────
    # 왜: 이 스크립트는 L341 에서 tc.select_curated_features 를 **항등함수로 몽키패치**한다 →
    # 스윕 AUC 는 '게이트 없는 210피처 풀' 값이다. 그런데 승격 경로(프로덕션 챔피언 러너)는
    # CORE_FEATURES(48) 게이트를 쓴다. 같은 런 실측: CO_core30_h5 0.5355±0.0168 ·
    # CO_core_all_h5 0.5263±0.0246 vs 대조군 LS_quant_q30_h5 0.5414 → **게이트가 약 −0.006**.
    # 즉 17사이클 동안의 모든 '+방향'은 게이트가 꺼진 경로에서 측정된 값이라, 승격 관문에서
    # 유지되는지가 검증되지 않았다(무개선 17사이클의 구조적 원인 후보).
    # 최고 기록 설정(rank×스무딩 Δ+0.0100·폴드 std 0.0107 최저분산)을 게이트 ON 으로 재측정한다.
    {"id": "CO_rank_smooth_h5", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "top30",
     "transform": "rank", "core_only": True,
     "desc": "core48 게이트 ON + rank 변환 + 라벨 스무딩 — 스윕 최고 설정의 프로덕션 경로 전이 검정"},
    # CG2(2026-09-28): 2×2 분해 — 게이트 비용과 게이트 안에서의 rank 이득을 **같은 런**에서 분리한다.
    # CG1 실측: 게이트 OFF 최고 TR_rank_LBsmooth_h5 0.5514 vs 게이트 ON 같은 설정 CO_rank_smooth_h5
    # 0.5412 → 게이트가 −0.0102 를 먹어 스윕 이득(Δ+0.0100)을 통째로 상쇄했다. 다만 CO_core30_h5
    # (게이트 ON 평범)는 이 런에 없어 '게이트 자체의 비용'과 'rank·스무딩의 조건부 효과'가 미분리였다.
    {"id": "CO_rank_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "transform": "rank", "core_only": True,
     "desc": "core48 게이트 ON + rank 변환(스무딩 없음) — 게이트 안에서 rank 단독 효과"},
    # CG3(2026-09-28): 게이트 ON 경로에서 '가진 방향을 전부 결합'하면 +0.02 에 닿는가 — 조정 축의
    # 생산경로 천장을 확정한다. CG2 실측: 게이트 ON 평범 0.5363 → rank+스무딩 0.5412(+0.0049),
    # rank 단독은 0.5098(−0.0265, 스무딩이 없으면 파괴적). 아직 게이트 ON 으로 안 재본 방향은
    # 라벨 스무딩 단독과 depth1·lr0.05(게이트 OFF 에서 Δ+0.0065) 뿐이다.
    {"id": "CO_smooth_h5", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True,
     "desc": "게이트 ON + 라벨 스무딩만(rank 없음) — 스무딩 단독 기여 분리"},
    {"id": "CO_d1_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "desc": "게이트 ON + depth1·lr0.05 — 유일하게 재현된 HP 방향의 게이트 내 효과"},
    {"id": "CO_rank_smooth_d1_h5", "kind": "smooth", "horizon": 5, "q": 0.30, "select": "top30",
     "transform": "rank", "core_only": True, "recipe": {"lr": 0.05, "depth": 1, "n_estimators": 2000},
     "desc": "게이트 ON + rank + 스무딩 + depth1 — 생산경로에서 가진 방향 전부 결합"},
    # ── CG12: 유니버스 크기 단조 추세 (2026-09-28 신설 · panel_150u 전용) ──────────
    # 왜: **같은 프로토콜·게이트 ON** 인데 대조군 AUC 가 패널에 따라 0.5365(49종목, CG4) →
    # 0.5101(150종목, CG11) 로 0.026 벌어진다. 등록 기준선 0.5406 은 49종목 값이고 프로덕션
    # 챔피언은 200종목으로 학습된다 → '기준선 자체가 소유니버스 낙관 편향'이면 승격 프로토콜의
    # +0.02 문턱은 생산 조건에서 도달 불가다(=26사이클 무개선의 구조적 원인 후보).
    # codes_limit 은 **패널 순서(유동성 상위) 첫 N종목**이라 25⊂49⊂75⊂100⊂150 이 중첩
    # 부분집합이고, 같은 패널·같은 행·같은 피처·같은 폴드에서 종목 수만 바뀐다.
    # ⚠ 이건 교차패널 비교가 아니다 — U1(교차패널 Δ−0.0266)이 UN1(동일 패널 Δ+0.0071)에서
    # 부호가 뒤집힌 전례 때문에 반드시 같은 패널 안에서 재야 한다.
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
    # 우승 config(rank+스무딩+depth1)의 전이를 같은 추세 위에서 본다: CG11 은 150종목에서만
    # 쟀다(−0.0109). '49종목에서만 이긴다'면 어느 크기에서 부호가 뒤집히는지가 승격 판단의 근거다.
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
    # ── CG13: 유니버스 교체 노이즈 밴드 (서로소 부분집합, 2026-09-28 신설) ─────────
    # 왜: 이 스택의 '신호'는 전부 유니버스를 바꾸면 뒤집혔다(U1 교차패널 Δ−0.0266 ↔ UN1 같은
    # 패널 +0.0071 / CG5 150종목 부호 반전 / CG12: 49종목 크기에서도 집합이 다르면 우승 config 가
    # −0.0287). 그런데 **같은 크기의 다른 종목 집합**이 만드는 AUC 분산 자체는 한 번도 측정된 적이
    # 없다. panel_150u 를 30종목씩 **서로소** 5구간으로 잘라 같은 config·같은 폴드를 걸면, 그 산포가
    # 곧 '유니버스 교체만으로 만들어지는 Δ' 다. 사전등록: 두 구간의 Δ 가 +0.02 를 넘으면 유니버스
    # 교체만으로 사전문턱이 만들어진다는 뜻 → 과거 Δ≤0.03 은 해석 불가(순열검정 필요)로 규정한다.
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
    # ── CG24 (2026-09-29): 라벨 꼬리 이득의 **유동성 구간 의존성** ─────────────────
    # 동기(실측): CG22 — 생산 유니버스(49종목·panel_420_asofpatch)에서 q0.05 Δ = **+0.0089**
    # (폴드 짝 3/5, min −0.0312)로 150종목 패널의 +0.048 이 재현되지 않았다. 그런데 CG13/CG16 은
    # q0.30 에서 유동성 상위 구간이 하위보다 +0.0287 우세함을 보였다 → 꼬리 이득이 '상위 유동성
    # 구간 특이'라면 CG22 실패의 기제가 설명되고 축을 닫을 수 있다. 같은 행·같은 구간에서 라벨
    # 꼬리만 q0.30↔q0.05 로 바꾼 짝 비교라 유니버스 교체 잡음이 통제된다.
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
    # ── CG14: 우승 후보의 구간 짝(paired) 재판정 (2026-09-28 신설) ────────────────
    # 왜(CG13 실측 5.7분): 같은 크기(30종목) **서로소** 5구간의 게이트 ON 대조군 폴드 평균이
    # 0.5204([0:30)) ~ 0.4917([120:150)) = Δ0.0287 로, 사전문턱 +0.02 를 **유니버스 교체만으로**
    # 넘겼다. 즉 27사이클의 모든 Δ≤0.03 은 유니버스 교체 잡음과 구분되지 않는다.
    # CG4/CG8 의 유일한 후보(게이트 ON + rank + 스무딩 + depth1, Δ+0.0227)도 그 밴드 안이다.
    # → 같은 구간 **안에서** arm−대조군 짝 Δ 를 5개 얻어(구간 간 교체 효과가 상쇄된다) 평균·부호로
    # 재판정한다. 대조군은 같은 런의 US_00_30..US_120_150(게이트 ON 평범 config)이다.
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
    # ── CG15: 유동성 단조 추세의 출처 분해 — 라벨 구성을 바꿔도 남는가 (2026-09-28 신설) ──
    # 왜(CG13 실측): 게이트 ON 대조군의 폴드 평균이 패널 순서(유동성 정렬)대로
    # 0.5204 → 0.5112 → 0.5034 → 0.4998 → 0.4917 로 **거의 단조 감소**했다. 구간마다 종목 수가
    # 30 으로 같으므로 단순 크기 효과는 아니다. 두 갈래 후보: ①유동성 상위 종목이 실제로 더
    # 예측 가능하다 ②분위 라벨(q=0.30)이 30종목 부분집합에서 날짜별 구성이 달라져 생기는 표본 효과.
    # → 같은 5구간을 **시장상대(relative: 횡단면 중앙값 초과) 라벨**로 재측정한다. 추세가 사라지면
    # 라벨 구성 효과(②), 남으면 유동성 예측성(①).
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
    # ── CG16: depth1(유일하게 재현됐던 HP 방향)의 구간 짝 재판정 (2026-09-28 신설) ──
    # 왜: CG8 은 게이트 ON 에서 depth1·lr0.05 단독이 Δ+0.0180(문턱 미달)·rank+스무딩+depth1 결합이
    # Δ+0.0227(문턱 통과)라고 기록했다. 그런데 CG13 실측으로 유니버스 교체만으로 0.0287 이 움직이고,
    # CG14 에서 결합 후보는 구간 짝 Δ 평균 **−0.0057(1/5 구간 양(+))** 로 소멸했다 → 같은 방식으로
    # HP 축의 마지막 후보(depth1)도 검정한다. 여기서 죽으면 튜닝 축 전체가 종료되고 남는 레버는
    # 데이터 축(창·신규 피처)뿐이라는 결론이 선다.
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
    # ── CG23 (2026-09-29): 표본 가중 축 — 기록상 0회 시험된 축 ────────────────────
    # 왜: 33사이클의 모든 arm 은 표본을 **균등 가중**했다(변환·HP·앙상블·유니버스·라벨만 시험).
    # 트레이더는 5일 보유 실현손익으로 평가받으므로 ①최근 표본에 가중(시간 감쇠)하거나
    # ②|선행수익| 이 큰 표본에 가중하면 '돈이 되는' 표본에 집중할 수 있다.
    # 설계(사전등록): panel_150u 를 30종목 **서로소** 5구간으로 나눠 구간 안에서 짝 비교한다
    # (CG13 실측: 유니버스 교체만으로 폴드 평균이 Δ0.0287 움직인다 → 단일 arm 대 단일 대조군 금지).
    # 3-arm: WDn = 가중 없음(원 경로) · WDu = 같은 가중 경로에 균등 가중(배관 통제, Δ≈0 이어야 함)
    #        · WDw_h60 = 시간 감쇠 half-life 60거래일(사전등록 arm).
    # ⚠ WDu 는 '가중 배관 자체가 AUC 를 바꾸지 않음'을 증명하는 통제다 — 이게 0 이 아니면
    #    WDw 의 Δ 를 가중 효과로 귀속할 수 없다.
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
    # 배관 통제(CG23 ④): 가중이 실제로 모델을 바꾸는지 확인용 — 절대값 비교가 아니라
    # '같은 런에서 WDw ≠ WDu 임'을 보이기 위한 참조다.
    {"id": "CO_core30_150", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "core_only": True, "codes_limit": 150,
     "desc": "게이트 ON 대조군 · 150종목 전체(같은 런 기준점)"},
    # ── CG25 (2026-09-29): 피처 시간 변화율(Δ) 파생 축 ──────────────────────────
    # 왜: 라벨(호라이즌·분위·스무딩·상대) · 변환(rank/z) · HP · 앙상블 · 유니버스 · 표본가중이
    # 전부 소진됐고, **피처의 시간 변화율**은 한 번도 만들지 않았다. 패널 스크린 실측(2026-09-29,
    # panel_150u 210피처): 시간가변 최고군 = 변동성·거래량비 계열, |IC| t 는 대부분 3 미만,
    # 무정보 141/210 → '수준'이 이미 약하니 '기울기'가 남은 정보일 수 있다.
    # 설계: 소스 6개를 **이름으로 사전 등록**(스크린에서 소스를 고르면 선택 누수)하고 Δ1·Δ5 를
    # 추가한다(210 → 222컬럼). 같은 구간·같은 폴드에서 derived 유무만 다른 짝 비교(CG13 실측
    # 유니버스 잡음 Δ0.0287 때문에 단일 arm 대 단일 대조군 비교는 금지).
    # ⚠ Δ 는 종목별 과거 행만 쓰고(shift(+k)), 라벨 결측 제거 **전에** 계산한다.
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


def make_weights(spec, dates_tr, fwd_tr=None, day_pos=None, t0_pos=None):
    """CG23 표본 가중 — 학습 행별 가중치 벡터(평균 1 정규화). None 이면 '가중 없음'.

    spec=None            → None  (원 경로 그대로: sample_weight 를 아예 넘기지 않는다)
    {"kind":"uniform"}   → 1.0 벡터 — **가중 배관 자체의 통제**(Δ≈0 이어야 귀속이 성립)
    {"kind":"time_decay","hl":60} → 0.5 ** (경과 거래일 / hl). 가장 최근 학습일이 가중 1.
    {"kind":"absret","clip":0.05} → |h일 선행수익| 기반(상한 clip). h5 보유에서 '크게 움직인'
                                    표본에 집중하는 가설.

    거래일 축을 쓰는 이유: 패널은 거래일만 있고 캘린더 결측(휴장)이 있어 달력일 기준 감쇠는
    구간에 따라 다르게 깎인다. day_pos 는 폴드가 쓰는 dd(정렬된 날짜 목록)의 위치다.
    """
    if not spec:
        return None
    kind = str(spec.get("kind") or "").lower()
    n = len(dates_tr)
    if kind == "uniform":
        w = np.ones(n, dtype=float)
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
        w = 0.25 + 0.75 * (a / mx)      # 완전 0 가중은 행을 없애는 것과 같아 바닥을 둔다
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
    _tc = ml.tc                     # main() 이 몽키패치한 것과 같은 모듈 객체
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
    if not srcs:
        raise RuntimeError("derived.sources 가 패널 컬럼과 하나도 일치하지 않는다")
    lags = [int(k) for k in (spec.get("lags") or [1, 5])]
    if not lags:
        raise RuntimeError("derived.lags 가 비었다")
    # 위치 기반으로만 계산한다(중복 라벨 pandas 정렬 사고 방지 — 시장레벨 제외 주석 참고).
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
    # 구간 끝 고정(기본: 실행 시각). 긴 패널 빌드의 체크포인트 재개용 — wf_wave.build_panel 주석 참조.
    ap.add_argument("--end-date", default=None,
                    help="패널 빌드 구간 끝 날짜 YYYY-MM-DD (기본: 실행 시각)")
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
    # ── 유니버스 경로 (2026-09-28 CG10 배선) ────────────────────────────────────
    # prod = 프로덕션 챔피언 학습기와 같은 함수(app.training.universe.select_training_universe)
    # 로 코드를 고른다 → 스윕 실측이 승격 조건을 대표하게 된다. 기본값(미지정)은 현행 유지.
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
    # ── 확률 덤프 (2026-09-28 CG21) ────────────────────────────────────────────
    # 왜: AUC 는 순위 지표라 '라벨 꼬리를 좁혀 과제가 쉬워진 것'과 '실제로 상위 k 를 잘
    # 고르게 된 것'을 구분하지 못한다. 매매 KPI 는 상위 k 바스켓의 정밀도·기대수익이므로
    # 폴드별 (date, code, y_true, y_pred, fwd_ret) 를 남겨 사후 계산이 가능하게 한다.
    # 기본 None → 기존 실행에는 아무 영향이 없다(파일도 쓰지 않는다).
    ap.add_argument("--dump-preds", default=None,
                    help="폴드 앙상블 확률 jsonl 저장 경로 (예: /app/scripts/_preds_CG21.jsonl)")
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
    if args.universe:
        ml.log(f"universe 경로: {args.universe} (seed={args.universe_seed})")
    df, names = W.build_panel(args.panel, args.limit, args.days, log=ml.log,
                              end_date=args.end_date, universe=args.universe,
                              universe_seed=args.universe_seed, **uni)
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

            def train(self, X_train, y_train, X_val=None, y_val=None, feature_names=None,
                      sample_weight=None):
                # sample_weight 는 CG23(표본 가중) 전용 통로 — None(기본) 이면 기존과 동일.
                _m = super().train(X_train, y_train, X_val, y_val,
                                   feature_names=feature_names, sample_weight=sample_weight)
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
    dump_recs: list = []        # --dump-preds 전용(CG21). 기본 경로에서는 비어 있다.

    cfgs = CONFIGS
    if args.only:
        want = {s.strip() for s in args.only.split(",") if s.strip()}
        cfgs = [c for c in CONFIGS if c["id"] in want]
        if not cfgs:
            print(f"--only 에 해당하는 실험 없음: {sorted(want)}", flush=True)
            return 1
    ml.log(f"실행 실험 {len(cfgs)}개: {[c['id'] for c in cfgs]}")
    # CG23: absret 가중을 쓰는 config 가 있으면 선행수익(_fwd)을 항상 계산해야 한다
    # (기본은 --dump-preds 일 때만 계산 — 기존 동작 무변경).
    _need_fwd = any(str((c.get("weight") or {}).get("kind") or "") == "absret" for c in cfgs)

    for cfg in cfgs:
        exp_id = cfg["id"]
        rec = {"exp": exp_id, "desc": cfg["desc"], "kind": cfg["kind"],
               "horizon": cfg["horizon"], "q": cfg["q"], "select": cfg["select"],
               "transform": cfg.get("transform"),
               "pool": cfg.get("pool"),
               "exclude_market_level": bool(cfg.get("exclude_market_level")),
               "weight": cfg.get("weight"),
               "ts": ml.now_iso(), "status": "failed", "folds": {}}
        ml.log(f"=== {exp_id}: {cfg['desc']} ===")
        try:
            y = W.make_labels(df, cfg["kind"], cfg["horizon"], cfg["q"])
            d = df.copy()
            # ── CG25 파생 피처(Δk): 라벨 결측 제거 **전에** 계산한다 ────────────────────
            # 왜 전에: 분위 라벨은 중간 분위도 NaN 이라, 결측 제거 후 shift 하면 Δ1 이 실제로는
            # 며칠 차이가 된다(조용한 오정의). 기본(bn=base_names)이면 기존 경로와 완전히 동일하다.
            bn = base_names
            dv_names: list = []
            if cfg.get("derived"):
                d, bn = add_derived(d, base_names, cfg["derived"], log=ml.log)
                # 파생 이름 = 확장 목록의 꼬리. 선별·게이트에서 특별 취급하기 위해 들고 간다.
                dv_names = [f for f in bn if f not in set(base_names)]
            d["_y"] = y
            # ── 실현 선행수익(진단·precision@k 전용, 2026-09-28 CG21) ────────────────
            # 판정에는 쓰지 않는다(판정은 폴드 평균 AUC 만). 매매 KPI 는 '상위 k 바스켓의
            # 보유기간 수익률'이라 예측 확률과 함께 남겨야 사후에 계산할 수 있다.
            # 라벨과 같은 shift(-h) 정의를 쓴다(스무딩 라벨이어도 여기선 단순 선행수익).
            if args.dump_preds or _need_fwd:
                try:
                    d["_fwd"] = df.groupby("stock_code", sort=False)["price"].transform(
                        lambda s: s.shift(-int(cfg["horizon"])) / s - 1.0).values
                except Exception as e:      # 계산 실패 시에도 측정은 계속(진단만 생략)
                    ml.log(f"  {exp_id}: 선행수익 계산 실패({type(e).__name__}: {e}) — fwd_ret 결측")
                    d["_fwd"] = np.nan
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
            _cslic = cfg.get("codes_slice")          # [start, stop) on the panel-ordered code list
            if _cfp or _clim or _cslic:
                if _cfp:
                    _zp = np.load(_cfp, allow_pickle=True)
                    keep, _src = {str(c) for c in _zp["codes"]}, os.path.basename(str(_cfp))
                else:
                    # 패널 순서(유니버스 정렬 순) 그대로의 고유 코드 목록.
                    # codes_limit = 첫 N개(중첩 부분집합) · codes_slice = [a,b) 구간(서로소 부분집합).
                    # 왜 둘 다 필요한가(2026-09-28 CG12/CG13): ① 크기 축은 중첩이어야 '종목 수 효과'가
                    # 분리되고 ② 같은 크기의 **다른 종목 집합**이 만드는 AUC 분산은 서로소 부분집합으로만
                    # 잴 수 있다(유니버스 교체만으로 사전문턱 +0.02 가 만들어지는지 검정).
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
                # ── CG23 표본 가중: 폴드 학습행에 대한 가중치(행 순서 = tr 순서) ────────
                # 여기서 계산하는 이유: purge 까지 끝난 tr 이 실제 학습행이고, 가중치는 그 행에
                # 1:1 로 붙어야 한다. 날짜 위치(day_pos)는 폴드가 쓰는 dd 기준이다.
                w_tr = None
                if cfg.get("weight"):
                    _dpos = {str(x): j for j, x in enumerate(dd)}
                    w_tr = make_weights(
                        cfg["weight"], trd,
                        fwd_tr=(np.asarray(tr["_fwd"].values, dtype=float)
                                if "_fwd" in tr.columns else None),
                        day_pos=_dpos, t0_pos=_dpos.get(str(cut)))
                    ml.log(f"  {exp_id} fold{i}: 가중 {cfg['weight']} — "
                           f"min={w_tr.min():.4f} max={w_tr.max():.4f} "
                           f"유니크={len(np.unique(np.round(w_tr, 6)))}")
                tkind = cfg.get("transform")
                Xtr = np.nan_to_num(
                    transform_matrix(tr[bn], trd, tkind).astype(np.float32), nan=0.0)
                ytr = tr["_y"].values.astype(int)
                Xte = np.nan_to_num(
                    transform_matrix(te[bn], ted, tkind).astype(np.float32), nan=0.0)
                yte = te["_y"].values.astype(int)
                # ── 하드 가드: 이름↔열 매핑이 깨지면 **즉시 실패**시킨다.
                # 왜: 패널 피처명에 중복 라벨이 있으면(=있었다) `df[list]` 가 열을 부풀려
                # (210→238) 선별 인덱스가 다른 열을 가리키고, 이름 기반 판정이 조용히 무효가 된다
                # (실측 2026-09-25). 이름 수와 열 수가 다르면 그 실험은 보고할 수 없다.
                if Xtr.shape[1] != len(bn) or Xte.shape[1] != len(bn):
                    raise RuntimeError(
                        f"피처 열 수 불일치(Xtr={Xtr.shape[1]}, Xte={Xte.shape[1]}, "
                        f"names={len(bn)}) — 패널 중복 라벨로 이름↔열 매핑이 깨졌다")
                cols = np.std(Xtr, axis=0) > 0
                # ── 피처 풀 필터 (종목-상수 vs 시간가변) ─────────────────────────
                # 실측: top30 을 지배하는 피처(net_income, op_margin, roa, debt_ratio…)가
                # **종목당 값이 1개**(14개월 패널 전체에서 상수)다 → 최신 스냅샷을 과거 날짜에
                # 적용한 것이라면 룩어헤드이고, 측정 AUC 가 부풀려진다. 이 필터로 분리 측정한다.
                pool = cfg.get("pool")
                if pool in ("timevary", "const"):
                    nun = tr[bn].groupby(tr["stock_code"].values).nunique()
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
                    except Exception as e:      # 판정 실패 시 제외하지 않는다(측정은 계속)
                        ml.log(f"  {exp_id} fold{i}: 시장레벨 판정 실패({type(e).__name__}: {e}) — 제외 없음")
                        is_mkt = np.zeros(len(bn), dtype=bool)
                    n_mkt_excluded = int((cols & is_mkt).sum())
                    mkt_excluded_names = [f for f, m in zip(bn, cols & is_mkt) if m]
                    cols = cols & ~is_mkt
                # ── core48 게이트 정합: 선별을 **실제 모델 입력 후보 안에서** 수행 ──────
                # train_seed 가 내부에서 CORE_FEATURES ∩ 선별 로 다시 거르므로(실측:
                # 선별 30개 → 실효 22개), 게이트를 먼저 적용해야 '선별 = 실효' 가 된다.
                if cfg.get("core_only"):
                    core_set = {str(f) for f in getattr(W.tc, "CORE_FEATURES", []) or []}
                    if not core_set:
                        raise RuntimeError("core_only 요청인데 CORE_FEATURES 를 읽지 못했다")
                    _pre_core = cols.copy()          # std>0 · pool · 시장레벨 제외를 이미 통과한 컬럼
                    cols = cols & np.array([f in core_set for f in bn], dtype=bool)
                    if dv_names:
                        # CG25: 파생 피처는 core48 게이트에 없으므로 그대로 두면 전량 잘린다.
                        # 이 실험은 '프로덕션 게이트를 켠 상태에서 파생을 **추가**하면 움직이는가'이므로
                        # 파생만 게이트 밖으로 통과시킨다(실험 config 한정 · 프로덕션 무변경).
                        # 단 앞선 필터(std=0 · pool · 시장레벨)를 통과한 파생만 되살린다.
                        _dset0 = set(dv_names)
                        cols = cols | (_pre_core & np.array([f in _dset0 for f in bn], dtype=bool))
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
                             for n in xnames) for b in bn], dtype=bool)
                    n_ex = int((cols & hit).sum())
                    ex_kept = [f for f, m in zip(bn, cols & hit) if m]
                    cols = cols & np.logical_not(hit)
                    ml.log(f"  {exp_id}: 피처군 제외 {n_ex}개 {ex_kept[:6]}")
                    if n_ex == 0:
                        raise RuntimeError(
                            f"exclude_names={xnames} 가 아무 컬럼도 제외하지 않았다 — "
                            "이름 표기가 틀렸다(측정 전에 실패시킨다)")
                fn = [f for f, m in zip(bn, cols) if m]
                Xtr, Xte = Xtr[:, cols], Xte[:, cols]
                # ── CG25 선별: 파생을 **선별 후보에서 빼고** top30 을 고른 뒤 강제로 덧붙인다 ──
                # 왜: 파생을 후보에 넣으면 top30 이 다른 30개가 되어(선별 경쟁) '추가 정보'가 아니라
                # '선별 교체'를 재게 된다. 후보에서 빼면 두 arm 의 edge top30 이 **동일 집합**이 되고
                # 차이는 '파생 12컬럼을 더 쓰는가' 하나로 좁혀진다(용량 교란 최소화).
                # ⚠ 파생이 선별에 0개 들어간 것을 '효과 없음'으로 오독하지 않도록 개수를 로그에 남긴다.
                if dv_names:
                    _dset = set(dv_names)
                    _keep = [j for j, f in enumerate(fn) if f not in _dset]
                    _idx_k, sel_desc = W.subset([fn[j] for j in _keep], cfg["select"],
                                                Xtr[:, _keep], ytr)
                    _der = [j for j, f in enumerate(fn) if f in _dset]
                    if not _der:
                        raise RuntimeError("파생 피처가 필터를 통과하지 못했다 — 측정 무효")
                    idx = [_keep[j] for j in _idx_k] + _der
                    sel_desc = f"{sel_desc} + Δ파생 {len(_der)}개(강제)"
                    ml.log(f"  {exp_id}: 선별 {len(idx)}개 = edge top{len(_idx_k)} + Δ파생 {len(_der)}개")
                else:
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
                    a, _m, _c, _e = train_seed_weighted(
                        Xtr[:, idx], None, Xte[:, idx], ytr, None, yte, sel,
                        f"/app/app/models/wf/labelsweep_{exp_id}", seed,
                        recipe["lr"], recipe["depth"],
                        recipe["n_estimators"], True, None, w_tr)
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
                    # ── 확률 덤프(CG21): 판정에는 쓰지 않고 매매 KPI 계산용으로만 남긴다 ──
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
                                            "weight_stats": (None if w_tr is None else {
                                                "min": float(w_tr.min()), "max": float(w_tr.max()),
                                                "mean": float(w_tr.mean()),
                                                "n_unique": int(len(np.unique(np.round(w_tr, 6))))}),
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

    # ── 확률 덤프 저장(파일 핸들을 오래 열지 않는다: 중단돼도 손상 없음) ──────────
    if args.dump_preds:
        try:
            _dd = os.path.dirname(args.dump_preds)
            if _dd:
                os.makedirs(_dd, exist_ok=True)
            with open(args.dump_preds, "w") as f:
                for _r in dump_recs:
                    f.write(json.dumps(_r, ensure_ascii=False) + "\n")
            print(f"preds dumped: {args.dump_preds} ({len(dump_recs)} rows)", flush=True)
        except Exception as e:      # 덤프 실패가 AUC 판정을 막아서는 안 된다
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
                       "best": ok[0], "results": results}, f,
                      ensure_ascii=False, indent=2)
        print(f"\nbest: {ok[0]['exp']} ({ok[0]['desc']}) AUC {ok[0]['auc_mean']:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""top30 vs top60 선별 A/B — 부활 피처가 모델에 들어오면 AUC가 오르는가.

WHY(2026-11-19 실측): 뉴스·공매도 피처를 부활시켰지만 walk-forward 의 `top30` 선별에서
탈락했다(|IC| 0.038·0.033·0.015 vs 경계 0.054). 그래서 v2/v3/v4 모델이 **비트 동일**했다.
이 스크립트는 **같은 패널·같은 폴드**에서 선별 폭만 30 → 60 으로 바꿔 통제 실험을 한다.

판정(사전 고정): top60 의 폴드평균 AUC − top30 폴드평균 AUC **≥ +0.01** 이고
4폴드 중 3폴드 이상에서 방향이 같으면 "부활 피처 기여". 미달이면 기여 없음으로 기록.

사용(컨테이너에서 실행):
  docker exec -w /app -e OMP_NUM_THREADS=4 stock_xgboost_ml \
    python -u scripts/wf_top60_probe.py --panel /app/app/models/wf/p_v4recent.npz \
    --folds 4 --seeds 1 --summary-out /app/reports/overnight/wf_top60_probe.json
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import wf_wave as w  # noqa: E402


def main() -> int:
    # 두 실험을 한 번에: 대조(top30) + 처리(top60), 둘 다 h5(최근 구간 최고 조합)
    # WF_PROBE_SELECT=30,60(기본: A/B 둘 다) | 60(단일: 피처 기여 분리용)
    widths = [x.strip() for x in os.environ.get("WF_PROBE_SELECT", "30,60").split(",") if x.strip()]
    w.CONFIGS = [
        {**w.BASE, "id": "T%s_h5" % k, "horizon": 5, "select": "top%s" % k,
         "desc": ("대조" if k == "30" else "처리") + ": h5 + 분위0.3 + top%s" % k}
        for k in widths
    ]
    return w.main()


if __name__ == "__main__":
    sys.exit(main())

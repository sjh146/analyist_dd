#!/usr/bin/env python3
"""지평(horizon) A/B — 우리 매매는 30~90분 보유인데 라벨은 h5(5거래일)다.

WHY(2026-11-20): 단타로 완전 전환했지만 walk-forward 라벨은 여전히 **5거래일 선행수익**이다.
보유시간(수십 분~1일)과 라벨 지평이 어긋나면, 모델은 우리가 쓰지 않는 신호를 배운다.
이 스크립트는 같은 패널·폴드·선별폭에서 **지평만** h1/h2/h3/h5 로 바꿔 비교한다.

사용(컨테이너):
  docker exec -w /app -e OMP_NUM_THREADS=4 -e WF_PROBE_HORIZONS=1,2,3 \
    stock_xgboost_ml python -u scripts/wf_horizon_probe.py \
    --panel /app/app/models/wf/p_v5recent.npz --universe prod --folds 4 --seeds 1 \
    --summary-out /app/reports/overnight/wf_horizon_probe.json
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import wf_wave as w  # noqa: E402


def main() -> int:
    raw = os.environ.get("WF_PROBE_HORIZONS", "1,2,3,5")
    horizons = [int(x.strip()) for x in raw.split(",") if x.strip()]
    select = os.environ.get("WF_PROBE_SELECT", "60")
    w.CONFIGS = [
        {**w.BASE, "id": "H%d_top%s" % (h, select), "horizon": h, "select": "top%s" % select,
         "desc": "h%d + 분위0.3 + top%s" % (h, select)}
        for h in horizons
    ]
    return w.main()


if __name__ == "__main__":
    sys.exit(main())

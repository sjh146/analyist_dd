#!/usr/bin/env python3
"""CG164 자체점검 — `--drop-serving-blind-features` 플래그의 무회귀 + 효과 증명.

검사(모두 PASS 여야 한다):
  1) 플래그 OFF 의 feature_names.json 은 `FeaturePipeline.get_feature_names()` 와 **비트 동일**
     (기본 OFF = 프로덕션 무변경).
  2) 플래그 ON 은 그 목록에서 `SERVING_BLIND_FEATURES`(횡단면 rank_* 6개)만 제거한다.
  3) 제거 목록이 meta 에 기록된다(dropped_serving_blind == 6 · drop_serving_blind True).
  4) OFF 결과에는 그 6개가 그대로 남아 있다(제거가 '우연히' 일어나지 않았음을 확인).

재현(컨테이너): docker exec stock_xgboost_ml sh -c 'cd /app && python -u scripts/_cg164_drop_blind_test.py'
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, "/app")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from app.training.retrain_champion import (  # noqa: E402
    SERVING_BLIND_FEATURES, retrain_champion,
)
from app.feature_engine.feature_pipeline import FeaturePipeline  # noqa: E402

FAILS = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILS.append(name)


def make_df(n_dates: int = 60, n_stocks: int = 6, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2026-01-01", periods=n_dates, freq="D").strftime("%Y-%m-%d")
    rows = []
    for d in dates:
        for i in range(n_stocks):
            rows.append({
                "date": d, "stock_code": f"{i:06d}",
                "close": 1000.0 + rng.normal(0, 10),
                "return_5d": rng.normal(0, 0.02),
                "return_20d": rng.normal(0, 0.04),
                "volatility_20d": abs(rng.normal(0.02, 0.005)),
                "volume_ratio_5": abs(rng.normal(1.0, 0.2)),
                "ma_position_5": rng.normal(1.0, 0.02),
                "volume_ratio_20": abs(rng.normal(1.0, 0.2)),
            })
    return pd.DataFrame(rows)


def main() -> int:
    print("CG164 drop-serving-blind-features 자체점검")
    canonical = FeaturePipeline().get_feature_names()
    check("SERVING_BLIND_FEATURES == rank_* 6종",
          sorted(SERVING_BLIND_FEATURES) == sorted(
              ["rank_return_5d", "rank_return_20d", "rank_volatility_20d",
               "rank_volume_ratio_5", "rank_ma_position_5", "rank_volume_ratio_20"]),
          f"{SERVING_BLIND_FEATURES}")
    check("canonical 목록이 6개를 포함한다",
          all(n in canonical for n in SERVING_BLIND_FEATURES),
          f"canonical={len(canonical)}")

    df = make_df()
    with tempfile.TemporaryDirectory() as td:
        off_dir = os.path.join(td, "off")
        on_dir = os.path.join(td, "on")
        meta_off = retrain_champion(df.copy(), out_dir=off_dir, n_estimators=5)
        meta_on = retrain_champion(df.copy(), out_dir=on_dir, n_estimators=5,
                                   drop_serving_blind=True)
        names_off = json.load(open(os.path.join(off_dir, "feature_names.json")))
        names_on = json.load(open(os.path.join(on_dir, "feature_names.json")))

    check("① OFF = FeaturePipeline.get_feature_names() 와 비트 동일",
          names_off == canonical, f"{len(names_off)} vs {len(canonical)}")
    dropped = sorted(set(canonical) - set(names_on))
    check("② ON 은 SERVING_BLIND_FEATURES 만 제거",
          dropped == sorted(SERVING_BLIND_FEATURES), f"제거={dropped}")
    check("② ON 목록 = canonical − 6 (순서 유지)",
          names_on == [n for n in canonical if n not in set(SERVING_BLIND_FEATURES)])
    check("③ meta 에 제거 사실이 기록된다",
          bool(meta_on.get("drop_serving_blind")) and
          sorted(meta_on.get("dropped_serving_blind", [])) == sorted(SERVING_BLIND_FEATURES))
    check("④ OFF 는 6개를 그대로 보유(무회귀)",
          all(n in names_off for n in SERVING_BLIND_FEATURES) and
          not meta_off.get("drop_serving_blind") and
          meta_off.get("dropped_serving_blind") == [])
    check("n_features 기록이 실제 목록 길이와 일치",
          meta_off["n_features"] == len(names_off) and
          meta_on["n_features"] == len(names_on))

    print(f"결과: {'전부 PASS' if not FAILS else 'FAIL ' + ', '.join(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())

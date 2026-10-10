#!/usr/bin/env python3
"""CG164 발행 입력(피처) 정합 프로브 — 읽기 전용.

WHY(2026-10-11 엔지니어 자율): CG162 는 배포 스코어가 10-02~10-08 압축됐다가
컨테이너 재시작(10-09)과 함께 복구된 사실을 남겼다. 원인 후보는 (a) 실행 중 프로세스가
읽은 **모델 신원**과 (b) 발행 시 **입력 피처** 의 드리프트다. (a) 는 CG163 감지 가드로 덮었지만,
(b) 는 사후 이분이 불가능했다 — `ml_predictions.features_used` 가 **전 행 빈 배열(`[]`)** 이라
당시 어떤 피처 벡터로 채점했는지 흔적이 없다(실측 2026-10-11: 16일 61,238행 전부 len=0).

이 프로브가 답하는 것:
  1) 지금 살아 있는 경로에서 `models/champion/feature_names.json` 의 이름들이
     `FeaturePipeline.build_features()` 출력에 **전부 존재하는가** (없으면 그 이름은 조용히
     0.0 으로 대체된다 — predictor.py `features.get(f, 0.0)`).
  2) 피처 원천 테이블의 신선도(최신 trade_date) — 압축 구간과 겹치는 원천이 멈춘 채인지.

재현: docker exec stock_xgboost_ml sh -c 'cd /app && python -u scripts/_cg164_publish_input_probe.py'
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
import traceback

SAMPLES = [("2026-10-01", 2), ("2026-10-06", 2)]  # (date, n_stocks) — 정상일 / 압축일
SOURCE_TABLES = [
    "supply_market_features",
    "financial_ratio_features",
    "market_data",
    "stock_prices",
    "event_features",
    "foreign_institutional",
    "krx_short_selling",
]
OUT = "/app/reports/overnight/cg164_publish_input_probe.json"


def main() -> int:
    from app.config import Config
    from app.storage.postgres_storage import PostgresStorage
    from app.feature_engine.feature_pipeline import FeaturePipeline

    cfg = Config()
    names = json.load(open(os.path.join(cfg.MODEL_PATH, "feature_names.json")))
    nameset = set(names)
    out: dict = {
        "champion_model_path": cfg.MODEL_PATH,
        "n_champion_feature_names": len(names),
        "sample_coverage": [],
        "source_freshness": {},
        "features_used_persisted": None,
        "errors": [],
    }

    st = PostgresStorage()
    conn = st._get_conn()

    # --- 0) 발행 시 입력 스냅샷이 저장되는가 ------------------------------------
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT count(*) AS n,"
            " count(*) FILTER (WHERE features_used IS NULL) AS nullf,"
            " count(*) FILTER (WHERE jsonb_array_length(features_used) > 0) AS nonempty"
            " FROM ml_predictions"
        )
        n, nullf, nonempty = cur.fetchone()
        out["features_used_persisted"] = {
            "rows": n, "null": nullf, "nonempty": nonempty,
            "verdict": "입력 스냅샷 없음(사후 원인 이분 불가)" if nonempty == 0 else "저장됨",
        }
    except Exception as e:  # noqa: BLE001
        out["errors"].append(f"features_used probe: {e}")

    # --- 1) 피처 이름 커버리지 --------------------------------------------------
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT stock_code FROM stocks WHERE length(stock_code) = 6"
            " AND stock_code BETWEEN '000000' AND '999999'"
            " ORDER BY stock_code LIMIT 4"
        )
        codes = [r[0] for r in cur.fetchall()]
    except Exception as e:  # noqa: BLE001
        out["errors"].append(f"stock list: {e}")
        codes = []

    fp = FeaturePipeline(pg_conn=conn)
    for date, k in SAMPLES:
        for code in codes[:k]:
            t0 = time.time()
            try:
                feats = fp.build_features(code, date)
                missing = sorted(nameset - set(feats.keys()))
                zeros = sum(1 for n in names if float(feats.get(n, 0.0) or 0.0) == 0.0)
                out["sample_coverage"].append({
                    "date": date, "stock_code": code,
                    "engine_keys": len(feats),
                    "missing_of_champion": len(missing),
                    "missing_sample": missing[:8],
                    "champion_vec_exact_zero": zeros,
                    "elapsed_s": round(time.time() - t0, 1),
                })
            except Exception as e:  # noqa: BLE001
                out["errors"].append(f"build_features {date}/{code}: {type(e).__name__}: {e}")
                traceback.print_exc()

    # --- 2) 원천 신선도 --------------------------------------------------------
    try:
        cur = conn.cursor()
        for t in SOURCE_TABLES:
            try:
                cur.execute(f"SELECT max(trade_date)::text FROM {t}")  # noqa: S608 (고정 목록)
                out["source_freshness"][t] = cur.fetchone()[0]
            except Exception as e:  # noqa: BLE001
                conn.rollback()
                out["source_freshness"][t] = f"ERR {type(e).__name__}"
                out["errors"].append(f"freshness {t}: {e}")
    except Exception as e:  # noqa: BLE001
        out["errors"].append(f"freshness block: {e}")

    cover = out["sample_coverage"]
    if cover:
        miss = [c["missing_of_champion"] for c in cover]
        out["verdict"] = (
            "커버리지 정상 — 모델 이름 전부 엔진에 존재"
            if max(miss) == 0
            else f"이름 드리프트 — 최대 {max(miss)}개가 0.0 으로 대체됨"
        )
        out["median_elapsed_s"] = statistics.median(c["elapsed_s"] for c in cover)
    else:
        out["verdict"] = "측정 불가 — 표본 없음"

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"[ok] {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

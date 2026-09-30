#!/usr/bin/env python3
"""rank_universe_check — 학습 입력과 평가(추론) 입력의 **랭크 유니버스 정합성** 진단.

WHY (가설 CG39): 배포 챔피언의 자기 과제 OOS 는 0.4410 (3/3 창이 0.5 미만, CG37)로 **동전보다 나쁘다**.
약한 신호라면 0.49~0.51 이 나온다 — 0.44 는 '모델이 반대로 정렬돼 있다'는 신호에 가깝다.
그런데 두 경로의 피처를 만드는 함수는 같은 `FeaturePipeline.build_features` 이므로(학습 경로
`build_training_features` 도 페어마다 그 함수를 호출한다) **값 자체는 같다**. 다른 것은 **랭크**다:

  * 학습: `_add_cross_sectional_ranks(df)` — 그날 **학습 유니버스(200종목)** 안에서 `rank(pct=True)`
  * 평가: `champion_robust_eval` → `compute_cross_sectional_ranks(features_by_code)` —
          그날 **표본(유동성 상위 80종목)** 안에서 `rank(pct=True)`

canonical 199피처 중 6개가 rank_*(return_5d·return_20d·volatility_20d·volume_ratio_5·
ma_position_5·volume_ratio_20)이고, 부분집합 랭크는 재척도되므로 같은 종목·같은 날에도 값이
달라진다. 배포 경로(스크리너)도 매일 자기 부분집합 위에서 랭크를 만들므로, 이 불일치가 실제
추론 성능을 깎고 있을 수 있다.

이 스크립트는 **AUC 를 재지 않는다**(그건 후속 A/B). 지금 재는 것은 메커니즘의 크기다:
같은 날·같은 종목에서 두 랭크가 얼마나 다른가 + 추론 경로에서 0으로 채워지는(canonical 에 있으나
피처 사전에 없는) 이름이 몇 개인가.

사용(컨테이너 안, cwd=/app):
    python scripts/rank_universe_check.py --dates 3 --sample 80 --universe 200
출력: /app/reports/rank_universe_check.json
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import statistics
import sys
from datetime import datetime, timedelta

sys.path.insert(0, "/app")

import psycopg2  # noqa: E402

from app.feature_engine.feature_pipeline import FeaturePipeline  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("rank_universe_check")

RANK_COLS = ["return_5d", "return_20d", "volatility_20d",
             "volume_ratio_5", "ma_position_5", "volume_ratio_20"]

# champion_robust_eval.py L47-60 과 동일한 표본 선정(유동성 상위) — 평가 경로를 그대로 재현한다.
SAMPLE_SQL = """
SELECT m.stock_code
FROM market_data m
JOIN stocks s ON s.stock_code = m.stock_code
WHERE m.trade_date BETWEEN %(start)s AND %(end)s
  AND m.close_price > 0
  AND s.stock_name NOT LIKE '%%스팩%%'
GROUP BY m.stock_code
HAVING count(*) FILTER (WHERE m.close_price > 0) >= 60
   AND avg(COALESCE(m.trading_value, m.close_price * m.volume)) > 0
ORDER BY avg(COALESCE(m.trading_value, m.close_price * m.volume)) DESC
LIMIT %(limit)s
"""

DATES_SQL = """
SELECT DISTINCT trade_date::text FROM market_data
WHERE trade_date <= %(end)s AND close_price > 0
ORDER BY trade_date DESC LIMIT %(limit)s
"""


def ranks(values: list[float]) -> list[float]:
    """FeaturePipeline.compute_cross_sectional_ranks 와 같은 계산(pct rank, ties=average)."""
    import pandas as pd
    return [float(x) for x in pd.Series(values, dtype="float64").rank(pct=True).tolist()]


def main() -> int:
    ap = argparse.ArgumentParser(description="랭크 유니버스 정합성 진단(학습 vs 평가 경로)")
    ap.add_argument("--dates", type=int, default=3, help="진단할 거래일 수")
    ap.add_argument("--sample", type=int, default=80, help="평가 경로 표본 종목 수(기본 80)")
    ap.add_argument("--universe", type=int, default=200, help="학습 유니버스 종목 수(기본 200)")
    ap.add_argument("--end-date", default=None, help="기준일(기본 오늘)")
    ap.add_argument("--out", default="/app/reports/rank_universe_check.json")
    args = ap.parse_args()

    pg = psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )
    out: dict = {"measured_at": datetime.now().isoformat(timespec="seconds"),
                 "protocol": {"sample": args.sample, "universe": args.universe,
                              "dates": args.dates}, "dates": []}
    try:
        from app.training.universe import select_training_universe
        uni = select_training_universe(pg, limit=args.universe, min_days=30, seed=0)
        out["universe_size"] = len(uni)

        end = args.end_date or datetime.now().strftime("%Y-%m-%d")
        # 표본 선정용 창: 최근 250일(유동성 조건이 60거래일 이상을 요구한다)
        start = (datetime.strptime(end, "%Y-%m-%d") - timedelta(days=250)).strftime("%Y-%m-%d")

        cur = pg.cursor()
        cur.execute(SAMPLE_SQL, {"start": start, "end": end, "limit": args.sample})
        sample = [r[0] for r in cur.fetchall()]
        cur.execute(DATES_SQL, {"end": end, "limit": args.dates})
        dates = sorted([r[0] for r in cur.fetchall()])
        cur.close()
        out["sample_size"] = len(sample)
        out["sample_universe_overlap"] = len(set(sample) & set(uni))
        logger.info("유니버스 %d종목 · 표본 %d종목(교집합 %d) · 날짜 %s",
                    len(uni), len(sample), out["sample_universe_overlap"], dates)

        pipeline = FeaturePipeline(pg_conn=pg)
        canonical = pipeline.get_feature_names()
        out["canonical_features"] = len(canonical)

        per_col: dict[str, list[float]] = {c: [] for c in RANK_COLS}
        zero_ratios: list[float] = []
        for date in dates:
            feats_by_code = {}
            for code in sorted(set(uni) | set(sample)):
                try:
                    f = pipeline.build_features(code, date)
                except Exception as e:      # 한 종목 실패가 진단을 막지 않는다
                    logger.debug("build_features %s %s 실패: %s", code, date, e)
                    continue
                if f and f.get("feature_count", 0) >= 10:
                    feats_by_code[code] = f
            # 추론 벡터에서 0으로 채워지는 피처 비율 — (종목, 날짜) 셀마다 세어 평균낸다.
            # 왜: 과거 'canonical 에 있으나 항상 0.0 인 피처 20개' 사고가 있었다(학습↔추론 어긋남).
            # ⚠ 종목별 누적 카운트를 그대로 비율로 쓰면 안 된다(날짜 수만큼 부풀려진다).
            for _code, f in feats_by_code.items():
                nz = sum(1 for name in canonical
                         if name not in f or float(f.get(name) or 0.0) == 0.0)
                zero_ratios.append(nz / len(canonical))

            inter = [c for c in sample if c in feats_by_code]
            full = [c for c in feats_by_code if c not in inter] + inter
            if len(inter) < 5 or len(full) < len(inter) + 5:
                logger.warning("%s: 표본/유니버스 부족(inter=%d full=%d) — 건너뜀",
                               date, len(inter), len(full))
                continue
            row = {"date": date, "n_sample": len(inter), "n_full": len(full), "rank_delta": {}}
            for col in RANK_COLS:
                r_sample = ranks([float(feats_by_code[c].get(col, 0.0) or 0.0) for c in inter])
                r_full_map = dict(zip(
                    full, ranks([float(feats_by_code[c].get(col, 0.0) or 0.0) for c in full])))
                d = [abs(r_full_map[c] - r) for c, r in zip(inter, r_sample)]
                per_col[col].extend(d)
                row["rank_delta"][col] = {
                    "mean_abs": round(statistics.fmean(d), 4),
                    "max_abs": round(max(d), 4),
                    "frac_gt_0.1": round(sum(x > 0.1 for x in d) / len(d), 4),
                }
            out["dates"].append(row)
            logger.info("%s: 표본 %d / 전체 %d — %s", date, len(inter), len(full),
                        {c: v["mean_abs"] for c, v in row["rank_delta"].items()})

        agg = {}
        for col, vals in per_col.items():
            if not vals:
                continue
            agg[col] = {"mean_abs": round(statistics.fmean(vals), 4),
                        "p90_abs": round(sorted(vals)[int(0.9 * (len(vals) - 1))], 4),
                        "max_abs": round(max(vals), 4),
                        "frac_gt_0.1": round(sum(x > 0.1 for x in vals) / len(vals), 4),
                        "n": len(vals)}
        out["rank_delta_overall"] = agg
        out["rank_delta_all_mean"] = (round(statistics.fmean(
            [v for vals in per_col.values() for v in vals]), 4)
            if any(per_col.values()) else None)
        # 추론 벡터에서 0으로 채워지는 피처 비율(과거 '항상 0인 20개' 사고의 재발 감시)
        if zero_ratios:
            out["zero_fill_ratio_mean"] = round(statistics.fmean(zero_ratios), 4)
            out["zero_fill_cells"] = len(zero_ratios)
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(json.dumps({k: out[k] for k in
                          ("universe_size", "sample_size", "sample_universe_overlap",
                           "canonical_features", "rank_delta_all_mean")}, ensure_ascii=False))
        print("RANK_UNIVERSE_CHECK ->", args.out)
    finally:
        pg.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

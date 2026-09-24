#!/usr/bin/env python3
"""배포 챔피언 모델의 **견고 성능** 측정 — 시간창 다중 분할 프로토콜.

WHY: 승격 게이트가 오랫동안 `champion/auc.txt` 의 **단일 시드 운값**(0.6131)을 비교
기준으로 썼다. 그래서 그 값이 실제 성능인지조차 확인되지 않은 채 승격이 잠겼다.
이 스크립트는 배포된 모델 자체를 **여러 시간창에서, 실제 추론 경로 그대로**
(스크리너와 동일: FeaturePipeline.build_features → 크로스섹션 랭크 주입 → 챔피언
feature_names 순서로 벡터화 → EnsembleModel.predict) 평가해 기준선을 실측한다.

프로토콜:
  * 최근 거래일을 5개의 **연속 시간창**으로 나눈다(창마다 균등 간격 날짜 표본).
  * 날짜별로 유동성 상위 종목 일부를 표본으로 잡고 그 날짜의 피처를 as-of 로 빌드한다.
  * 라벨: h거래일 뒤 **시장(표본) 상대** 초과수익 — 그 날짜 표본의 중앙값 대비 상위=1.
    (창의 마지막 h 거래일은 미래 가격을 쓰므로 purge 로 제외)
  * 지표: 날짜별 크로스섹션 AUC 의 평균(창 대표값) → 창 평균 ± 표준편차.
    순위 모델이므로 크로스섹션 AUC 가 맞는 지표다(풀링 AUC 도 참고로 함께 낸다).

출력: reports/champion_robust_eval.json
      `--write` 를 주면 champion/robust_auc.json 도 기록(승격 기준선 갱신).

사용(컨테이너 안, cwd=/app):
    python scripts/champion_robust_eval.py --folds 5 --dates-per-fold 10 --stocks 80
    python scripts/champion_robust_eval.py --write
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import statistics
import sys
from datetime import datetime

sys.path.insert(0, "/app")

import numpy as np  # noqa: E402
import psycopg2  # noqa: E402

from app.feature_engine.feature_pipeline import FeaturePipeline  # noqa: E402
from app.models.ensemble_model import EnsembleModel  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("champion_robust_eval")

UNIVERSE_SQL = """
SELECT m.stock_code,
       count(*) FILTER (WHERE m.close_price > 0) AS n_days,
       avg(COALESCE(m.trading_value, m.close_price * m.volume)) AS avg_value
FROM market_data m
JOIN stocks s ON s.stock_code = m.stock_code
WHERE m.trade_date BETWEEN %(start)s AND %(end)s
  AND m.close_price > 0
  AND s.stock_name NOT LIKE '%%스팩%%'
GROUP BY m.stock_code
HAVING count(*) FILTER (WHERE m.close_price > 0) >= 60
   AND avg(COALESCE(m.trading_value, m.close_price * m.volume)) > 0
ORDER BY avg_value DESC
LIMIT %(limit)s
"""


def auc(y: list[int], p: list[float]) -> float:
    """Mann-Whitney AUC (sklearn 의존 없이). 동점은 0.5 로 처리한다."""
    pos = sum(1 for v in y if v == 1)
    neg = len(y) - pos
    if pos == 0 or neg == 0:
        return float("nan")
    order = sorted(range(len(p)), key=lambda i: p[i])
    ranks = [0.0] * len(p)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and p[order[j + 1]] == p[order[i]]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        i = j + 1
    rank_sum = sum(ranks[i] for i in range(len(p)) if y[i] == 1)
    return (rank_sum - pos * (pos + 1) / 2.0) / (pos * neg)


def trading_dates(conn, n_dates: int) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT trade_date FROM market_data
            WHERE close_price > 0 AND trade_date <= CURRENT_DATE - INTERVAL '7 days'
            ORDER BY trade_date DESC LIMIT %s
            """,
            (n_dates,),
        )
        return [r[0].strftime("%Y-%m-%d") for r in cur.fetchall()]


def load_prices(conn, codes: list[str], start: str, end: str) -> dict[str, list[tuple[str, float]]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT stock_code, trade_date, close_price FROM market_data
            WHERE stock_code = ANY(%s) AND trade_date BETWEEN %s AND %s AND close_price > 0
            ORDER BY stock_code, trade_date
            """,
            (codes, start, end),
        )
        out: dict[str, list[tuple[str, float]]] = {}
        for code, d, close in cur.fetchall():
            out.setdefault(code, []).append((d.strftime("%Y-%m-%d"), float(close)))
        return out


def forward_return(prices: list[tuple[str, float]], date: str, h: int) -> float | None:
    """date 종가 대비 h거래일 뒤 종가 수익률. 데이터가 모자라면 None."""
    idx = None
    for i, (d, _) in enumerate(prices):
        if d == date:
            idx = i
            break
    if idx is None or idx + h >= len(prices):
        return None
    base = prices[idx][1]
    if base <= 0:
        return None
    return prices[idx + h][1] / base - 1.0


def main() -> int:
    ap = argparse.ArgumentParser(description="배포 챔피언 견고 성능 측정(시간창 다중 분할)")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--dates-per-fold", type=int, default=10)
    ap.add_argument("--stocks", type=int, default=80)
    ap.add_argument("--horizon", type=int, default=5, help="라벨 호라이즌(거래일)")
    ap.add_argument("--model-dir", default="app/models/champion")
    ap.add_argument("--out", default="app/reports/champion_robust_eval.json")
    ap.add_argument("--write", action="store_true",
                    help="champion/robust_auc.json 도 기록(승격 기준선 갱신)")
    args = ap.parse_args()

    conn = psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD"),
    )
    # 창 크기: 폴드당 h 간격 날짜를 dates-per-fold 개 뽑을 수 있게 창을 넉넉히(≈2배) 잡는다.
    span = args.dates_per_fold * 2 * args.folds
    dates_desc = trading_dates(conn, max(span + 5 * args.horizon, 200))
    if not dates_desc:
        logger.error("거래일을 찾지 못했습니다")
        return 2
    dates_asc = sorted(dates_desc)
    logger.info("거래일 %d개 확보 (%s ~ %s)", len(dates_asc), dates_asc[0], dates_asc[-1])

    chunk = max(1, len(dates_asc) // args.folds)
    windows = [dates_asc[i * chunk:(i + 1) * chunk] for i in range(args.folds)]
    windows = [w for w in windows if len(w) > args.horizon + 1]

    # 유동성 유니버스는 **폴드 창 전체**가 아니라 최근 120거래일 기준으로 뽑는다
    # (창 하나만 보면 n_days 조건을 만족하는 종목이 없어 유니버스가 빈다 — 실측 함정).
    univ_start = dates_asc[max(0, len(dates_asc) - 120)]
    univ_end = dates_asc[-1]
    with conn.cursor() as cur:
        cur.execute(UNIVERSE_SQL, {"start": univ_start, "end": univ_end, "limit": args.stocks})
        universe = [r[0] for r in cur.fetchall()]
    logger.info("표본 유니버스 %d종목(유동성 상위)", len(universe))
    if not universe:
        logger.error("유니버스가 비었습니다")
        return 2

    pipeline = FeaturePipeline(pg_conn=conn)
    ensemble = EnsembleModel(model_dir=args.model_dir)
    ensemble.load(args.model_dir)
    if not ensemble._is_trained:
        logger.error("챔피언 모델 로드 실패: %s", args.model_dir)
        return 2
    with open(os.path.join(args.model_dir, "feature_names.json")) as f:
        feature_names = json.load(f)
    logger.info("챔피언 계약 %d피처", len(feature_names))

    prices = load_prices(conn, universe, dates_asc[0], dates_asc[-1])

    scored_dates = 0
    fold_stats = []
    per_date_aucs: list[float] = []
    pooled_y: list[int] = []
    pooled_p: list[float] = []
    errors = 0

    for fi, window in enumerate(windows):
        # purge: 창의 마지막 h 거래일은 미래 가격이 필요하므로 제외
        usable = window[:-args.horizon] if len(window) > args.horizon else []
        if not usable:
            continue
        step = max(1, len(usable) // args.dates_per_fold)
        sample_dates = usable[::step][:args.dates_per_fold]
        w_aucs: list[float] = []

        for date in sample_dates:
            features_by_code = {}
            for code in universe:
                try:
                    feats = pipeline.build_features(code, date)
                    if feats and feats.get("feature_count", 0) >= 10:
                        features_by_code[code] = feats
                except Exception as e:  # 피처 하나 실패는 그 종목만 건너뛴다
                    errors += 1
                    if errors <= 3:
                        logger.warning("피처 실패 %s %s: %s: %s", code, date, type(e).__name__, e)
            if len(features_by_code) < 10:
                logger.warning("%s: 유효 종목 %d개 — 건너뜀", date, len(features_by_code))
                continue
            pipeline.compute_cross_sectional_ranks(features_by_code)

            codes, probs, rets = [], [], []
            for code, feats in features_by_code.items():
                plist = prices.get(code)
                if not plist:
                    continue
                r = forward_return(plist, date, args.horizon)
                if r is None:
                    continue
                try:
                    vec = np.nan_to_num(
                        np.array([float(feats.get(f, 0.0)) for f in feature_names], dtype=np.float32)
                    )
                    probs.append(float(ensemble.predict(np.array([vec]))[0]))
                    rets.append(r)
                    codes.append(code)
                except Exception as e:
                    errors += 1
                    if errors <= 3:
                        logger.warning("추론 실패 %s %s: %s: %s", code, date, type(e).__name__, e)
            if len(rets) < 10:
                continue

            median_ret = statistics.median(rets)
            y = [1 if r > median_ret else 0 for r in rets]
            a = auc(y, probs)
            if a == a:  # NaN 아님
                w_aucs.append(a)
                per_date_aucs.append(a)
                pooled_y.extend(y)
                pooled_p.extend(probs)
            scored_dates += 1

        if w_aucs:
            fold_stats.append({
                "fold": fi + 1,
                "window": [usable[0], usable[-1]],
                "n_dates": len(w_aucs),
                "auc_mean": round(statistics.mean(w_aucs), 4),
                "auc_std": round(statistics.pstdev(w_aucs), 4) if len(w_aucs) > 1 else None,
            })
            logger.info("폴드 %d: AUC %.4f (날짜 %d개, %s~%s)",
                        fi + 1, fold_stats[-1]["auc_mean"], len(w_aucs),
                        usable[0], usable[-1])

    if not fold_stats:
        logger.error("유효 폴드가 없습니다")
        return 2

    fold_means = [f["auc_mean"] for f in fold_stats]
    payload = {
        "model_dir": args.model_dir,
        "protocol": (f"{len(fold_stats)}-fold 연속 시간창, h={args.horizon} 시장상대 "
                     f"중앙값 라벨, 크로스섹션 AUC, purge={args.horizon}거래일"),
        "metric": "cross_sectional_auc_mean",
        "robust_auc": round(statistics.mean(fold_means), 4),
        "auc_std_across_folds": round(statistics.pstdev(fold_means), 4) if len(fold_means) > 1 else None,
        "folds": fold_stats,
        "dates_scored": scored_dates,
        "rows_scored": len(pooled_y),
        "auc_pooled": round(auc(pooled_y, pooled_p), 4) if pooled_y else None,
        "auc_per_date_mean": round(statistics.mean(per_date_aucs), 4) if per_date_aucs else None,
        "model_aucs": getattr(ensemble, "model_aucs", None) or None,
        "errors": errors,
        "measured_at": datetime.now().isoformat(timespec="seconds"),
    }
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    logger.info("견고 AUC = %.4f (폴드 %s) → %s",
                payload["robust_auc"], fold_means, args.out)

    if args.write:
        target = os.path.join(args.model_dir, "robust_auc.json")
        with open(target, "w") as f:
            json.dump({
                "robust_auc": payload["robust_auc"],
                "metric": payload["metric"],
                "auc_std": payload["auc_std_across_folds"],
                "protocol": payload["protocol"],
                "rows_scored": payload["rows_scored"],
                "folds": [f["auc_mean"] for f in fold_stats],
                "measured_at": payload["measured_at"],
            }, f, ensure_ascii=False, indent=2)
        logger.info("기준선 기록: %s", target)

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

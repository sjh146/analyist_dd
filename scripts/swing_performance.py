#!/usr/bin/env python3
"""
swing_performance.py — 스윙 발굴 종목 승률 검증

과거 swing_candidates_*.csv(발굴 기록)를 읽어, 각 종목의
발굴일(trade_date) 이후 N거래일(기본 7) 수익률을 market_data에서
조회해 승률(수익률>0 비율)·평균 수익률·최고/최저를 계산한다.

사용법:
  # 컨테이너 내부 (DB 접근)
  docker compose exec -T xgboost-ml python /app/scripts/swing_performance.py \
      --horizon 7 --min-days-ago 8

  # 또는 호스트에서 POSTGRES_HOST=localhost POSTGRES_PORT=5434 로 실행
  POSTGRES_HOST=localhost POSTGRES_PORT=5434 python3 scripts/swing_performance.py --horizon 7

산출물: data/reports/swing_performance_<timestamp>.json (전체 통계)
        data/reports/swing_performance_<timestamp>.csv (종목별 상세)
"""
import argparse
import csv
import glob
import json
import logging
import os
import sys
from datetime import datetime, date

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("swing_performance")

PG_HOST = os.environ.get("POSTGRES_HOST", "postgres")
PG_PORT = int(os.environ.get("POSTGRES_PORT", 5432))
PG_DB = os.environ.get("POSTGRES_DB", "stock_trading")
PG_USER = os.environ.get("POSTGRES_USER", "stock_user")
PG_PASS = os.environ.get("POSTGRES_PASSWORD", "9MrYGP4JkNZpMlRGhichIp8TqPuNDNPr")


def get_pg_conn():
    import psycopg2
    return psycopg2.connect(host=PG_HOST, port=PG_PORT, dbname=PG_DB,
                            user=PG_USER, password=PG_PASS)


def load_candidate_files(report_dir):
    """data/reports/swing_candidates_*.csv 로드 → (발굴일, DataFrame) 리스트."""
    files = sorted(glob.glob(os.path.join(report_dir, "swing_candidates_*.csv")))
    out = []
    for f in files:
        try:
            df = pd.read_csv(f)
            if df.empty or "stock_code" not in df.columns:
                continue
            # 파일명에서 발굴일 추출: swing_candidates_YYYYMMDD_HHMMSS.csv
            base = os.path.basename(f)
            ts = base.replace("swing_candidates_", "").replace(".csv", "")
            try:
                pickup = datetime.strptime(ts[:8], "%Y%m%d").date()
            except ValueError:
                continue
            out.append((pickup, df, f))
        except Exception as e:
            logger.warning(f"파일 로드 실패 {f}: {e}")
    return out


def fetch_forward_returns(pg, stock_codes, pickup_date, horizon):
    """발굴일 이후 horizon 거래일 수익률 조회: {code: ret_pct}"""
    rets = {}
    for code in stock_codes:
        try:
            cur = pg.cursor()
            cur.execute("""
                SELECT trade_date, close_price FROM market_data
                WHERE stock_code = %s AND trade_date > %s
                ORDER BY trade_date LIMIT %s
            """, (code, pickup_date, horizon + 1))
            rows = cur.fetchall()
            cur.close()
            if len(rows) >= horizon:
                base = float(rows[0][1])
                fwd = float(rows[horizon][1])
                if base and base > 0:
                    rets[code] = (fwd - base) / base * 100.0
        except Exception:
            continue
    return rets


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report-dir", default="data/reports")
    ap.add_argument("--horizon", type=int, default=7, help="발굴 후 N거래일 수익률")
    ap.add_argument("--min-days-ago", type=int, default=8,
                    help="이 값보다 오래된 발굴만 평가 (오늘 발굴은 미래 가격 없음)")
    ap.add_argument("--output", default=None)
    args = ap.parse_args()

    report_dir = args.report_dir
    if not os.path.isabs(report_dir):
        report_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", report_dir)

    # 프로젝트 루트 기준 상대경로 정규화
    report_dir = os.path.normpath(report_dir)

    files = load_candidate_files(report_dir)
    if not files:
        logger.error(f"후보 파일 없음: {report_dir}/swing_candidates_*.csv")
        sys.exit(1)

    logger.info(f"발굴 기록 {len(files)}개 로드")
    pg = get_pg_conn()

    all_results = []
    per_pickup = []
    today = date.today()
    import datetime as dt

    for pickup, df, path in files:
        age_days = (today - pickup).days
        if age_days < args.min_days_ago:
            logger.info(f"스킵 {pickup} (발굴 {age_days}일 전 — 평가 불가)")
            continue

        codes = df["stock_code"].astype(str).tolist()
        rets = fetch_forward_returns(pg, codes, pickup, args.horizon)

        if not rets:
            logger.info(f"{pickup}: 수익률 데이터 없음 (종목 {len(codes)}개)")
            continue

        wins = sum(1 for r in rets.values() if r > 0)
        total = len(rets)
        stats = {
            "pickup_date": str(pickup),
            "candidates": len(codes),
            "evaluated": total,
            "win_rate": round(wins / total * 100.0, 1) if total else 0.0,
            "avg_return_pct": round(float(np.mean(list(rets.values()))), 2),
            "median_return_pct": round(float(np.median(list(rets.values()))), 2),
            "max_return_pct": round(float(np.max(list(rets.values()))), 2),
            "min_return_pct": round(float(np.min(list(rets.values()))), 2),
            "horizon_days": args.horizon,
        }
        per_pickup.append(stats)
        logger.info(f"{pickup}: 승률 {stats['win_rate']}% (n={total}), "
                    f"평균 {stats['avg_return_pct']}%, 중앙 {stats['median_return_pct']}%")

        # 종목별 상세
        for _, row in df.iterrows():
            code = str(row["stock_code"])
            if code in rets:
                all_results.append({
                    "pickup_date": str(pickup),
                    "stock_code": code,
                    "stock_name": row.get("stock_name", ""),
                    "sector": row.get("sector", ""),
                    "confidence": float(row.get("confidence", 0)),
                    "expected_return": float(row.get("expected_return", 0)),
                    f"return_{args.horizon}d_pct": round(rets[code], 2),
                    "hit": rets[code] > 0,
                })

    pg.close()

    if not per_pickup:
        logger.error("평가 가능한 발굴 기록 없음 (오늘 발굴은 최소 8일 후 평가)")
        sys.exit(2)

    # 전체 통합 승률
    total_wins = sum(1 for r in all_results if r["hit"])
    total_n = len(all_results)
    summary = {
        "generated_at": datetime.now().isoformat(),
        "horizon_days": args.horizon,
        "pickups_evaluated": len(per_pickup),
        "total_evaluated": total_n,
        "overall_win_rate": round(total_wins / total_n * 100.0, 1) if total_n else 0.0,
        "overall_avg_return_pct": round(float(np.mean([r[f"return_{args.horizon}d_pct"] for r in all_results])), 2) if all_results else 0.0,
        "per_pickup": per_pickup,
    }

    # 산출물 저장
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(os.path.dirname(report_dir), "reports")
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, f"swing_performance_{ts}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    logger.info(f"JSON 저장: {json_path}")

    if all_results:
        csv_path = os.path.join(out_dir, f"swing_performance_{ts}.csv")
        pd.DataFrame(all_results).to_csv(csv_path, index=False)
        logger.info(f"CSV 저장: {csv_path}")

    # 콘솔 출력
    print("\n" + "=" * 60)
    print(f"스윙 발굴 승률 검증 (발굴 후 {args.horizon}거래일)")
    print("=" * 60)
    for p in per_pickup:
        print(f"  {p['pickup_date']}: 승률 {p['win_rate']}% (평가 {p['evaluated']}/{p['candidates']}), "
              f"평균 {p['avg_return_pct']}% / 중앙 {p['median_return_pct']}% / "
              f"최고 {p['max_return_pct']}% / 최저 {p['min_return_pct']}%")
    print("-" * 60)
    print(f"전체: 승률 {summary['overall_win_rate']}% (n={total_n}), "
          f"평균 {summary['overall_avg_return_pct']}%")
    print(f"JSON: {json_path}")


if __name__ == "__main__":
    main()

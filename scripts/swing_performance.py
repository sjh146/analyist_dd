#!/usr/bin/env python3
"""
swing_performance.py — 스윙 발굴 종목 승률 검증 (거래비용 반영 + 세그먼트 분석)

과거 swing_candidates_*.csv(발굴 기록)를 읽어, 각 종목의
발굴일(trade_date) 이후 N거래일(기본 7) 수익률을 market_data에서
조회해 승률(수익률>0 비율)·평균 수익률·최고/최저를 계산한다.
수익률 정의: 발굴일 다음 거래일 종가에 매수 → N거래일 보유 → N번째 거래일 종가에 매도.

거래비용 모델 (한국 주식 왕복, 기본값):
  수수료 0.015% x 2 + 거래세 0.18% + 슬리피지 0.05% x 2 = 0.31%
  --round-trip-cost-pct 로 조정 가능.

세그먼트 분석:
  - confidence 3분위 (확신도가 높을수록 이기는가?)
  - 거래대금(trading_value) 3분위 (기관이 못 사는 소형주일수록 이기는가?)
  - sector (업종별 — n>=5만)

사용법:
  docker compose exec -T xgboost-ml python /app/scripts/swing_performance.py \
      --horizon 7 --min-days-ago 8

산출물: data/reports/swing_performance_<timestamp>.json (전체 통계 + 세그먼트)
        data/reports/swing_performance_<timestamp>.csv (종목별 상세)
"""
import argparse
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

# 한국 주식 왕복 거래비용 기본값 (%)
DEFAULT_ROUND_TRIP_COST_PCT = 0.31


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
    """발굴일 이후 horizon 거래일 수익률 + 발굴일 거래대금 조회.

    반환: {code: {"ret_pct": float, "trading_value": float|None}}
    """
    out = {}
    for code in stock_codes:
        try:
            cur = pg.cursor()
            cur.execute("""
                SELECT trade_date, close_price FROM market_data
                WHERE stock_code = %s AND trade_date > %s
                ORDER BY trade_date LIMIT %s
            """, (code, pickup_date, horizon))
            rows = cur.fetchall()
            cur.close()
            # 익일 종가 매수 → N거래일 보유 → rows[N-1] 종가 매도
            if len(rows) >= horizon:
                base = float(rows[0][1])
                fwd = float(rows[horizon - 1][1])
                if base and base > 0:
                    out[code] = {"ret_pct": (fwd - base) / base * 100.0, "volume": None}
        except Exception:
            continue
    # 발굴일 거래량 (유동성 세그먼트용 — trading_value는 수집기가 최근 미기재, volume은 항상 존재)
    for code in list(out.keys()):
        try:
            cur = pg.cursor()
            cur.execute("""
                SELECT volume FROM market_data
                WHERE stock_code = %s AND trade_date = %s
            """, (code, pickup_date))
            row = cur.fetchone()
            cur.close()
            if row and row[0] is not None:
                out[code]["volume"] = float(row[0])
        except Exception:
            continue
    return out


def seg_stats(rows, horizon, cost_pct):
    """세그먼트 통계: n/승률(총)/평균/중앙 + 비용 차감 후 동일 지표."""
    if not rows:
        return None
    gross = [r[f"return_{horizon}d_pct"] for r in rows]
    net = [g - cost_pct for g in gross]
    return {
        "n": len(rows),
        "win_rate_pct": round(sum(1 for g in gross if g > 0) / len(gross) * 100.0, 1),
        "avg_return_pct": round(float(np.mean(gross)), 2),
        "median_return_pct": round(float(np.median(gross)), 2),
        "net_win_rate_pct": round(sum(1 for g in net if g > 0) / len(net) * 100.0, 1),
        "net_avg_return_pct": round(float(np.mean(net)), 2),
        "net_median_return_pct": round(float(np.median(net)), 2),
    }


def compute_segments(all_results, horizon, cost_pct):
    """confidence/거래대금 3분위 + sector 세그먼트."""
    segments = {}

    # 1) confidence 3분위
    confs = sorted(r["confidence"] for r in all_results if r.get("confidence") is not None)
    if confs:
        q1 = confs[len(confs) // 3]
        q2 = confs[2 * len(confs) // 3]
        for label, lo, hi in [("high", q2, float("inf")), ("mid", q1, q2), ("low", float("-inf"), q1)]:
            rows = [r for r in all_results
                    if r.get("confidence") is not None and lo <= r["confidence"] < hi]
            st = seg_stats(rows, horizon, cost_pct)
            if st:
                segments[f"confidence_{label}"] = st

    # 2) 거래량 3분위 (발굴일 기준, 유동성 프록시 — 낮을수록 기관이 못 사는 소형주)
    vals = sorted(r["volume"] for r in all_results if r.get("volume") is not None)
    if vals:
        q1 = vals[len(vals) // 3]
        q2 = vals[2 * len(vals) // 3]
        for label, lo, hi in [("large", q2, float("inf")), ("mid", q1, q2), ("small", float("-inf"), q1)]:
            rows = [r for r in all_results
                    if r.get("volume") is not None and lo <= r["volume"] < hi]
            st = seg_stats(rows, horizon, cost_pct)
            if st:
                segments[f"liquidity_{label}"] = st

    # 3) 배치 타입 (signal=0.55 이상 실시그널 존재, raw_fallback=모두 미달)
    by_batch = {}
    for r in all_results:
        by_batch.setdefault(str(r.get("batch_type") or "unknown"), []).append(r)
    for bt, rows in sorted(by_batch.items(), key=lambda kv: -len(kv[1])):
        st = seg_stats(rows, horizon, cost_pct)
        if st:
            segments[f"batch_{bt}"] = st

    # 4) sector (n>=5)
    by_sector = {}
    for r in all_results:
        s = str(r.get("sector") or "unknown")
        by_sector.setdefault(s, []).append(r)
    for s, rows in sorted(by_sector.items(), key=lambda kv: -len(kv[1])):
        if len(rows) >= 5:
            st = seg_stats(rows, horizon, cost_pct)
            if st:
                segments[f"sector_{s}"] = st

    return segments


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report-dir", default="data/reports")
    ap.add_argument("--horizon", type=int, default=7, help="발굴 후 N거래일 수익률")
    ap.add_argument("--min-days-ago", type=int, default=8,
                    help="이 값보다 오래된 발굴만 평가 (오늘 발굴은 미래 가격 없음)")
    ap.add_argument("--round-trip-cost-pct", type=float, default=DEFAULT_ROUND_TRIP_COST_PCT,
                    help=f"왕복 거래비용 %% (기본 {DEFAULT_ROUND_TRIP_COST_PCT}: 수수료0.015x2+거래세0.18+슬리피지0.05x2)")
    ap.add_argument("--output", default=None)
    args = ap.parse_args()

    report_dir = args.report_dir
    if not os.path.isabs(report_dir):
        report_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", report_dir)
    report_dir = os.path.normpath(report_dir)

    files = load_candidate_files(report_dir)
    if not files:
        logger.error(f"후보 파일 없음: {report_dir}/swing_candidates_*.csv")
        sys.exit(1)

    logger.info(f"발굴 기록 {len(files)}개 로드 | 왕복비용 {args.round_trip_cost_pct}% 반영")
    pg = get_pg_conn()

    all_results = []
    per_pickup = []
    today = date.today()

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

        gross = [v["ret_pct"] for v in rets.values()]
        net = [g - args.round_trip_cost_pct for g in gross]
        wins = sum(1 for r in gross if r > 0)
        net_wins = sum(1 for r in net if r > 0)
        total = len(rets)
        stats = {
            "pickup_date": str(pickup),
            "candidates": len(codes),
            "evaluated": total,
            "win_rate": round(wins / total * 100.0, 1) if total else 0.0,
            "avg_return_pct": round(float(np.mean(gross)), 2),
            "median_return_pct": round(float(np.median(gross)), 2),
            "max_return_pct": round(float(np.max(gross)), 2),
            "min_return_pct": round(float(np.min(gross)), 2),
            "net_win_rate": round(net_wins / total * 100.0, 1) if total else 0.0,
            "net_avg_return_pct": round(float(np.mean(net)), 2),
            "net_median_return_pct": round(float(np.median(net)), 2),
            "horizon_days": args.horizon,
        }
        per_pickup.append(stats)
        logger.info(f"{pickup}: 승률 {stats['win_rate']}% (비용후 {stats['net_win_rate']}%), "
                    f"평균 {stats['avg_return_pct']}% (비용후 {stats['net_avg_return_pct']}%)")

        # 종목별 상세
        for _, row in df.iterrows():
            code = str(row["stock_code"])
            if code in rets:
                gross_ret = rets[code]["ret_pct"]
                all_results.append({
                    "pickup_date": str(pickup),
                    "stock_code": code,
                    "stock_name": row.get("stock_name", ""),
                    "sector": row.get("sector", ""),
                    "confidence": float(row.get("confidence", 0)),
                    "expected_return": float(row.get("expected_return", 0)),
                    "volume": rets[code]["volume"],
                    "batch_type": row.get("batch_type", ""),
                    f"return_{args.horizon}d_pct": round(gross_ret, 2),
                    f"net_return_{args.horizon}d_pct": round(gross_ret - args.round_trip_cost_pct, 2),
                    "hit": gross_ret > 0,
                    "net_hit": gross_ret - args.round_trip_cost_pct > 0,
                })

    pg.close()

    if not per_pickup:
        logger.error("평가 가능한 발굴 기록 없음 (오늘 발굴은 최소 8일 후 평가)")
        sys.exit(2)

    # 전체 통합
    total_wins = sum(1 for r in all_results if r["hit"])
    total_net_wins = sum(1 for r in all_results if r["net_hit"])
    total_n = len(all_results)
    gross_all = [r[f"return_{args.horizon}d_pct"] for r in all_results]
    net_all = [r[f"net_return_{args.horizon}d_pct"] for r in all_results]
    segments = compute_segments(all_results, args.horizon, args.round_trip_cost_pct)
    summary = {
        "generated_at": datetime.now().isoformat(),
        "horizon_days": args.horizon,
        "round_trip_cost_pct": args.round_trip_cost_pct,
        "pickups_evaluated": len(per_pickup),
        "total_evaluated": total_n,
        "overall_win_rate": round(total_wins / total_n * 100.0, 1) if total_n else 0.0,
        "overall_avg_return_pct": round(float(np.mean(gross_all)), 2) if all_results else 0.0,
        "overall_net_win_rate": round(total_net_wins / total_n * 100.0, 1) if total_n else 0.0,
        "overall_net_avg_return_pct": round(float(np.mean(net_all)), 2) if all_results else 0.0,
        "per_pickup": per_pickup,
        "segments": segments,
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
    print("\n" + "=" * 66)
    print(f"스윙 발굴 승률 검증 (발굴 후 {args.horizon}거래일, 왕복비용 {args.round_trip_cost_pct}% 반영)")
    print("=" * 66)
    for p in per_pickup:
        print(f"  {p['pickup_date']}: 승률 {p['win_rate']}% → 비용후 {p['net_win_rate']}% | "
              f"평균 {p['avg_return_pct']}% → {p['net_avg_return_pct']}% (n={p['evaluated']})")
    print("-" * 66)
    print(f"전체: 승률 {summary['overall_win_rate']}% → 비용후 {summary['overall_net_win_rate']}% (n={total_n})")
    print(f"      평균 {summary['overall_avg_return_pct']}% → 비용후 {summary['overall_net_avg_return_pct']}%")
    if segments:
        print("\n[세그먼트] (승률 % → 비용후 % | 평균 % → 비용후 % | n)")
        for name, st in sorted(segments.items()):
            print(f"  {name:24s} {st['win_rate_pct']:>5} → {st['net_win_rate_pct']:>5}  | "
                  f"{st['avg_return_pct']:>6} → {st['net_avg_return_pct']:>6}  | n={st['n']}")
    print(f"\nJSON: {json_path}")


if __name__ == "__main__":
    main()

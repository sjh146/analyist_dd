#!/usr/bin/env python3
"""daytrading_performance.py — 단타스크리너 성과 채점 (분봉 부재 → 일봉 갭 프록시).

과거 daytrading_candidates_*.csv(발굴 기록)를 읽어, 각 종목의 발굴일(D) → D+1
시가 갭으로 채점한다. 분봉(KIS) 데이터가 아직 없으므로 30분 창은 프록시로
D 종가→D+1 시가 갭을 사용한다(승 = 갭 업). 분봉이 도착하면
MinutePriceProvider 구현만 교체해 30분 창 채점으로 전환된다.

사용법:
  python3 scripts/daytrading_performance.py --report-dir data/reports
  python3 scripts/daytrading_performance.py --input data/reports/daytrading_candidates_x.csv
"""
import argparse
import glob
import json
import logging
import os
import sys
from datetime import date

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("daytrading_performance")

PG_HOST = os.environ.get("POSTGRES_HOST", "127.0.0.1")
PG_PORT = int(os.environ.get("POSTGRES_PORT", 5432))
PG_DB = os.environ.get("POSTGRES_DB", "stock_trading")
PG_USER = os.environ.get("POSTGRES_USER", "stock_user")
PG_PASS = os.environ.get("POSTGRES_PASSWORD", "")

DETAIL_COLUMNS = [
    "pickup_date", "stock_code", "stock_name", "sector", "score",
    "signal_close", "next_date", "next_open", "gap_return_pct", "gap_hit",
]


def get_pg_conn():
    """psycopg2 연결 생성 (lazy import)."""
    import psycopg2
    return psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PASS
    )


def load_candidate_files(report_dir):
    """data/reports/daytrading_candidates_*.csv 로드 → [(signal_date, DataFrame, path)]."""
    files = sorted(glob.glob(os.path.join(report_dir, "daytrading_candidates_*.csv")))
    out = []
    for f in files:
        try:
            df = pd.read_csv(f)
            if df.empty or "stock_code" not in df.columns:
                continue
            signal_date = None
            if "signal_date" in df.columns and not df["signal_date"].isna().all():
                try:
                    signal_date = pd.to_datetime(str(df["signal_date"].iloc[0])).date()
                except (ValueError, TypeError):
                    signal_date = None
            if signal_date is None:
                try:
                    signal_date = parse_signal_date_from_filename(f)
                except ValueError:
                    continue
            out.append((signal_date, df, f))
        except Exception as e:
            logger.warning(f"파일 로드 실패 {f}: {e}")
    return out


def parse_signal_date_from_filename(name_or_path):
    """파일명 *_YYYYMMDD_HHMMSS.csv 에서 날짜 파싱 → datetime.date."""
    base = os.path.basename(name_or_path)
    m = None
    import re
    m = re.search(r"_(\d{8})_\d{6}\.csv$", base)
    if not m:
        raise ValueError(f"파일명에서 날짜를 파싱할 수 없습니다: {name_or_path}")
    from datetime import datetime
    return datetime.strptime(m.group(1), "%Y%m%d").date()


def normalize_code(code):
    """종목코드를 6자리 숫자 문자열로 정규화 (pandas가 선행 0을 자르는 것 방지)."""
    s = str(code).strip()
    if s.isdigit():
        return s.zfill(6)
    return s


def fetch_signal_close(pg, stock_code, signal_date):
    """발굴일(D) 종가를 DB에서 조회 → float|None."""
    cur = pg.cursor()
    cur.execute(
        "SELECT close_price FROM market_data "
        "WHERE stock_code = %s AND trade_date = %s",
        (stock_code, signal_date),
    )
    row = cur.fetchone()
    cur.close()
    if row is None or row[0] is None:
        return None
    return float(row[0])


def score_gap(signal_close, next_open):
    """(gap_return_pct, gap_hit) — ((next_open/signal_close - 1) * 100, >0)."""
    if signal_close is None or signal_close <= 0 or next_open is None:
        return None, None
    ret = (float(next_open) / float(signal_close) - 1.0) * 100.0
    return round(ret, 2), ret > 0


def compute_gap_from_provider(provider, stock_code, signal_close, signal_date):
    """MinutePriceProvider로 D+1 시가(30분 창 프록시) 조회 후 갭 채점."""
    from datetime import timedelta
    d_plus_1 = signal_date + timedelta(days=1)
    open_price = provider.get_minute_price(stock_code, d_plus_1)
    if open_price is None:
        return None, None, d_plus_1
    ret, hit = score_gap(signal_close, open_price)
    return ret, hit, d_plus_1


def _stats(values):
    """수익률 리스트 → 통계 dict (빈 리스트 → 0)."""
    if not values:
        return {
            "win_rate": 0.0, "avg_return_pct": 0.0, "median_return_pct": 0.0,
            "max_return_pct": 0.0, "min_return_pct": 0.0,
        }
    arr = np.array(values, dtype=float)
    wins = int(np.sum(arr > 0))
    return {
        "win_rate": round(wins / len(arr) * 100.0, 1),
        "avg_return_pct": round(float(np.mean(arr)), 2),
        "median_return_pct": round(float(np.median(arr)), 2),
        "max_return_pct": round(float(np.max(arr)), 2),
        "min_return_pct": round(float(np.min(arr)), 2),
    }


def summarize_results(rows):
    """성과 통계 요약 → {'count', 'gap': {...}}."""
    gap_vals = [float(r["gap_return_pct"]) for r in rows]
    return {"count": len(rows), "gap": _stats(gap_vals)}


def main():
    ap = argparse.ArgumentParser(
        description="단타스크리너 성과 채점 (분봉 부재 → D→D+1 시가 갭 프록시)")
    ap.add_argument("--report-dir", default="data/reports", help="후보 CSV 디렉토리")
    ap.add_argument("--input", default=None, help="단일 후보 CSV 경로")
    ap.add_argument("--min-days-ago", type=int, default=1,
                    help="이 값보다 오래된 발굴만 채점 (기본 1)")
    ap.add_argument("--minute-offset", type=int, default=30,
                    help="D+1 개장 후 분 시점 가격 (기본 30, 분봉 도착 시 사용)")
    ap.add_argument("--provider", choices=["gap", "kis"], default="gap",
                    help="30분 창 가격 제공자: gap(D+1 시가 갭 프록시, 기본) | "
                         "kis(KIS minute_bars 실측 30분 가격)")
    ap.add_argument("--output", default=None, help="출력 디렉토리 (기본: report-dir)")
    args = ap.parse_args()

    report_dir = args.report_dir
    if not os.path.isabs(report_dir):
        report_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", report_dir)
    report_dir = os.path.normpath(report_dir)

    if args.input:
        files = []
        try:
            df = pd.read_csv(args.input)
            if df.empty or "stock_code" not in df.columns:
                logger.error(f"입력 CSV에 stock_code 컬럼 없음: {args.input}")
                sys.exit(1)
            signal_date = None
            if "signal_date" in df.columns and not df["signal_date"].isna().all():
                try:
                    signal_date = pd.to_datetime(str(df["signal_date"].iloc[0])).date()
                except (ValueError, TypeError):
                    signal_date = None
            if signal_date is None:
                try:
                    signal_date = parse_signal_date_from_filename(args.input)
                except ValueError:
                    logger.error(f"입력 CSV에서 발굴일 파싱 불가: {args.input}")
                    sys.exit(1)
            files = [(signal_date, df, args.input)]
        except Exception as e:
            logger.error(f"입력 CSV 로드 실패: {e}")
            sys.exit(1)
    else:
        files = load_candidate_files(report_dir)

    if not files:
        logger.error(f"후보 파일 없음: {report_dir}/daytrading_candidates_*.csv")
        sys.exit(1)

    logger.info(f"발굴 기록 {len(files)}개 로드 | 30분 창={args.minute_offset}분 (갭 프록시)")
    pg = get_pg_conn()

    from day_trading_engine import DailyGapProvider, KisMinuteProvider
    if args.provider == "kis":
        provider = KisMinuteProvider(pg, open_time="090000")
        mode = "kis_30min"
        logger.info("30분 창 제공자: KisMinuteProvider (minute_bars 실측)")
    else:
        provider = DailyGapProvider(pg)
        mode = "gap_proxy"

    all_results = []
    per_pickup = []
    today = date.today()

    for signal_date, df, path in files:
        age_days = (today - signal_date).days
        if age_days < args.min_days_ago:
            logger.info(f"스킵 {signal_date} (발굴 {age_days}일 전 — 채점 불가)")
            continue

        batch_rows = []
        for _, row in df.iterrows():
            code = normalize_code(row["stock_code"])
            try:
                signal_close = fetch_signal_close(pg, code, signal_date)
                if signal_close is None:
                    csv_close = row.get("close_price")
                    if csv_close is None or (isinstance(csv_close, float)
                                             and np.isnan(csv_close)):
                        continue
                    signal_close = float(csv_close)
                ret, hit, next_date = compute_gap_from_provider(
                    provider, code, signal_close, signal_date)
                if ret is None:
                    continue
                batch_rows.append({
                    "pickup_date": str(signal_date),
                    "stock_code": code,
                    "stock_name": str(row.get("stock_name", "")),
                    "sector": str(row.get("sector", "")),
                    "score": float(row.get("score", 0)),
                    "signal_close": round(signal_close, 2),
                    "next_date": str(next_date),
                    "next_open": None,
                    "gap_return_pct": ret,
                    "gap_hit": hit,
                })
            except Exception as e:
                logger.warning(f"{code} 채점 실패: {e}")
                continue

        if batch_rows:
            batch_summary = summarize_results(batch_rows)
            per_pickup.append({
                "pickup_date": str(signal_date),
                "evaluated": batch_summary["count"],
                "gap": batch_summary["gap"],
            })
            all_results.extend(batch_rows)
            logger.info(
                f"{signal_date}: 채점 {batch_summary['count']}개, "
                f"갭 승률 {batch_summary['gap']['win_rate']}%"
            )

    pg.close()

    if not per_pickup:
        logger.error("채점 가능한 발굴 기록 없음")
        sys.exit(2)

    overall = summarize_results(all_results)
    summary = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "window_minutes": args.minute_offset,
        "mode": mode,
        "files_evaluated": len(per_pickup),
        "total_evaluated": overall["count"],
        "overall": {"gap": overall["gap"]},
        "per_pickup": per_pickup,
    }

    ts = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    out_dir = args.output if args.output else report_dir
    if not os.path.isabs(out_dir):
        out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", out_dir)
    out_dir = os.path.normpath(out_dir)
    os.makedirs(out_dir, exist_ok=True)

    json_path = os.path.join(out_dir, f"daytrading_performance_{ts}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    logger.info(f"JSON 저장: {json_path}")

    if all_results:
        csv_path = os.path.join(out_dir, f"daytrading_performance_{ts}.csv")
        pd.DataFrame(all_results, columns=DETAIL_COLUMNS).to_csv(csv_path, index=False)
        logger.info(f"CSV 저장: {csv_path}")

    print("\n" + "=" * 60)
    print(f"단타스크리너 성과 채점 (30분 창={args.minute_offset}분, 갭 프록시)")
    print("=" * 60)
    for p in per_pickup:
        g = p["gap"]
        print(f"  {p['pickup_date']}: 채점 {p['evaluated']}개 | "
              f"갭 승률 {g['win_rate']}% 평균 {g['avg_return_pct']}%")
    print("-" * 60)
    g = overall["gap"]
    print(f"전체: 채점 {overall['count']}개 | "
          f"갭 승률 {g['win_rate']}% 평균 {g['avg_return_pct']}%")
    print(f"JSON: {json_path}")


if __name__ == "__main__":
    main()

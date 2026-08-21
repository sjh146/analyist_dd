#!/usr/bin/env python3
"""close_screener_performance.py — 종가스크리너 익일 매도 성과 검증.

과거 close_candidates_*.csv(발굴 기록)를 읽어, 각 종목의
발굴일(signal_date) 종가 매수 → 다음 거래일(T+1) 시가/종가 매도 수익률을
market_data에서 조회해 승률·평균·중앙값·최고/최저를 계산한다.

사용법:
  python3 scripts/close_screener_performance.py --report-dir data/reports
  python3 scripts/close_screener_performance.py --input data/reports/close_candidates_xxx.csv
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
logger = logging.getLogger("close_screener_performance")

PG_HOST = os.environ.get("POSTGRES_HOST", "postgres")
PG_PORT = int(os.environ.get("POSTGRES_PORT", 5432))
PG_DB = os.environ.get("POSTGRES_DB", "stock_trading")
PG_USER = os.environ.get("POSTGRES_USER", "stock_user")
PG_PASS = os.environ.get("POSTGRES_PASSWORD", "")

DETAIL_COLUMNS = [
    "pickup_date", "stock_code", "stock_name", "sector", "score",
    "signal_close", "next_date", "next_open", "next_close",
    "open_sell_return_pct", "close_sell_return_pct", "open_hit", "close_hit",
]


def get_pg_conn():
    """psycopg2 연결 생성 (lazy import)."""
    import psycopg2
    return psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PASS
    )


def load_candidate_files(report_dir):
    """data/reports/close_candidates_*.csv 로드 → [(signal_date, DataFrame, path)]."""
    files = sorted(glob.glob(os.path.join(report_dir, "close_candidates_*.csv")))
    out = []
    for f in files:
        try:
            df = pd.read_csv(f)
            if df.empty or "stock_code" not in df.columns:
                continue
            # signal_date 컬럼 우선, 없으면 파일명 타임스탬프
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
    from close_screener import parse_signal_date_from_filename as _parse
    return _parse(name_or_path)


def fetch_next_day_prices(pg, stock_code, signal_date):
    """발굴일 종가 + 다음 거래일 시가/종가 조회 → dict|None.

    dict: {'signal_close': float|None, 'next_date': date, 'next_open': float,
           'next_close': float}
    """
    cur = pg.cursor()
    cur.execute("""
        SELECT trade_date, open_price, close_price FROM market_data
        WHERE stock_code = %s AND trade_date >= %s
        ORDER BY trade_date LIMIT 2
    """, (stock_code, signal_date))
    rows = cur.fetchall()
    cur.close()
    if not rows:
        return None
    first_date = rows[0][0]
    if first_date == signal_date:
        if len(rows) < 2:
            return None
        return {
            "signal_close": float(rows[0][2]),
            "next_date": rows[1][0],
            "next_open": float(rows[1][1]),
            "next_close": float(rows[1][2]),
        }
    elif first_date > signal_date:
        # 시그널일 바가 없음 (휴장 등) → signal_close None
        return {
            "signal_close": None,
            "next_date": rows[0][0],
            "next_open": float(rows[0][1]),
            "next_close": float(rows[0][2]),
        }
    return None


def compute_trade_returns(signal_close, next_open, next_close):
    """(open_ret_pct, close_ret_pct) — ((next/signal - 1) * 100)."""
    if signal_close is None or signal_close <= 0:
        raise ValueError("signal_close가 없거나 0 이하입니다.")
    open_ret = (float(next_open) / float(signal_close) - 1.0) * 100.0
    close_ret = (float(next_close) / float(signal_close) - 1.0) * 100.0
    return open_ret, close_ret


def _stats(values):
    """수익률 리스트 → 통계 dict (빈 리스트 → 0)."""
    if not values:
        return {
            "win_rate": 0.0,
            "avg_return_pct": 0.0,
            "median_return_pct": 0.0,
            "max_return_pct": 0.0,
            "min_return_pct": 0.0,
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
    """성과 통계 요약 → {'count', 'open_sell': {...}, 'close_sell': {...}}."""
    open_vals = [float(r["open_sell_return_pct"]) for r in rows]
    close_vals = [float(r["close_sell_return_pct"]) for r in rows]
    return {
        "count": len(rows),
        "open_sell": _stats(open_vals),
        "close_sell": _stats(close_vals),
    }


def main():
    ap = argparse.ArgumentParser(description="종가스크리너 익일 매도 성과 검증")
    ap.add_argument("--report-dir", default="data/reports", help="후보 CSV 디렉토리")
    ap.add_argument("--input", default=None, help="단일 후보 CSV 경로")
    ap.add_argument("--min-days-ago", type=int, default=1,
                    help="이 값보다 오래된 발굴만 평가 (기본 1)")
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
        logger.error(f"후보 파일 없음: {report_dir}/close_candidates_*.csv")
        sys.exit(1)

    logger.info(f"발굴 기록 {len(files)}개 로드")
    pg = get_pg_conn()

    all_results = []
    per_pickup = []
    today = date.today()

    for signal_date, df, path in files:
        age_days = (today - signal_date).days
        if age_days < args.min_days_ago:
            logger.info(f"스킵 {signal_date} (발굴 {age_days}일 전 — 평가 불가)")
            continue

        batch_rows = []
        for _, row in df.iterrows():
            code = str(row["stock_code"])
            try:
                info = fetch_next_day_prices(pg, code, signal_date)
                if info is None:
                    continue
                signal_close = info["signal_close"]
                if signal_close is None:
                    # DB에 시그널일 종가 없음 → CSV close_price 폴백
                    csv_close = row.get("close_price")
                    if csv_close is None or (isinstance(csv_close, float) and np.isnan(csv_close)):
                        continue
                    signal_close = float(csv_close)
                open_ret, close_ret = compute_trade_returns(
                    signal_close, info["next_open"], info["next_close"]
                )
                batch_rows.append({
                    "pickup_date": str(signal_date),
                    "stock_code": code,
                    "stock_name": str(row.get("stock_name", "")),
                    "sector": str(row.get("sector", "")),
                    "score": float(row.get("score", 0)),
                    "signal_close": round(signal_close, 2),
                    "next_date": str(info["next_date"]),
                    "next_open": round(info["next_open"], 2),
                    "next_close": round(info["next_close"], 2),
                    "open_sell_return_pct": round(open_ret, 2),
                    "close_sell_return_pct": round(close_ret, 2),
                    "open_hit": open_ret > 0,
                    "close_hit": close_ret > 0,
                })
            except Exception as e:
                logger.warning(f"{code} 평가 실패: {e}")
                continue

        if batch_rows:
            batch_summary = summarize_results(batch_rows)
            per_pickup.append({
                "pickup_date": str(signal_date),
                "evaluated": batch_summary["count"],
                "open_sell": batch_summary["open_sell"],
                "close_sell": batch_summary["close_sell"],
            })
            all_results.extend(batch_rows)
            logger.info(
                f"{signal_date}: 평가 {batch_summary['count']}개, "
                f"시가매도 승률 {batch_summary['open_sell']['win_rate']}%, "
                f"종가매도 승률 {batch_summary['close_sell']['win_rate']}%"
            )

    pg.close()

    if not per_pickup:
        logger.error("평가 가능한 발굴 기록 없음")
        sys.exit(2)

    overall = summarize_results(all_results)
    summary = {
        "generated_at": datetime.now().isoformat(),
        "horizon": "T+1",
        "files_evaluated": len(per_pickup),
        "total_evaluated": overall["count"],
        "overall": {
            "open_sell": overall["open_sell"],
            "close_sell": overall["close_sell"],
        },
        "per_pickup": per_pickup,
    }

    # 산출물 저장
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = args.output if args.output else report_dir
    if not os.path.isabs(out_dir):
        out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", out_dir)
    out_dir = os.path.normpath(out_dir)
    os.makedirs(out_dir, exist_ok=True)

    json_path = os.path.join(out_dir, f"close_performance_{ts}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    logger.info(f"JSON 저장: {json_path}")

    if all_results:
        csv_path = os.path.join(out_dir, f"close_performance_{ts}.csv")
        pd.DataFrame(all_results, columns=DETAIL_COLUMNS).to_csv(csv_path, index=False)
        logger.info(f"CSV 저장: {csv_path}")

    # 콘솔 출력
    print("\n" + "=" * 60)
    print("종가스크리너 익일 매도 성과 검증 (T+1)")
    print("=" * 60)
    for p in per_pickup:
        o = p["open_sell"]
        c = p["close_sell"]
        print(f"  {p['pickup_date']}: 평가 {p['evaluated']}개 | "
              f"시가매도 승률 {o['win_rate']}% 평균 {o['avg_return_pct']}% | "
              f"종가매도 승률 {c['win_rate']}% 평균 {c['avg_return_pct']}%")
    print("-" * 60)
    o = overall["open_sell"]
    c = overall["close_sell"]
    print(f"전체: 평가 {overall['count']}개 | "
          f"시가매도 승률 {o['win_rate']}% 평균 {o['avg_return_pct']}% | "
          f"종가매도 승률 {c['win_rate']}% 평균 {c['avg_return_pct']}%")
    print(f"JSON: {json_path}")


if __name__ == "__main__":
    main()

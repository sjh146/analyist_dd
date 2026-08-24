#!/usr/bin/env python3
"""단타스크리너 (Day-Trading Screener) — 칼만 노이즈 제거 + 챔피언 모델 · 당일 매수 후보 선별.

HFT 시장조성이 만드는 스프레드 바운스 노이즈를 칼만(RTS) 평활화로 제거한 뒤,
노이즈 없는 추세 강도 + 챔피언 모델 예측 + 거래량/변동성 정합으로 당일 매수 후보를
점수화(0~100)하여 랭킹한다. (docs/단타스크리너_PLAN.md)

전략 요약:
- 유니버스: KOSDAQ 전체 (market_data 20거래일 이상 보유)
- 노이즈 제거: 일봉 종가열 → Rauch–Tung–Striebel 칼만 평활화 (O(n))
- 점수: 칼만 추세 30 + 챔피언 모델 30 + 거래량 급증 20 + 변동성 정합 20
- 모델 미가용 시 칼만/거래량/변동성만으로 동작 (reason에 '모델미가용' 표기)

사용법:
  python3 scripts/daytrading_screener.py --top-n 20
  python3 scripts/daytrading_screener.py --date 2026-08-24 --output out.csv
"""
import argparse
import logging
import os
import sys
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

from day_trading_engine import (ChampionPredictor, DbDailyProvider,
                                OUTPUT_COLUMNS, run_screener)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("daytrading_screener")

PG_HOST = os.environ.get("POSTGRES_HOST", "127.0.0.1")
PG_PORT = int(os.environ.get("POSTGRES_PORT", 5432))
PG_DB = os.environ.get("POSTGRES_DB", "stock_trading")
PG_USER = os.environ.get("POSTGRES_USER", "stock_user")
PG_PASS = os.environ.get("POSTGRES_PASSWORD", "")


def write_csv(rows, path):
    """CSV 저장 (부모 디렉토리 생성, utf-8, 컬럼 순서 고정)."""
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    df = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    df.to_csv(path, index=False, encoding="utf-8")
    return path


def build_output_rows(df):
    """출력용 dict 리스트 (컬럼 순서 고정, 라운딩)."""
    rows = []
    for _, r in df.iterrows():
        prob = r.get("model_prob")
        prob_str = "" if prob is None or (isinstance(prob, float) and pd.isna(prob)) \
            else f"{float(prob):.3f}"
        rows.append({
            "rank": int(r["rank"]),
            "stock_code": r["stock_code"],
            "stock_name": r.get("stock_name", ""),
            "sector": r.get("sector", ""),
            "signal_date": str(r["signal_date"]),
            "close_price": round(float(r["close_price"]), 2),
            "score": round(float(r["score"]), 1),
            "kalman_trend": round(float(r["kalman_trend"]) * 1000.0, 2),
            "kalman_slope": round(float(r["kalman_slope"]) * 1000.0, 3),
            "noise_resid_std": round(float(r["noise_resid_std"]), 4),
            "volume_surge": round(float(r["volume_surge"]), 2),
            "volatility_ann": round(float(r["volatility_ann"]), 3),
            "model_prob": prob_str,
            "reason": r["reason"],
        })
    return rows


def main():
    ap = argparse.ArgumentParser(
        description="단타스크리너 — 칼만 노이즈 제거 + 챔피언 모델 기반 당일 매수 후보 선별")
    ap.add_argument("--top-n", type=int, default=20, help="상위 N개 후보 (기본 20)")
    ap.add_argument("--date", type=str, default=None, help="시그널 기준일 YYYY-MM-DD (기본: 최근 거래일)")
    ap.add_argument("--output", type=str, default=None, help="출력 CSV 경로")
    ap.add_argument("--min-trading-value", type=float, default=300_000_000, help="최소 거래대금 (기본 3억)")
    ap.add_argument("--min-price", type=float, default=1000, help="최소 종가 (기본 1000원)")
    ap.add_argument("--lookback", type=int, default=20, help="칼만 평활화 룩백 거래일 (기본 20)")
    args = ap.parse_args()

    pg = None
    try:
        import psycopg2
        pg = psycopg2.connect(host=PG_HOST, port=PG_PORT, dbname=PG_DB,
                              user=PG_USER, password=PG_PASS)
    except Exception as e:
        logger.error(f"PostgreSQL 연결 실패: {e}")
        print("\nDB 연결 실패 — Docker 컨테이너가 down 상태입니다. 컨테이너를 올린 뒤 재실행하세요.")
        sys.exit(1)

    try:
        provider = DbDailyProvider(pg_conn=pg)
        predictor = ChampionPredictor()
        if not predictor.available:
            logger.warning("챔피언 모델 미가용 — 칼만/거래량/변동성 점수만 사용")

        ranked = run_screener(
            provider,
            top_n=args.top_n,
            lookback=args.lookback,
            min_history=20,
            min_trading_value=args.min_trading_value,
            min_price=args.min_price,
            date_str=args.date,
            predictor=predictor,
        )

        rows = build_output_rows(ranked)
        if not rows:
            logger.warning("후보 없음 — 조건을 만족하는 종목 없음")
            print("\n후보 없음 (필터 조건을 만족하는 종목이 없습니다).")
            return

        if args.output:
            out_path = args.output
        else:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_dir = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "..", "data", "reports"
            )
            out_path = os.path.join(out_dir, f"daytrading_candidates_{ts}.csv")
        write_csv(rows, out_path)
        logger.info(f"CSV 저장: {out_path}")

        # 콘솔 테이블
        print(f"\nTop {len(rows)} KOSDAQ Day-Trading Candidates ({rows[0]['signal_date']})")
        print(f"{'Rank':<5} {'Code':<8} {'Name':<20} {'Score':<7} {'Slope‰':<8} Reason")
        print("-" * 84)
        for r in rows:
            reason = r["reason"]
            if len(reason) > 56:
                reason = reason[:56] + "..."
            print(f"  {r['rank']:<4} {r['stock_code']:<8} {r['stock_name']:<20} "
                  f"{r['score']:<7.1f} {r['kalman_slope']:<8} {reason}")
        print(f"\n총 후보: {len(rows)}")
    finally:
        if pg is not None:
            try:
                pg.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()

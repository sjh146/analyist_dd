#!/usr/bin/env python3
"""종가스크리너 (Close-Bet Screener) — 장 마감 후 매수 후보 선별.

장 마감(종가) 시점에 확정된 일봉 데이터 + 수급 신호로 매수 후보를 선별하여
**종가 매수 → 다음 거래일 매도**하는 1일 보유 단기 매매용 스크리너.

전략 요약 (docs/close_screener_PLAN.md §4):
- 유니버스: KOSDAQ 전체 (market_data 20거래일 이상 보유 종목)
- 지표: 거래량 급증, 종가 강도, 3/5일 모멘텀, 당일 등락률, 공매도 비율
- 점수: 0~100 (거래량 25 + 종가강도 20 + 모멘텀 25 + 당일상승 10 + 공매도 20)
  + 시장 레짐 보정 ±5 (KOSPI 외국인+기관+프로그램 순매수 합)

사용법:
  python3 scripts/close_screener.py --top-n 20
  python3 scripts/close_screener.py --date 2026-08-19 --output out.csv
"""
import argparse
import logging
import os
import re
import sys
from datetime import datetime, date

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("close_screener")

PG_HOST = os.environ.get("POSTGRES_HOST", "postgres")
PG_PORT = int(os.environ.get("POSTGRES_PORT", 5432))
PG_DB = os.environ.get("POSTGRES_DB", "stock_trading")
PG_USER = os.environ.get("POSTGRES_USER", "stock_user")
PG_PASS = os.environ.get("POSTGRES_PASSWORD", "")

OUTPUT_COLUMNS = [
    "rank", "stock_code", "stock_name", "sector", "signal_date", "close_price",
    "score", "volume_surge", "close_strength", "ret_3d_pct", "ret_5d_pct",
    "day_change_pct", "short_ratio", "reason",
]


def get_pg_conn():
    """psycopg2 연결 생성 (lazy import)."""
    import psycopg2
    return psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PASS
    )


def get_kosdaq_stocks(pg_conn):
    """KOSDAQ 전체 종목 (market_data 20거래일 이상 보유) → [(code, name, sector, latest_date)]."""
    cur = pg_conn.cursor()
    cur.execute("""
        SELECT s.stock_code, s.stock_name, COALESCE(s.sector, 'Unknown') as sector,
               MAX(md.trade_date) as latest_date
        FROM stocks s
        JOIN market_data md ON s.stock_code = md.stock_code
        WHERE s.market = 'KOSDAQ'
        GROUP BY s.stock_code, s.stock_name, s.sector
        HAVING COUNT(*) >= 20
        ORDER BY s.stock_code
    """)
    rows = cur.fetchall()
    cur.close()
    return rows


def resolve_trade_date(pg_conn, date_str_or_None):
    """시그널 기준 거래일 결정 → 'YYYY-MM-DD' str.

    None → market_data의 최근 거래일 (CURRENT_DATE 이하).
    'YYYY-MM-DD' → 해당 날짜 이하의 최대 거래일 (휴장 무시).
    """
    cur = pg_conn.cursor()
    if date_str_or_None is None:
        cur.execute(
            "SELECT MAX(trade_date) FROM market_data WHERE trade_date <= CURRENT_DATE"
        )
    else:
        cur.execute(
            "SELECT MAX(trade_date) FROM market_data WHERE trade_date <= %s",
            (date_str_or_None,),
        )
    row = cur.fetchone()
    cur.close()
    if row is None or row[0] is None:
        raise ValueError("market_data에 유효한 거래일이 없습니다.")
    return str(row[0])


def load_price_history(pg_conn, trade_date_str, lookback=7):
    """종목별 최근 lookback 거래일 OHLCV 로드 (쿼리 1회).

    반환 컬럼: [stock_code(str), trade_date(datetime.date), open_price, high_price,
               low_price, close_price, volume, trading_value] (모두 float).
    """
    cur = pg_conn.cursor()
    cur.execute("""
        SELECT stock_code, trade_date,
               open_price::float8, high_price::float8, low_price::float8,
               close_price::float8, volume::float8, trading_value::float8
        FROM (
            SELECT stock_code, trade_date, open_price, high_price, low_price,
                   close_price, volume, trading_value,
                   ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY trade_date DESC) AS rn
            FROM market_data
            WHERE trade_date <= %s
        ) t
        WHERE rn <= %s
        ORDER BY stock_code, trade_date
    """, (trade_date_str, lookback))
    rows = cur.fetchall()
    cur.close()
    df = pd.DataFrame(rows, columns=[
        "stock_code", "trade_date", "open_price", "high_price",
        "low_price", "close_price", "volume", "trading_value",
    ])
    df["stock_code"] = df["stock_code"].astype(str)
    for col in ["open_price", "high_price", "low_price", "close_price",
                "volume", "trading_value"]:
        df[col] = df[col].astype(float)
    return df


def load_short_ratios(pg_conn, trade_date_str, lookback_days=5):
    """종목별 최근 lookback_days 거래일 공매도 비율 평균 → [stock_code, short_ratio(float)]."""
    cur = pg_conn.cursor()
    cur.execute("""
        SELECT stock_code, AVG(short_ratio)::float8 AS short_ratio
        FROM krx_short_selling
        WHERE trade_date > %s::date - %s::int
          AND trade_date <= %s
          AND stock_code ~ '^[0-9]+$'
        GROUP BY stock_code
    """, (trade_date_str, lookback_days, trade_date_str))
    rows = cur.fetchall()
    cur.close()
    df = pd.DataFrame(rows, columns=["stock_code", "short_ratio"])
    df["stock_code"] = df["stock_code"].astype(str)
    df["short_ratio"] = df["short_ratio"].astype(float)
    return df


def load_market_regime(pg_conn, trade_date_str):
    """KOSPI 시장 레짐 → {'investor_net': float, 'program_net': float}.

    investor_net = 외국인+기관 순매수 합 (최근 3일 윈도우).
    program_net = 프로그램 순매수 (최근 1일). 데이터 없으면 0.0.
    """
    regime = {"investor_net": 0.0, "program_net": 0.0}

    # 외국인 + 기관 순매수 합 (최근 3일 윈도우)
    try:
        cur = pg_conn.cursor()
        cur.execute("""
            SELECT COALESCE(SUM(net_buy), 0)::float8
            FROM krx_trading
            WHERE market = 'KOSPI'
              AND investor_type IN ('Foreign', 'Institution')
              AND trade_date <= %s
              AND trade_date >= %s::date - 3
        """, (trade_date_str, trade_date_str))
        row = cur.fetchone()
        cur.close()
        regime["investor_net"] = float(row[0]) if row and row[0] is not None else 0.0
    except Exception as e:
        logger.warning(f"investor_net 조회 실패(0.0 처리): {e}")
        regime["investor_net"] = 0.0

    # 프로그램 순매수 (최근 가용일 1일 값 — 기간 합산 금지: 원 데이터가 스냅샷 성격)
    try:
        cur = pg_conn.cursor()
        cur.execute("""
            SELECT COALESCE(net_buy, 0)::float8
            FROM krx_program_trading
            WHERE market = 'KOSPI'
              AND trade_date = (
                  SELECT MAX(trade_date) FROM krx_program_trading
                  WHERE market = 'KOSPI' AND trade_date <= %s
              )
        """, (trade_date_str,))
        row = cur.fetchone()
        cur.close()
        regime["program_net"] = float(row[0]) if row and row[0] is not None else 0.0
    except Exception as e:
        logger.warning(f"program_net 조회 실패(0.0 처리): {e}")
        regime["program_net"] = 0.0

    return regime


def compute_indicators(df, lookback=7):
    """종목별 지표 계산 → 종목당 1행 (시그널일 = 최근 거래일).

    추가 컬럼: volume_surge, close_strength, ret_3d, ret_5d, day_change.
    윈도우 내 행 수가 lookback 미만인 종목은 제외.
    """
    out = []
    for code, grp in df.groupby("stock_code"):
        grp = grp.sort_values("trade_date")
        if len(grp) < lookback:
            continue
        g = grp.iloc[-1]      # r6 = 당일
        prev = grp.iloc[-6:-1]  # v1..v5 (직전 5거래일)
        # iloc[-6]=r1(5거래일 전), iloc[-4]=r3(3거래일 전), iloc[-2]=r5(전일)
        c1 = float(grp.iloc[-6]["close_price"])  # r1
        c3 = float(grp.iloc[-4]["close_price"])  # r3
        c5 = float(grp.iloc[-2]["close_price"])  # r5
        c6 = float(g["close_price"])
        v6 = float(g["volume"])
        h6 = float(g["high_price"])
        l6 = float(g["low_price"])

        mean_prev_vol = float(prev["volume"].mean())
        if mean_prev_vol and mean_prev_vol > 0:
            volume_surge = v6 / mean_prev_vol
        else:
            volume_surge = np.nan

        if h6 == l6:
            close_strength = 0.5
        else:
            close_strength = (c6 - l6) / (h6 - l6)

        ret_3d = c6 / c3 - 1.0 if c3 else np.nan
        ret_5d = c6 / c1 - 1.0 if c1 else np.nan
        day_change = c6 / c5 - 1.0 if c5 else np.nan

        out.append({
            "stock_code": code,
            "trade_date": g["trade_date"],
            "open_price": float(g["open_price"]),
            "high_price": h6,
            "low_price": l6,
            "close_price": c6,
            "volume": v6,
            "trading_value": float(g["trading_value"]),
            "volume_surge": volume_surge,
            "close_strength": close_strength,
            "ret_3d": ret_3d,
            "ret_5d": ret_5d,
            "day_change": day_change,
        })
    return pd.DataFrame(out)


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def score_candidates(df, regime=None):
    """점수(0~100) + reason 문자열 부여 (docs/close_screener_PLAN.md §4.3)."""
    rows = []
    for _, r in df.iterrows():        # 거래량 급증 (NaN → 0점)
        surge = r.get("volume_surge")
        if surge is None or (isinstance(surge, float) and np.isnan(surge)):
            vol_pts = 0.0
        else:
            vol_pts = min(float(surge) / 3.0, 1.0) * 25.0

        strength_pts = float(r.get("close_strength", 0.0)) * 20.0

        ret_3d = r.get("ret_3d")
        ret_5d = r.get("ret_5d")
        ret_3d = 0.0 if ret_3d is None or (isinstance(ret_3d, float) and np.isnan(ret_3d)) else float(ret_3d)
        ret_5d = 0.0 if ret_5d is None or (isinstance(ret_5d, float) and np.isnan(ret_5d)) else float(ret_5d)
        momentum_pts = 12.5 + _clamp(ret_3d / 0.06, -1.0, 1.0) * 7.5 + _clamp(ret_5d / 0.10, -1.0, 1.0) * 5.0

        day_change = r.get("day_change")
        day_change = 0.0 if day_change is None or (isinstance(day_change, float) and np.isnan(day_change)) else float(day_change)
        day_pts = _clamp(day_change / 0.05, 0.0, 1.0) * 10.0

        # 공매도 (NaN → 중립 10점)
        short_ratio = r.get("short_ratio")
        has_short = short_ratio is not None and not (isinstance(short_ratio, float) and np.isnan(short_ratio))
        if not has_short:
            short_pts = 10.0
        else:
            short_pts = _clamp(1.0 - float(short_ratio) / 0.03, 0.0, 1.0) * 20.0

        base = vol_pts + strength_pts + momentum_pts + day_pts + short_pts

        # 시장 레짐 보정
        regime_adj = 0.0
        if regime is not None:
            combined = float(regime.get("investor_net", 0.0)) + float(regime.get("program_net", 0.0))
            if combined > 0:
                regime_adj = 5.0
            elif combined < 0:
                regime_adj = -5.0

        score = _clamp(base + regime_adj, 0.0, 100.0)

        # reason 문자열 (결정적, 테스트 친화적)
        parts = []
        if not (surge is None or (isinstance(surge, float) and np.isnan(surge))):
            parts.append(f"거래량 {surge:.1f}배")
        parts.append(f"종가강도 {close_strength_pct(r.get('close_strength')):.0f}%")
        parts.append(f"3일 {ret_3d * 100:+.1f}%")
        if has_short:
            parts.append(f"공매도 {float(short_ratio) * 100:.1f}%")
        if regime_adj > 0:
            parts.append("시장레짐+")
        elif regime_adj < 0:
            parts.append("시장레짐-")
        reason = ", ".join(parts)

        out_row = {
            "stock_code": r["stock_code"],
            "trade_date": r["trade_date"],
            "open_price": r["open_price"],
            "high_price": r["high_price"],
            "low_price": r["low_price"],
            "close_price": r["close_price"],
            "volume": r["volume"],
            "trading_value": r["trading_value"],
            "volume_surge": r["volume_surge"],
            "close_strength": r["close_strength"],
            "ret_3d": r["ret_3d"],
            "ret_5d": r["ret_5d"],
            "day_change": r["day_change"],
            "short_ratio": r.get("short_ratio"),
            "score": score,
            "reason": reason,
        }
        # 메타 컬럼(stock_name/sector) 보존 — 출력 CSV에 필요
        for extra_col in ("stock_name", "sector"):
            if extra_col in df.columns:
                out_row[extra_col] = r[extra_col]
        rows.append(out_row)
    if not rows:
        return pd.DataFrame(columns=[
            "stock_code", "trade_date", "open_price", "high_price", "low_price",
            "close_price", "volume", "trading_value", "volume_surge",
            "close_strength", "ret_3d", "ret_5d", "day_change",
            "short_ratio", "score", "reason",
        ])
    return pd.DataFrame(rows)


def close_strength_pct(v):
    """종가강도를 퍼센트로 (0.92 → 92)."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return 0.0
    return float(v) * 100.0


def filter_candidates(df, min_trading_value=300_000_000, min_price=1000):
    """하드 필터: 거래대금, 최저가, 거래량>0, 지표 결측 제외.

    trading_value가 결측인 최근 데이터(수집 공백)를 대비해
    close_price × volume 근사값으로 폴백한다.
    """
    required = ["volume_surge", "close_strength", "ret_3d", "ret_5d", "day_change"]
    mask = pd.Series(True, index=df.index)
    for col in required:
        mask &= df[col].notna()
    tv = df["trading_value"]
    effective_tv = tv.where(tv.notna(), df["close_price"] * df["volume"])
    mask &= effective_tv >= min_trading_value
    mask &= df["close_price"] >= min_price
    mask &= df["volume"] > 0
    return df[mask].copy()


def rank_candidates(df, top_n=20):
    """score 내림차순 정렬 + rank 1..n + top_n 절단."""
    if len(df) == 0:
        out = df.copy()
        out["rank"] = []
        return out
    out = df.sort_values("score", ascending=False).reset_index(drop=True)
    out = out.head(top_n).copy()
    out["rank"] = range(1, len(out) + 1)
    return out


def build_output_rows(df):
    """출력용 dict 리스트 (컬럼 순서 고정)."""
    rows = []
    for _, r in df.iterrows():
        short_ratio = r.get("short_ratio")
        if short_ratio is None or (isinstance(short_ratio, float) and np.isnan(short_ratio)):
            short_str = ""
        else:
            short_str = f"{float(short_ratio):.3f}"
        rows.append({
            "rank": int(r["rank"]),
            "stock_code": r["stock_code"],
            "stock_name": r.get("stock_name", ""),
            "sector": r.get("sector", ""),
            "signal_date": str(r["trade_date"]),
            "close_price": round(float(r["close_price"]), 2),
            "score": round(float(r["score"]), 1),
            "volume_surge": round(float(r["volume_surge"]), 2),
            "close_strength": round(float(r["close_strength"]), 3),
            "ret_3d_pct": round(float(r["ret_3d"]) * 100.0, 2),
            "ret_5d_pct": round(float(r["ret_5d"]) * 100.0, 2),
            "day_change_pct": round(float(r["day_change"]) * 100.0, 2),
            "short_ratio": short_str,
            "reason": r["reason"],
        })
    return rows


def write_csv(rows, path):
    """CSV 저장 (부모 디렉토리 생성, utf-8, 컬럼 순서 고정)."""
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    df = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    df.to_csv(path, index=False, encoding="utf-8")
    return path


def parse_signal_date_from_filename(name_or_path):
    """파일명 *_YYYYMMDD_HHMMSS.csv 에서 날짜 파싱 → datetime.date."""
    base = os.path.basename(name_or_path)
    m = re.search(r"_(\d{8})_\d{6}\.csv$", base)
    if not m:
        raise ValueError(f"파일명에서 날짜를 파싱할 수 없습니다: {name_or_path}")
    return datetime.strptime(m.group(1), "%Y%m%d").date()


def main():
    ap = argparse.ArgumentParser(description="종가스크리너 — 장 마감 후 매수 후보 선별")
    ap.add_argument("--top-n", type=int, default=20, help="상위 N개 후보 (기본 20)")
    ap.add_argument("--date", type=str, default=None, help="시그널 기준일 YYYY-MM-DD (기본: 최근 거래일)")
    ap.add_argument("--output", type=str, default=None, help="출력 CSV 경로")
    ap.add_argument("--min-trading-value", type=float, default=300_000_000, help="최소 거래대금 (기본 3억)")
    ap.add_argument("--min-price", type=float, default=1000, help="최소 종가 (기본 1000원)")
    args = ap.parse_args()

    pg = get_pg_conn()
    try:
        trade_date = resolve_trade_date(pg, args.date)
        logger.info(f"시그널 기준일: {trade_date}")

        stocks = get_kosdaq_stocks(pg)
        logger.info(f"KOSDAQ 유니버스: {len(stocks)} 종목")

        price_df = load_price_history(pg, trade_date, lookback=7)
        logger.info(f"가격 이력 로드: {len(price_df)} 행")

        # 유니버스(KOSDAQ) 외 종목 제외 — market_data에는 KOSPI도 있음
        universe_codes = {code for code, _, _, _ in stocks}
        price_df = price_df[price_df["stock_code"].isin(universe_codes)]
        logger.info(f"유니버스 필터 후: {price_df['stock_code'].nunique()} 종목")

        ind_df = compute_indicators(price_df, lookback=7)
        logger.info(f"지표 계산 완료: {len(ind_df)} 종목")

        short_df = load_short_ratios(pg, trade_date, lookback_days=5)
        ind_df = ind_df.merge(short_df, on="stock_code", how="left")

        # 종목명/섹터 매핑
        stock_meta = {code: (name, sector) for code, name, sector, _ in stocks}
        ind_df["stock_name"] = ind_df["stock_code"].map(lambda c: stock_meta.get(c, ("", ""))[0])
        ind_df["sector"] = ind_df["stock_code"].map(lambda c: stock_meta.get(c, ("", ""))[1])

        filtered = filter_candidates(
            ind_df, min_trading_value=args.min_trading_value, min_price=args.min_price
        )
        logger.info(f"하드 필터 통과: {len(filtered)} 종목")

        if filtered.empty:
            logger.warning("필터 통과 종목 없음 — CSV 생성 생략")
            print("\n후보 없음 (필터 조건을 만족하는 종목이 없습니다).")
            return

        regime = load_market_regime(pg, trade_date)
        logger.info(f"시장 레짐: {regime}")

        scored = score_candidates(filtered, regime=regime)
        ranked = rank_candidates(scored, top_n=args.top_n)
        rows = build_output_rows(ranked)

        if not rows:
            logger.warning("후보 없음 — 빈 결과 처리")
            print("\n후보 없음 (필터 조건을 만족하는 종목이 없습니다).")
            pg.close()
            return

        if args.output:
            out_path = args.output
        else:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_dir = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "..", "data", "reports"
            )
            out_path = os.path.join(out_dir, f"close_candidates_{ts}.csv")
        write_csv(rows, out_path)
        logger.info(f"CSV 저장: {out_path}")

        # 콘솔 테이블
        print(f"\nTop {len(rows)} KOSDAQ Close Candidates ({trade_date})")
        print(f"{'Rank':<5} {'Code':<8} {'Name':<20} {'Score':<7} Reason")
        print("-" * 80)
        for r in rows:
            reason = r["reason"]
            if len(reason) > 50:
                reason = reason[:50] + "..."
            print(f"  {r['rank']:<4} {r['stock_code']:<8} {r['stock_name']:<20} "
                  f"{r['score']:<7.1f} {reason}")
        print(f"\n총 후보: {len(rows)} / 유니버스: {len(stocks)}")
    finally:
        pg.close()


if __name__ == "__main__":
    main()

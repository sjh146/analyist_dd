#!/usr/bin/env python3
"""build_supply_market_features — 수급·시장·모멘텀 피처 19개를 종목×거래일 격자로 적재 (R10).

WHY (2026-09-25 실측): feature_coverage 에서 아래 19개가 nonzero_ratio=0 이었다:
  institution_net_buy(_5d), foreign_net_buy(_5d), ownership_pct 계열 3개,
  short_interest_ratio, short_selling_ratio, days_to_cover,
  momentum_3_12m, momentum_ni, momentum_op,
  krx_advance_decline_ratio, krx_total_trading_value, market_breadth,
  market_impact_score, relative_strength, bb_position
원천은 이미 보유 중인데(조회: `docker exec stock_postgres psql -U stock_user -d stock_trading
-tAc "SELECT COUNT(*) FROM ..."` → foreign_institutional 87,895행 / krx_short_selling 2,393행
/ market_data 1,113,634행 / ownership 225행 / krx_trading 504행 / financial_statements
10,370행 / news_events 4,672행) 원천→피처 변환 코드가 없었다. 수급·모멘텀은 가격 피처와
독립 신호다(DEAD_FEATURE_REVIVAL.md #4·#5).

원천 실측 한계 (2026-09-25 조회, 0 이 아니라 NULL 로 정직하게 남긴다):
  · krx_short_selling.balance_quantity → 2,393행 전부 NULL(조회: COUNT(*) FILTER (WHERE
    balance_quantity IS NOT NULL) = 0) ⇒ short_interest_ratio·days_to_cover 는 원천이
    채워지기 전까지 NULL(계산 코드는 준비해 둠 — 백필되면 재실행으로 살아난다).
  · ownership.institution_ownership_pct → 225행 전부 NULL(같은 조회 = 0) ⇒
    institution_ownership_pct 는 NULL 유지. retail_ownership_pct 는 파이프라인 정의
    (feature_pipeline.py:605)대로 COALESCE(기관,0) → 100-외국인-기관(개인 지분 상한 근사).
  · ownership 는 2026-09-23 하루치(225종목)뿐 ⇒ 지분율 계열은 마지막 거래일만 값이 있다.

as-of 규율 (미래 누수 없음):
  · 격자 = market_data 의 (stock_code, trade_date). 모든 계산은 trade_date <= D 의
    정보만 사용한다.
  · 수급: foreign_institutional 당일 행 = net_buy, _5d = 당일 포함 직전 5행(5거래일) SUM.
    (파이프라인의 np.mean 과 다른 '누적' — 스펙 지시 "5일 누적 순매수"를 따른다.)
  · 모멘텀: close[D]/close[D-63] - 1, close[D]/close[D-252] - 1. 이력 부족(<63/252행)은
    NULL — 파이프라인의 "12m 부족 시 3m 대체(=momentum_3_12m 을 0으로 위장)" 폴백은
    '정보 없음'을 0으로 만들므로 쓰지 않는다(스펙: 결측은 0과 구분).
  · momentum_ni/op: financial_statements 를 report_date <= D 로 자르고 최신 2개 보고서의
    변화율. (기간유형 연간/반기 구분은 R2 소관 — 여기선 report_date 순 최신 2개.)
  · 공매도 비율: krx_short_selling 당일 행 short_volume/total_volume.
  · 뉴스 충격도: news_event_features.py market_impact_score 정의를 그대로 —
    anchor = D 23:59:59.999999, 최근 24h 건수 / 과거 7d 일평균 서지 × (1+최근중요도합)
    × (1+평균신선도+평균중요도), 상한 100. 최근 활동이 없으면 NULL(파이프라인은 0.0 —
    표에서는 정보 없음과 구분).
  · 시장레벨 피처: breadth = 당일 상승종목/유효종목, ADR = 상승/하락(하락 0이면 NULL),
    krx_total_trading_value = krx_trading(KOSPI·Total) 당일 거래대금.

멱등성: 재실행 안전 — supply_market_features 는 이 빌더 전용이므로 DELETE 후 COPY.
feature_coverage 는 19개 행을 ON CONFLICT(feature_name) DO UPDATE 로 갱신(계산식은
feature_coverage_report.compute_coverage 와 동일 정의, 격자 전체를 분모로 실측).

자기신고: dq_claim.record_claim(소스 격자 행수 / 생성·적재 행수 / 저장 후 테이블 행수).
  source_rows>0 이고 persisted==0 이면 exit 4 (2026-09-24 파서 키 불일치 유형 재발 방지).

주의 — feature_coverage 스냅샷과의 관계: feature_coverage_report.py 는 훈련 패널(파이프라인
내 계산)에서 커버리지를 뽑는데, 그 연결은 R14 소관이다(QUANT_RESEARCH_BACKLOG.json R10.note).
이 빌더는 **피처 테이블 실측값**으로 19개 행의 커버리지를 직접 갱신한다(R10 method
"피처 테이블 적재 → feature_coverage 재계산"). R14 가 패널 경유 스냅샷을 가동하면 이
빌더를 재실행해 실측값을 재확정할 수 있다(멱등).

사용 (호스트):
  cd /home/jhshi/analyist_dd
  set -a && . ./.env && set +a
  export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434
  /usr/bin/python3 scripts/build_supply_market_features.py
  /usr/bin/python3 scripts/build_supply_market_features.py --dry-run --limit-stocks 50
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import psycopg2  # noqa: E402

PG = dict(host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
          port=int(os.environ.get("POSTGRES_PORT", "5434")),
          dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
          user=os.environ.get("POSTGRES_USER", "stock_user"),
          password=os.environ.get("POSTGRES_PASSWORD", ""))

TABLE = "supply_market_features"
DDL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "init-scripts", "postgres", "18_supply_market_features.sql")

FEATURES = [
    "institution_net_buy", "institution_net_buy_5d",
    "foreign_net_buy", "foreign_net_buy_5d",
    "foreign_ownership_pct", "institution_ownership_pct", "retail_ownership_pct",
    "short_interest_ratio", "short_selling_ratio", "days_to_cover",
    "momentum_3_12m", "momentum_ni", "momentum_op",
    "krx_advance_decline_ratio", "krx_total_trading_value", "market_breadth",
    "market_impact_score", "relative_strength", "bb_position",
]

# 파이프라인과 동일한 유효성 필터(feature_pipeline.MARKET_DATA_VALID)
MARKET_DATA_VALID = "NOT (open_price = 0 AND high_price = 0 AND low_price = 0)"

WIN_5D = 5        # 수급 5거래일 누적
MOM_3M, MOM_12M = 63, 252     # 거래일 기준 3/12개월
BB_N = 20         # 볼린저 창
VOL_N = 20        # days_to_cover 평균거래량 창
IMPACT_MAX = 100.0  # market_impact_score 상한(news_event_features.MAX_IMPACT_SCORE)


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def load_market_data(cur, since, limit_stocks=0):
    """시세 격자 로드(종목·거래일) — 모든 피처의 기준 격자이자 분모."""
    q = (f"SELECT stock_code, trade_date, open_price, high_price, low_price, "
         f"close_price, volume FROM market_data WHERE trade_date >= %s "
         f"AND {MARKET_DATA_VALID}")
    params = [since]
    if limit_stocks:
        q += (" AND stock_code IN (SELECT stock_code FROM market_data GROUP BY 1 "
              f"ORDER BY 1 LIMIT {int(limit_stocks)})")
    cur.execute(q + " ORDER BY stock_code, trade_date", params)
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    for c in ("open_price", "high_price", "low_price", "close_price", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def price_features(df):
    """종목별: 1일 수익률, 3/12개월 모멘텀, 볼린저 %B, 20일 평균거래량."""
    g = df.groupby("stock_code", sort=False)
    prev = g["close_price"].shift(1)
    ok = (df["close_price"] > 0) & (prev > 0)
    df["_ret"] = np.where(ok, df["close_price"] / prev - 1, np.nan)
    c3 = g["close_price"].shift(MOM_3M)
    c12 = g["close_price"].shift(MOM_12M)
    df["_ret_3m"] = np.where((df["close_price"] > 0) & (c3 > 0),
                             df["close_price"] / c3 - 1, np.nan)
    df["_ret_12m"] = np.where((df["close_price"] > 0) & (c12 > 0),
                              df["close_price"] / c12 - 1, np.nan)
    roll_mean = (df.groupby("stock_code", sort=False)["close_price"]
                 .rolling(BB_N, min_periods=BB_N).mean().reset_index(level=0, drop=True))
    roll_std = (df.groupby("stock_code", sort=False)["close_price"]
                .rolling(BB_N, min_periods=BB_N).std(ddof=0).reset_index(level=0, drop=True))
    upper, lower = roll_mean + 2 * roll_std, roll_mean - 2 * roll_std
    width = upper - lower
    df["_bb"] = np.where(width > 0, (df["close_price"] - lower) / width, np.nan)
    df["_vol20"] = (df.groupby("stock_code", sort=False)["volume"]
                    .rolling(VOL_N, min_periods=VOL_N).mean().reset_index(level=0, drop=True))
    return df


def market_level_features(df):
    """거래일별 시장레벨: 동일가중 시장수익률, 상승/하락/유효 종목수, ADR, breadth."""
    valid = df["_ret"].notna()
    per_date = (df.loc[valid].groupby("trade_date")["_ret"]
                .agg(mkt_ret="mean", adv=lambda s: int((s > 0).sum()),
                     dec=lambda s: int((s < 0).sum()), tot="size"))
    per_date["breadth"] = per_date["adv"] / per_date["tot"]
    per_date["adr"] = np.where(per_date["dec"] > 0,
                               per_date["adv"] / per_date["dec"], np.nan)
    return per_date.reset_index()


def supply_features(cur, grid, since):
    """foreign_institutional → net_buy(당일) + 5거래일 누적 SUM."""
    cur.execute(
        "SELECT stock_code, trade_date, foreign_net_buy, institution_net_buy "
        "FROM foreign_institutional WHERE trade_date >= %s", (since,))
    fi = pd.DataFrame(cur.fetchall(),
                      columns=["stock_code", "trade_date", "foreign_net_buy",
                               "institution_net_buy"])
    for c in ("foreign_net_buy", "institution_net_buy"):
        fi[c] = pd.to_numeric(fi[c], errors="coerce")
    g = fi.groupby("stock_code", sort=False)
    fi["foreign_net_buy_5d"] = g["foreign_net_buy"].rolling(WIN_5D, min_periods=1).sum().reset_index(level=0, drop=True)
    fi["institution_net_buy_5d"] = g["institution_net_buy"].rolling(WIN_5D, min_periods=1).sum().reset_index(level=0, drop=True)
    return fi.drop_duplicates(["stock_code", "trade_date"], keep="first").set_index(
        ["stock_code", "trade_date"])


def ownership_features(cur, grid):
    """ownership → 외국인/기관/개인 보유비율 (as-of: trade_date 이하 최신 행).

    pandas 2.1.4 merge_asof 는 객체 dtype by 키에서 "left keys must be sorted" 를
    내는 실측 버그가 있어(재현: 소규모 정렬 프레임에서 확인), 종목별 searchsorted 로
    직접 as-of 조인한다(ownership 225행이라 비용 무시 가능).
    """
    cur.execute("SELECT stock_code, trade_date, foreign_ownership_pct, "
                "institution_ownership_pct, listed_shares FROM ownership")
    ow = pd.DataFrame(cur.fetchall(), columns=[
        "stock_code", "trade_date", "foreign_ownership_pct",
        "institution_ownership_pct", "listed_shares"])
    for c in ("foreign_ownership_pct", "institution_ownership_pct", "listed_shares"):
        ow[c] = pd.to_numeric(ow[c], errors="coerce")
    ow = ow.sort_values(["stock_code", "trade_date"])

    cols = ("foreign_ownership_pct", "institution_ownership_pct", "listed_shares")
    arr = {c: np.full(len(grid), np.nan) for c in cols}
    codes = grid.index.get_level_values("stock_code").to_numpy()
    dates = pd.to_datetime(grid.index.get_level_values("trade_date")).to_numpy()
    for code, g in ow.groupby("stock_code", sort=False):
        mask = codes == code
        if not mask.any():
            continue
        od = pd.to_datetime(g["trade_date"]).to_numpy()
        idx = np.searchsorted(od, dates[mask], side="right") - 1  # 최신 행 <= D
        safe = np.where(idx >= 0, idx, 0)
        for c in cols:
            vals = g[c].to_numpy(dtype=float)
            arr[c][mask] = np.where(idx >= 0, vals[safe], np.nan)
    out = pd.DataFrame(arr, index=grid.index)
    # 개인 = 100 - 외국인 - 기관. 기관 pct 는 원천에서 전부 NULL(실측)이라 COALESCE 0
    # (파이프라인 feature_pipeline.py:605 와 동일 정의 — 개인 지분의 상한 근사).
    out["retail_ownership_pct"] = np.where(
        out["foreign_ownership_pct"].notna(),
        100.0 - out["foreign_ownership_pct"]
        - out["institution_ownership_pct"].fillna(0.0), np.nan)
    return out[["foreign_ownership_pct", "institution_ownership_pct",
                "retail_ownership_pct", "listed_shares"]]


def short_features(cur, grid, since):
    """krx_short_selling → 공매도 비율 / 잔고 비율 / days_to_cover."""
    cur.execute("SELECT stock_code, trade_date, short_volume, total_volume, "
                "balance_quantity FROM krx_short_selling WHERE trade_date >= %s",
                (since,))
    ss = pd.DataFrame(cur.fetchall(), columns=[
        "stock_code", "trade_date", "short_volume", "total_volume", "balance_quantity"])
    for c in ("short_volume", "total_volume", "balance_quantity"):
        ss[c] = pd.to_numeric(ss[c], errors="coerce")
    ss["short_selling_ratio"] = np.where(
        ss["total_volume"] > 0, ss["short_volume"] / ss["total_volume"], np.nan)
    return ss.drop_duplicates(["stock_code", "trade_date"], keep="first").set_index(
        ["stock_code", "trade_date"])[["short_selling_ratio", "balance_quantity"]]


def krx_trading_value(cur):
    """krx_trading(KOSPI·Total) 당일 거래대금 → 거래일 매핑."""
    cur.execute("SELECT trade_date, trading_value FROM krx_trading "
                "WHERE market = 'KOSPI' AND investor_type = 'Total'")
    return {r[0]: float(r[1]) for r in cur.fetchall() if r[1] is not None}


def financial_momentum(cur, grid):
    """financial_statements → momentum_ni/op (report_date <= D 최신 2개 보고서 변화율)."""
    cur.execute("SELECT stock_code, report_date, operating_profit, net_income "
                "FROM financial_statements")
    fin = pd.DataFrame(cur.fetchall(), columns=[
        "stock_code", "report_date", "operating_profit", "net_income"])
    for c in ("operating_profit", "net_income"):
        fin[c] = pd.to_numeric(fin[c], errors="coerce")
    fin = fin.sort_values(["stock_code", "report_date"])

    out = {c: np.full(len(grid), np.nan) for c in ("momentum_ni", "momentum_op")}
    codes = grid.index.get_level_values("stock_code").to_numpy()
    dates = pd.to_datetime(grid.index.get_level_values("trade_date")).to_numpy()
    for code, g in fin.groupby("stock_code", sort=False):
        mask = codes == code
        if not mask.any():
            continue
        rep = pd.to_datetime(g["report_date"]).to_numpy()
        for feat, col in (("momentum_ni", "net_income"), ("momentum_op", "operating_profit")):
            vals = g[col].to_numpy()
            idx = np.searchsorted(rep, dates[mask], side="right") - 1  # 최신 <= D
            prev = idx - 1
            ok = (idx >= 0) & (prev >= 0)
            if not ok.any():
                continue
            l = np.where(ok, vals[np.where(ok, idx, 0)], np.nan)
            p = np.where(ok, vals[np.where(ok, prev, 0)], np.nan)
            # np.where 는 mask 와 무관하게 양쪽 분기를 모두 평가하므로 p==0 일 때
            # RuntimeWarning 이 나온다(결과는 mask 로 NaN 처리되어 안전). 경고 노이즈 제거.
            with np.errstate(divide="ignore", invalid="ignore"):
                change = np.where((ok & (p > 0) & ~np.isnan(l)), (l - p) / p * 100.0, np.nan)
            out[feat][mask] = change
    return pd.DataFrame(out, index=grid.index)


def news_impact(cur, grid):
    """news_events + news_event_extraction → market_impact_score (파이프라인 정의)."""
    cur.execute("SELECT stock_code, last_article_at, article_count, total_importance "
                "FROM news_events WHERE last_article_at IS NOT NULL")
    ne = pd.DataFrame(cur.fetchall(), columns=[
        "stock_code", "last_article_at", "article_count", "total_importance"])
    cur.execute("SELECT stock_code, created_at, novelty, importance "
                "FROM news_event_extraction WHERE created_at IS NOT NULL")
    nx = pd.DataFrame(cur.fetchall(), columns=[
        "stock_code", "created_at", "novelty", "importance"])
    if ne.empty:
        return pd.Series(np.nan, index=grid.index)
    ne = ne.sort_values(["stock_code", "last_article_at"])
    nx = nx.sort_values(["stock_code", "created_at"])

    h24 = np.timedelta64(24, "h")
    d7 = np.timedelta64(7, "D")
    day_end = np.timedelta64(86_399_999_999, "us")  # 23:59:59.999999

    out = np.full(len(grid), np.nan)
    codes = grid.index.get_level_values("stock_code").to_numpy()
    dates = pd.to_datetime(grid.index.get_level_values("trade_date")).to_numpy(
        dtype="datetime64[ns]")
    for code, g in ne.groupby("stock_code", sort=False):
        mask = codes == code
        if not mask.any():
            continue
        t = g["last_article_at"].to_numpy(dtype="datetime64[ns]")
        cnt = g["article_count"].fillna(0).to_numpy(dtype=float)
        imp = g["total_importance"].fillna(0).to_numpy(dtype=float)
        nxg = nx[nx["stock_code"] == code]
        if nxg.empty:
            e_t = np.array([], dtype="datetime64[ns]")
            e_nov = e_imp = np.array([], dtype=float)
        else:
            e_t = nxg["created_at"].to_numpy(dtype="datetime64[ns]")
            e_nov = nxg["novelty"].fillna(0).to_numpy(dtype=float)
            e_imp = nxg["importance"].fillna(0).to_numpy(dtype=float)

        for j in np.where(mask)[0]:
            anchor = dates[j] + day_end
            i_hi = np.searchsorted(t, anchor, side="right")
            i_lo = np.searchsorted(t, anchor - h24, side="right")
            if i_hi - i_lo <= 0:        # 최근 24h 활동 없음 → NULL(정보 없음)
                continue
            j_hi = i_lo
            j_lo = np.searchsorted(t, anchor - d7, side="right")
            recent_cnt = cnt[i_lo:i_hi].sum()
            past_cnt = cnt[j_lo:j_hi].sum()
            past_avg = past_cnt / 7.0
            surge = recent_cnt / past_avg if past_avg > 0 else recent_cnt
            weight = 1.0 + imp[i_lo:i_hi].sum()
            k_hi = np.searchsorted(e_t, anchor, side="right")
            k_lo = np.searchsorted(e_t, anchor - h24, side="right")
            ni = 1.0
            if k_hi - k_lo > 0:
                ni += e_nov[k_lo:k_hi].mean() + e_imp[k_lo:k_hi].mean()
            score = surge * weight * ni
            out[j] = min(max(score, 0.0), IMPACT_MAX)
    return pd.Series(out, index=grid.index)


def compute_coverage_rows(grid, window_days, computed_at):
    """feature_coverage_report.compute_coverage 와 동일 정의로 격자 전체 커버리지 계산."""
    rows = []
    n = len(grid)
    codes = grid.index.get_level_values("stock_code")
    dates = grid.index.get_level_values("trade_date")
    for col in FEATURES:
        num = pd.to_numeric(grid[col], errors="coerce")
        naive = float((num != 0).sum()) / n if n else 0.0
        honest = float(((num.notna()) & (num != 0)).sum()) / n if n else 0.0
        null_ratio = float(num.isna().sum()) / n if n else 0.0
        filled = num.fillna(0.0)
        nonzero = int((filled != 0).sum())
        ratio = (nonzero / n) if n else 0.0
        std = float(filled.std(ddof=0)) if n else 0.0
        per_stock = num.groupby(codes).nunique()
        stock_unique_median = float(per_stock.median()) if len(per_stock) else 0.0
        per_date = num.groupby(dates).nunique()
        cross_const = (float(per_date.le(1).mean()) if len(per_date) else 0.0)
        rows.append({
            "feature_name": col,
            "nonzero_ratio": round(float(ratio), 6),
            "std": round(float(std), 6),
            "window_days": int(window_days),
            "nonzero_ratio_naive": round(naive, 6),
            "nonzero_ratio_honest": round(honest, 6),
            "null_ratio": round(null_ratio, 6),
            "stock_unique_median": round(stock_unique_median, 4),
            "cross_section_constant_ratio": round(cross_const, 4),
            "computed_at": computed_at,
        })
    return rows


def main():
    ap = argparse.ArgumentParser(description="수급·시장·모멘텀 피처 19개 빌더 (R10)")
    ap.add_argument("--since", default="2025-06-16", help="피처 시작일(격자 하한)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit-stocks", type=int, default=0, help="디버그용 종목 수 제한")
    a = ap.parse_args()
    t0 = time.time()

    conn = psycopg2.connect(**PG)
    cur = conn.cursor()

    # ── DDL 적용(단일 원천: init-scripts/postgres/18_*.sql) ──────────────
    with open(DDL_PATH, "r", encoding="utf-8") as f:
        cur.execute(f.read())
    conn.commit()

    # ── 1) 격자(종목×거래일, market_data 기준) ────────────────────────────
    md = load_market_data(cur, a.since, a.limit_stocks)
    if md.empty:
        log("격자가 비었다 — market_data 확인")
        return 1
    grid_rows = len(md)
    log(f"격자 {grid_rows}행 ({md['stock_code'].nunique()}종목 × "
        f"{md['trade_date'].nunique()}거래일, {md['trade_date'].min()} ~ "
        f"{md['trade_date'].max()})")

    # ── 2) 가격 기반 피처 (종목별) ────────────────────────────────────────
    md = price_features(md)
    grid = md.set_index(["stock_code", "trade_date"])

    # ── 3) 시장레벨 피처 (거래일별, 전 종목 동일 값) ───────────────────────
    mkt = market_level_features(md)
    grid = grid.join(mkt.set_index("trade_date")[["mkt_ret", "breadth", "adr"]],
                     on="trade_date")
    grid["relative_strength"] = grid["_ret"] - grid["mkt_ret"]
    grid["momentum_3_12m"] = grid["_ret_12m"] - grid["_ret_3m"]
    grid["market_breadth"] = grid["breadth"]
    grid["krx_advance_decline_ratio"] = grid["adr"]
    grid["bb_position"] = grid["_bb"]
    krx_tv = krx_trading_value(cur)
    grid["krx_total_trading_value"] = grid.index.get_level_values("trade_date").map(krx_tv)

    # ── 4) 수급 (foreign_institutional) ───────────────────────────────────
    fi = supply_features(cur, grid, a.since)
    for c in ("institution_net_buy", "institution_net_buy_5d",
              "foreign_net_buy", "foreign_net_buy_5d"):
        grid[c] = fi[c]

    # ── 5) 지분율 (ownership, as-of) ──────────────────────────────────────
    ow = ownership_features(cur, grid)
    for c in ("foreign_ownership_pct", "institution_ownership_pct",
              "retail_ownership_pct", "listed_shares"):
        grid[c] = ow[c]

    # ── 6) 공매도 (krx_short_selling) ─────────────────────────────────────
    ss = short_features(cur, grid, a.since)
    grid["short_selling_ratio"] = ss["short_selling_ratio"]
    grid["balance_quantity"] = ss["balance_quantity"]
    grid["short_interest_ratio"] = np.where(
        (grid["balance_quantity"].notna()) & (grid["listed_shares"] > 0),
        grid["balance_quantity"] / grid["listed_shares"], np.nan)
    grid["days_to_cover"] = np.where(
        (grid["balance_quantity"].notna()) & (grid["_vol20"] > 0),
        grid["balance_quantity"] / grid["_vol20"], np.nan)

    # ── 7) 재무 모멘텀 (financial_statements) ─────────────────────────────
    fin_m = financial_momentum(cur, grid)
    grid["momentum_ni"] = fin_m["momentum_ni"]
    grid["momentum_op"] = fin_m["momentum_op"]

    # ── 8) 뉴스 충격도 (news_events, as-of) ───────────────────────────────
    grid["market_impact_score"] = news_impact(cur, grid)

    grid = grid[FEATURES].copy()
    log(f"피처 계산 완료 ({time.time() - t0:.1f}s)")

    # 격자 전체 커버리지(분모 = 격자 행수) — 적재 전에 계산(0 아닌 행만 저장해도 분모 유지)
    window_days = int((grid.index.get_level_values("trade_date").max()
                       - grid.index.get_level_values("trade_date").min()).days)
    computed_at = datetime.now(timezone.utc)
    cov_rows = compute_coverage_rows(grid, window_days, computed_at)
    alive = [r for r in cov_rows if r["nonzero_ratio"] > 0]
    log(f"커버리지(격자 전체): nonzero 피처 {len(alive)}/19")
    for r in cov_rows:
        log(f"  {r['feature_name']:<28} nonzero={r['nonzero_ratio']:.4f} "
            f"null={r['null_ratio']:.4f} std={r['std']:.3f}")

    # ── 9) 적재: 19개 중 하나라도 값이 있는 행만 저장(없는 행 = 전부 NULL, LEFT JOIN 시
    #        NULL 과 동일 의미). 멱등: DELETE 후 COPY. ───────────────────────
    keep = grid[FEATURES].notna().any(axis=1)
    out = grid.loc[keep]
    # to_csv: MultiIndex(종목·일자) + 19컬럼, NaN → \N (COPY NULL). 파이썬 루프 없이 C 경로.
    buf = io.StringIO()
    buf.write(out.to_csv(sep="\t", header=False, index=True, na_rep="\\N"))
    generated = len(out)
    log(f"생성 {generated}행 (전부 NULL 인 {int((~keep).sum())}행 제외)")

    if a.dry_run:
        log("dry-run: DB 쓰기 생략")
        cur.close(); conn.close()
        return 0

    cur.execute(f"DELETE FROM {TABLE}")
    deleted = cur.rowcount
    buf.seek(0)
    cur.copy_expert(f"COPY {TABLE} (stock_code, trade_date, {', '.join(FEATURES)}) "
                    f"FROM STDIN", buf)
    conn.commit()
    cur.execute(f"SELECT COUNT(*) FROM {TABLE}")
    persisted = cur.fetchone()[0]

    # ── 10) feature_coverage 재계산(19개 행, 동일 computed_at — R10 method) ──
    cur.execute("""CREATE TABLE IF NOT EXISTS feature_coverage (
        feature_name TEXT PRIMARY KEY,
        nonzero_ratio DOUBLE PRECISION NOT NULL,
        std DOUBLE PRECISION NOT NULL,
        window_days INTEGER NOT NULL,
        computed_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
    cur.execute("""ALTER TABLE feature_coverage ADD COLUMN IF NOT EXISTS nonzero_ratio_naive DOUBLE PRECISION;
        ALTER TABLE feature_coverage ADD COLUMN IF NOT EXISTS nonzero_ratio_honest DOUBLE PRECISION;
        ALTER TABLE feature_coverage ADD COLUMN IF NOT EXISTS null_ratio DOUBLE PRECISION;
        ALTER TABLE feature_coverage ADD COLUMN IF NOT EXISTS stock_unique_median DOUBLE PRECISION;
        ALTER TABLE feature_coverage ADD COLUMN IF NOT EXISTS cross_section_constant_ratio DOUBLE PRECISION""")
    for r in cov_rows:
        cur.execute("""
            INSERT INTO feature_coverage (feature_name, nonzero_ratio, std, window_days,
                computed_at, nonzero_ratio_naive, nonzero_ratio_honest, null_ratio,
                stock_unique_median, cross_section_constant_ratio)
            VALUES (%(feature_name)s, %(nonzero_ratio)s, %(std)s, %(window_days)s,
                %(computed_at)s, %(nonzero_ratio_naive)s, %(nonzero_ratio_honest)s,
                %(null_ratio)s, %(stock_unique_median)s, %(cross_section_constant_ratio)s)
            ON CONFLICT (feature_name) DO UPDATE SET
                nonzero_ratio = EXCLUDED.nonzero_ratio, std = EXCLUDED.std,
                window_days = EXCLUDED.window_days, computed_at = EXCLUDED.computed_at,
                nonzero_ratio_naive = EXCLUDED.nonzero_ratio_naive,
                nonzero_ratio_honest = EXCLUDED.nonzero_ratio_honest,
                null_ratio = EXCLUDED.null_ratio,
                stock_unique_median = EXCLUDED.stock_unique_median,
                cross_section_constant_ratio = EXCLUDED.cross_section_constant_ratio
        """, r)
    conn.commit()
    log(f"적재 완료: 삭제 {deleted}행 → 삽입 {persisted}행 / feature_coverage 19행 갱신 "
        f"(computed_at={computed_at.isoformat()})")

    # ── 11) 자기신고 (소스 수신 / 파서 생성 / 실제 저장 3분리) ─────────────
    try:
        from dq_claim import record_claim
        record_claim(conn, "build_supply_market_features", TABLE,
                     claimed_rows=generated, persisted_rows=persisted,
                     source_rows=grid_rows,
                     note=f"grid={grid_rows} alive={len(alive)}/19 "
                          f"since={a.since} window_days={window_days} "
                          f"balance_quantity_null=all ownership_inst_pct_null=all")
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        log(f"자기신고 생략: {exc}")

    if grid_rows > 0 and persisted == 0:
        log("적재 실패(소스 있음 + 저장 0행) — exit 4")
        cur.close(); conn.close()
        return 4
    log(f"완료 ({time.time() - t0:.1f}s)")
    cur.close(); conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

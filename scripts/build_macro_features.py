#!/usr/bin/env python3
"""build_macro_features — macro_indicators·economic_events → 거시 피처 16개 적재 (R11).

WHY (2026-09-25 실측): feature_coverage 에서 아래 16개가 nonzero_ratio=0 이었다
(조회: `docker exec stock_postgres psql -U stock_user -d stock_trading -tAc "SELECT
feature_name, nonzero_ratio, computed_at FROM feature_coverage WHERE feature_name IN
('fx_usd_krw','oil_wti','interest_rate','cpi_yoy','yield_spread','cycle_up')"` →
전부 0, computed_at 2026-09-24):
  fx_usd_krw, fx_change_1m/3m, oil_wti, oil_change_1m/3m,
  interest_rate, interest_rate_change_1m/3m, cpi_yoy, ppi_yoy, yield_spread,
  economic_event_count_7d, economic_event_impact, cycle_up, cycle_down
원천은 이미 보유 중이었다(조회: `SELECT indicator_name, COUNT(*), MIN(date), MAX(date)
FROM macro_indicators GROUP BY 1` → 6,261행: USD/JPY/CNY 환율 751×3(2023-09-19~),
WTI 유가 749(2023-09-19~), 기준금리 1,361(2023-01-01~, 일별 캘린더), 국고채3년/회사채3년
913×2(2023-01-02~), CPI/PPI 36×2(2023-09-01~2026-08-01, 월별·지수 레벨). 값 NULL 0행).
부재한 것은 원천→피처 변환·적재 코드였다.

as-of 규율 (룩어헤드 금지 — R11 method "발표일 기준 as-of 조인"):
  · 일별 지표(환율·금리·유가·국고채): 거래일 D 에 관측일 <= D 의 최신 값(당일 사용).
  · 월별 지표(CPI/PPI)는 발표 지연을 반영한다. macro_indicators.date 는 관측월(매월 1일)이고
    발표일 컬럼이 없다(economic_events 는 예정 캘린더일 뿐, actual 0/291행 실측) →
    관측일 + 고정 지연으로 발표 시점을 근사한다:
      CPI: 관측일 + 35일  (한국 CPI 는 익월 첫째 주 발표 → 익월 5~6일 이후에만 보임)
      PPI: 관측일 + 55일  (한국 PPI 는 익월 셋째 주 발표 → 익월 24~25일 이후에만 보임)
    지연 이전의 거래일 행에는 그 월 값이 존재하지 않는다.
  · 변화율 창은 파이프라인 reader(macro_features.py)와 동일: 1m=30일, 3m=90일, YoY=12개월.
    이력 부족 시 NULL — "정보 없음"을 0으로 위장하지 않는다.
  · cpi_yoy/ppi_yoy: 원천이 지수 레벨(실측: CPI 2026-08-01=120.05)이므로 전년동월비
    (M)/(M-12)-1 ×100 으로 계산한다(reader 의 2026-09-24 수정과 동일 정의).
  · yield_spread: 국고채3년 − 기준금리 (%p).
  · economic_event_count_7d/impact: economic_events(291행, 2026-01-28~2027-12-25)는
    **사전 공지된 발표 캘린더** → 창 (D-6, D] 의 이벤트만 센다(미래 이벤트는 event_date > D
    로 자동 제외). 캘린더 시작 전 7일이 걸치는 D 는 창이 부분 커버 → NULL.
    impact 는 서프라이즈가 아니라 중요도 가중 강도(high=2, medium=1)다 — actual/forecast 가
    291행 전부 NULL(실측 조회: COUNT(*) FILTER (WHERE actual_value IS NOT NULL) = 0).
  · cycle_up/down: 기준금리 90일 차분 방향 one-hot(>0 인상 국면 / <0 인하 국면 / 0 보합).
    실측 변경점(2025-05-29 인하 2.50 → 2026-07-16·08-27 인상 2.75/3.00)으로
    두 국면 모두 nonzero 구간이 생긴다.

저장 형태: 거시 값은 종목 횡단면에서 동일하므로 **거래일 1열 PK** 테이블(macro_features)에
저장한다(종목×일자 격자 불필요). feature_coverage 는 R10 과 동일하게 market_data 격자
전체를 분모로 16개 행을 실측 갱신한다(cross_section_constant_ratio=1 — 의도된 설계).
※ feature_coverage_report.py(훈련 패널 경유 스냅샷)와의 연결은 R14 소관 — R11.note.

멱등성: 재실행 안전 — macro_features 는 이 빌더 전용이므로 DELETE 후 COPY.

자기신고: dq_claim.record_claim(소스 행수 = macro 6,261 + events 291 / 생성·적재 행수 /
저장 후 테이블 행수). source_rows>0 이고 persisted==0 이면 exit 4
(2026-09-24 파서 키 불일치 유형 재발 방지).

as-of 자체 검증: 적재와 별도로 SQL ground truth 대조를 수행한다(verify_macro_asof_features
[3] 방식 — fx/oil/rate/spread 는 `date<=D 최신 행`, cpi/ppi 는 `관측일+지연<=D 최신 행`).
불일치가 있으면 출력에 남기고 exit 5 (R11 성공 기준 "as-of 위반 0 유지").

사용 (호스트):
  cd /home/jhshi/analyist_dd
  set -a && . ./.env && set +a
  export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434
  /usr/bin/python3 scripts/build_macro_features.py
  /usr/bin/python3 scripts/build_macro_features.py --dry-run
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

TABLE = "macro_features"
DDL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "init-scripts", "postgres", "19_macro_features.sql")

FEATURES = [
    "fx_usd_krw", "fx_change_1m", "fx_change_3m",
    "oil_wti", "oil_change_1m", "oil_change_3m",
    "interest_rate", "interest_rate_change_1m", "interest_rate_change_3m",
    "cpi_yoy", "ppi_yoy", "yield_spread",
    "economic_event_count_7d", "economic_event_impact",
    "cycle_up", "cycle_down",
]

# 파이프라인 reader 와 동일한 창 정의(macro_features.py)
WIN_1M, WIN_3M = 30, 90          # 달력일 기준 1/3개월
# 발표 지연(관측일 + 지연일 = 발표 시점 근사). 근거는 docstring.
CPI_LAG_DAYS, PPI_LAG_DAYS = 35, 55
# economic_events 중요도 가중치
IMPACT_W = {"high": 2.0, "medium": 1.0}
EV_WIN_DAYS = 7                  # (D-6, D] 창

BASE_RATE, USD_KRW, WTI = "기준금리", "USD/KRW 환율", "WTI 유가"
CPI, PPI, GOV_3Y = "CPI", "PPI", "국고채3년"

# 파이프라인과 동일한 유효성 필터(feature_pipeline.MARKET_DATA_VALID)
MARKET_DATA_VALID = "NOT (open_price = 0 AND high_price = 0 AND low_price = 0)"


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def as_d64(series):
    """date 객체 컬럼 → datetime64[D] numpy 배열."""
    return np.asarray(pd.to_datetime(series).dt.normalize(), dtype="datetime64[D]")


def load_grid(cur, since):
    """market_data 격자(종목×거래일) — 피처 분모이자 feature_coverage 계산 기준(R10 동일)."""
    cur.execute(f"SELECT stock_code, trade_date FROM market_data "
                f"WHERE trade_date >= %s AND {MARKET_DATA_VALID} "
                f"ORDER BY trade_date", (since,))
    df = pd.DataFrame(cur.fetchall(), columns=["stock_code", "trade_date"])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df


def load_macro(cur):
    """macro_indicators 전체 → 지표명별 (날짜[datetime64[D]], 값[float64]) 정렬 배열."""
    cur.execute("SELECT indicator_name, date, value FROM macro_indicators "
                "ORDER BY indicator_name, date")
    rows = cur.fetchall()
    series = {}
    for name, d, v in rows:
        series.setdefault(name, ([], []))
        series[name][0].append(d)
        series[name][1].append(float(v) if v is not None else np.nan)
    for name in series:
        dd, vv = series[name]
        order = np.argsort(dd)
        series[name] = (np.asarray(dd, dtype="datetime64[D]")[order],
                        np.asarray(vv, dtype=float)[order])
    return series, len(rows)


def load_events(cur):
    """economic_events → (event_date 정렬, 중요도 가중치 정렬)."""
    cur.execute("SELECT event_date, importance FROM economic_events "
                "ORDER BY event_date")
    rows = cur.fetchall()
    dd = np.asarray([r[0] for r in rows], dtype="datetime64[D]")
    ww = np.asarray([IMPACT_W.get((r[1] or "").lower(), 0.0) for r in rows])
    return dd, ww, len(rows)


def asof(dates, values, targets):
    """정렬된 (dates, values) 에 대해 targets 각각의 최신 관측 <= target. 없으면 NaN."""
    idx = np.searchsorted(dates, targets, side="right") - 1
    safe = np.maximum(idx, 0)
    return np.where(idx >= 0, values[safe], np.nan)


def pct_change(now, then):
    """(now/then-1)*100 — 이력 부족/0 분모는 NULL(0으로 위장하지 않음)."""
    ok = ~np.isnan(now) & ~np.isnan(then) & (then > 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        chg = np.where(ok, (now / then - 1.0) * 100.0, np.nan)
    return chg


def monthly_yoy(obs_dates, obs_vals, lag_days, targets):
    """월별 지표 → 거래일별 전년동월비(%p). 관측일+lag 이후에만 값이 보인다.

    yoy 는 관측월 단위로 미리 계산하고(전년동월 값이 없으면 NaN), 거래일 D 에는
    pub_date <= D 인 최신 관측월의 yoy 를 붙인다(발표 지연 반영).
    """
    obs_map = dict(zip(obs_dates.astype(object), obs_vals))
    yoy = np.full(len(obs_dates), np.nan)
    for i, d in enumerate(obs_dates):
        m12 = (d.astype("datetime64[M]") - np.timedelta64(12, "M")).astype("datetime64[D]")
        prev = obs_map.get(m12.astype(object))
        if prev is not None and prev > 0 and not np.isnan(obs_vals[i]):
            yoy[i] = (obs_vals[i] / prev - 1.0) * 100.0
    pub = obs_dates + np.timedelta64(int(lag_days), "D")
    return asof(pub, yoy, targets)


def event_window(ev_dates, ev_w, targets):
    """경제 이벤트 창 (D-6, D] 건수·가중 강도. 캘린더 시작 전 7일이 걸치면 NULL."""
    if len(ev_dates) == 0:
        return np.full(len(targets), np.nan), np.full(len(targets), np.nan)
    hi = np.searchsorted(ev_dates, targets, side="right")
    lo = np.searchsorted(ev_dates, targets - np.timedelta64(EV_WIN_DAYS, "D"),
                         side="right")
    count = hi - lo
    cum_w = np.cumsum(ev_w)
    hh, ll = np.maximum(hi, 1), np.maximum(lo, 1)
    impact = cum_w[hh - 1] - cum_w[ll - 1]
    covered = (targets - np.timedelta64(EV_WIN_DAYS, "D")) >= ev_dates[0]
    return (np.where(covered, count, np.nan),
            np.where(covered, impact, np.nan))


def compute_features(series, ev_dates, ev_w, dates):
    """거래일 배열 → 16개 피처 (feature_name → float64 배열)."""
    f = {}

    fx_d, fx_v = series[USD_KRW]
    oil_d, oil_v = series[WTI]
    base_d, base_v = series[BASE_RATE]
    gov_d, gov_v = series[GOV_3Y]

    fx_now = asof(fx_d, fx_v, dates)
    f["fx_usd_krw"] = fx_now
    f["fx_change_1m"] = pct_change(fx_now, asof(fx_d, fx_v,
                                                dates - np.timedelta64(WIN_1M, "D")))
    f["fx_change_3m"] = pct_change(fx_now, asof(fx_d, fx_v,
                                                dates - np.timedelta64(WIN_3M, "D")))

    oil_now = asof(oil_d, oil_v, dates)
    f["oil_wti"] = oil_now
    f["oil_change_1m"] = pct_change(oil_now, asof(oil_d, oil_v,
                                                  dates - np.timedelta64(WIN_1M, "D")))
    f["oil_change_3m"] = pct_change(oil_now, asof(oil_d, oil_v,
                                                  dates - np.timedelta64(WIN_3M, "D")))

    base_now = asof(base_d, base_v, dates)
    f["interest_rate"] = base_now
    f["interest_rate_change_1m"] = base_now - asof(base_d, base_v,
                                                   dates - np.timedelta64(WIN_1M, "D"))
    f["interest_rate_change_3m"] = base_now - asof(base_d, base_v,
                                                   dates - np.timedelta64(WIN_3M, "D"))

    f["cpi_yoy"] = monthly_yoy(series[CPI][0], series[CPI][1], CPI_LAG_DAYS, dates)
    f["ppi_yoy"] = monthly_yoy(series[PPI][0], series[PPI][1], PPI_LAG_DAYS, dates)

    gov_now = asof(gov_d, gov_v, dates)
    f["yield_spread"] = np.where(~np.isnan(gov_now) & ~np.isnan(base_now),
                                 gov_now - base_now, np.nan)

    cnt, imp = event_window(ev_dates, ev_w, dates)
    f["economic_event_count_7d"] = cnt
    f["economic_event_impact"] = imp

    diff3 = base_now - asof(base_d, base_v, dates - np.timedelta64(WIN_3M, "D"))
    f["cycle_up"] = np.where(diff3 > 0, 1.0, 0.0)
    f["cycle_down"] = np.where(diff3 < 0, 1.0, 0.0)
    return f


def compute_coverage_rows(grid, window_days, computed_at):
    """feature_coverage_report.compute_coverage 와 동일 정의로 격자 전체 커버리지 계산(R10 동일)."""
    rows = []
    n = len(grid)
    codes = grid["stock_code"]
    dates = grid["trade_date"]
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


def asof_sweep(cur, feats, dates):
    """SQL ground truth 대조 — R11 "as-of 위반 0 유지"의 자체 검증.

    fx/oil/rate/spread: `date<=D 최신 행`(verify_macro_asof_features.py [3] 방식).
    cpi/ppi: `관측일 + 지연일 <= D 최신 행의 전년동월비`(발표 지연 규칙의 SQL 표현).
    불일치 목록을 문자열로 반환한다(비어 있으면 위반 0).
    """
    idxs = np.linspace(0, len(dates) - 1, min(8, len(dates))).astype(int)
    sweep = [str(np.datetime_as_string(dates[i], unit="D")) for i in idxs]
    checks = [
        ("fx_usd_krw", USD_KRW, 0),
        ("oil_wti", WTI, 0),
        ("interest_rate", BASE_RATE, 0),
        ("yield_spread", None, 0),
        ("cpi_yoy", CPI, CPI_LAG_DAYS),
        ("ppi_yoy", PPI, PPI_LAG_DAYS),
    ]
    viol = []
    feat_by_date = {str(np.datetime_as_string(d, unit="D")): i
                    for i, d in enumerate(dates)}
    for d in sweep:
        i = feat_by_date[d]
        for feat, ind, lag in checks:
            if ind is None:  # yield_spread = 국고채3년 - 기준금리
                cur.execute("SELECT (SELECT value FROM macro_indicators WHERE "
                            "indicator_name='국고채3년' AND date<=%s ORDER BY date "
                            "DESC LIMIT 1) - (SELECT value FROM macro_indicators WHERE "
                            "indicator_name='기준금리' AND date<=%s ORDER BY date DESC "
                            "LIMIT 1)", (d, d))
            elif lag == 0:   # 일별 지표: date <= D 최신 값
                cur.execute("SELECT value FROM macro_indicators WHERE "
                            "indicator_name=%s AND date<=%s ORDER BY date DESC LIMIT 1",
                            (ind, d))
            else:            # 월별 지표: 관측일+지연 <= D 최신 관측월의 전년동월비
                cur.execute("SELECT round((a.value/b.value-1)*100, 4) "
                            "FROM macro_indicators a JOIN macro_indicators b "
                            "ON b.indicator_name=a.indicator_name AND "
                            "b.date = ((a.date::timestamp + interval '1 month' * -12)::date) "
                            "WHERE a.indicator_name=%s AND "
                            "a.date + (%s * interval '1 day') <= %s "
                            "ORDER BY a.date DESC LIMIT 1", (ind, lag, d))
            row = cur.fetchone()
            got = feats[feat][i]
            if row is None or row[0] is None:
                if not np.isnan(got):
                    viol.append(f"{d} {feat}: builder={got} db=NULL")
                continue
            dbv = float(row[0])
            if abs(dbv - got) >= 1e-3:
                viol.append(f"{d} {feat}: builder={got} db={dbv}")
    return viol, sweep


def main():
    ap = argparse.ArgumentParser(description="거시 피처 16개 빌더 (R11)")
    ap.add_argument("--since", default="2025-06-16", help="피처 시작일(격자 하한)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    t0 = time.time()

    conn = psycopg2.connect(**PG)
    cur = conn.cursor()

    # ── DDL 적용(단일 원천: init-scripts/postgres/19_*.sql) ──────────────
    with open(DDL_PATH, "r", encoding="utf-8") as fp:
        cur.execute(fp.read())
    conn.commit()

    # ── 1) 격자(종목×거래일, market_data 기준 — R10 과 동일 분모) ───────────
    grid = load_grid(cur, a.since)
    if grid.empty:
        log("격자가 비었다 — market_data 확인")
        return 1
    grid_rows = len(grid)
    dates = np.unique(as_d64(grid["trade_date"]))
    log(f"격자 {grid_rows}행 ({grid['stock_code'].nunique()}종목 × {len(dates)}거래일, "
        f"{dates.min()} ~ {dates.max()})")

    # ── 2) 원천 로드 ───────────────────────────────────────────────────────
    series, macro_rows = load_macro(cur)
    ev_dates, ev_w, ev_rows = load_events(cur)
    log(f"원천: macro_indicators {macro_rows}행 / economic_events {ev_rows}행")

    # ── 3) 거래일별 피처 계산 ───────────────────────────────────────────────
    feats = compute_features(series, ev_dates, ev_w, dates)

    # 격자에 매핑(거래일 인덱스 경유 — 날짜 키 타입 불일치 회피)
    pos = np.searchsorted(dates, as_d64(grid["trade_date"]))
    for name in FEATURES:
        grid[name] = feats[name][pos]
    log(f"피처 계산 완료 ({time.time() - t0:.1f}s)")

    # ── 4) as-of 자체 검증(SQL ground truth) ───────────────────────────────
    viol, sweep_dates = asof_sweep(cur, feats, dates)
    if viol:
        log(f"as-of 위반 {len(viol)}건: " + "; ".join(viol[:8]))
    else:
        log(f"as-of 검증 통과 (SQL 대조 {len(sweep_dates)}일 × 6개, 위반 0)")

    # ── 5) 격자 전체 커버리지(분모 = 격자 행수, R10 동일 정의) ──────────────
    window_days = int((dates.max() - dates.min()) / np.timedelta64(1, "D"))
    computed_at = datetime.now(timezone.utc)
    cov_rows = compute_coverage_rows(grid, window_days, computed_at)
    alive = [r for r in cov_rows if r["nonzero_ratio"] > 0]
    log(f"커버리지(격자 전체): nonzero 피처 {len(alive)}/16")
    for r in cov_rows:
        log(f"  {r['feature_name']:<28} nonzero={r['nonzero_ratio']:.4f} "
            f"null={r['null_ratio']:.4f} std={r['std']:.3f}")

    if a.dry_run:
        log("dry-run: DB 쓰기 생략")
        cur.close(); conn.close()
        return 0

    # ── 6) 적재(거래일 1열, 멱등: DELETE 후 COPY) ───────────────────────────
    out = pd.DataFrame({"trade_date": dates})
    out["economic_event_count_7d"] = pd.Series(
        feats["economic_event_count_7d"]).astype("Int64")
    out["cycle_up"] = pd.Series(feats["cycle_up"]).astype("Int64")
    out["cycle_down"] = pd.Series(feats["cycle_down"]).astype("Int64")
    for name in FEATURES:
        if name not in ("economic_event_count_7d", "cycle_up", "cycle_down"):
            out[name] = feats[name]
    out = out[["trade_date"] + FEATURES]
    buf = io.StringIO()
    buf.write(out.to_csv(sep="\t", header=False, index=False, na_rep="\\N"))
    generated = len(out)

    cur.execute(f"DELETE FROM {TABLE}")
    deleted = cur.rowcount
    buf.seek(0)
    cur.copy_expert(f"COPY {TABLE} (trade_date, {', '.join(FEATURES)}) FROM STDIN", buf)
    conn.commit()
    cur.execute(f"SELECT COUNT(*) FROM {TABLE}")
    persisted = cur.fetchone()[0]

    # ── 7) feature_coverage 재계산(16개 행, 동일 computed_at — R10 method) ──
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
    log(f"적재 완료: 삭제 {deleted}행 → 삽입 {persisted}행 / feature_coverage 16행 갱신 "
        f"(computed_at={computed_at.isoformat()})")

    # ── 8) 자기신고 (소스 수신 / 파서 생성 / 실제 저장 3분리) ─────────────
    try:
        from dq_claim import record_claim
        record_claim(conn, "build_macro_features", TABLE,
                     claimed_rows=generated, persisted_rows=persisted,
                     source_rows=macro_rows + ev_rows,
                     note=f"grid={grid_rows} dates={len(dates)} alive={len(alive)}/16 "
                          f"since={a.since} window_days={window_days} "
                          f"asof_violations={len(viol)} cpi_lag={CPI_LAG_DAYS} "
                          f"ppi_lag={PPI_LAG_DAYS}")
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        log(f"자기신고 생략: {exc}")

    if macro_rows + ev_rows > 0 and persisted == 0:
        log("적재 실패(소스 있음 + 저장 0행) — exit 4")
        cur.close(); conn.close()
        return 4
    if viol:
        log("as-of 위반 있음 — exit 5")
        cur.close(); conn.close()
        return 5
    log(f"완료 ({time.time() - t0:.1f}s)")
    cur.close(); conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

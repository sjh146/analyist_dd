#!/usr/bin/env python3
"""build_financial_ratio_features — 재무 비율 피처 19개를 종목×거래일 격자로 적재 (R12).

WHY (2026-09-25 실측): feature_coverage 에서 재무 비율 19개가 nonzero_ratio=0 이었다
(조회: `docker exec stock_postgres psql -U stock_user -d stock_trading -tAc "SELECT
feature_name, nonzero_ratio, computed_at FROM feature_coverage WHERE feature_name IN
('value_per','value_pbr','value_psr','value_pcr','value_ncav','value_ev_ebit','value_pfcr',
'quality_cp_to_assets','quality_op_to_equity','quality_roe','quality_roa','quality_f_score',
'quality_asset_growth','quality_debt_ratio_change','quality_op_growth','roe','per_current',
'pbr_current')"` → 19개 전부 0, computed_at 2026-09-24).
원천은 이미 보유 중이었다(조회: `SELECT report_date, COUNT(*) FROM financial_statements
GROUP BY 1` → 10,370행 = 2023/2024/2025-12-31 연간 2,592×3 + 2026-06-30 반기 2,588;
`SELECT MIN(rcept_dt), MAX(rcept_dt), COUNT(*) FROM disclosures` → 212,861행,
2025-01-02~2026-09-23). 부재한 것은 원천→피처 변환·적재 코드였다.
기존 factor_features.py 는 패널에서 **live 계산**을 하지만 ① rcept_dt 를 전혀 보지 않아
룩어헤드(최신 보고서를 과거 행에 사용) ② stocks.market_cap(현재값)을 과거 날짜에 사용
③ 연간/반기 행을 뒤섞어 비교 하는 상태였다. 이 빌더는 세 가지를 모두 고친 as-of 테이블이다.

as-of 규율 (미래 누수 없음 — R12 method "접수일 이후에만 값이 보이도록 조인"):
  · 격자 = market_data 의 (stock_code, trade_date). 거래일 D 행에는 **rcept_dt <= D**
    인 재무제표 중 report_date 가 가장 큰(최신) 행의 비율만 붙인다(공시 당일 포함 —
    17_event_features.sql 의 "공시는 장중 공개" 규율과 동일).
  · rcept_dt 는 disclosures 의 보고서명('사업보고서 (2024.12)' 형태)에서 기간말을
    파싱해 financial_statements.report_date 와 정합한다. 매칭 실측(period_end==report_date
    조인): 2024-12-31 2,451/2,592 · 2025-12-31 2,477/2,598 · 2026-06-30 2,544/2,588,
    (stock, 기간)당 접수건은 정확히 1건(19,484쌍 중 중복 0 — SQL로 실측).
  · rcept_dt 가 없고 report_date < 격자 시작(2025-06-16)이면 전 구간 가시로 처리한다
    (접수일이 disclosures 백필 시작 2025-01-02 이전 — 실측: 2023-12-31 행 2,592건 전부,
    2024-12-31 행 중 141건. 격자 시작보다 6개월 이상 이전 보고서라 룩어헤드 없음).
    report_date >= 격자 시작인데 rcept_dt 가 없으면 **그 행은 제외**(가시 시점 증명 불가 —
    실측 제외: 2025-12-31 121건, 2026-06-30 44건). 이 경우 해당 종목은 직전 가시 보고서 값 유지.
  · 시가총액 계열(PER/PBR/PSR/PCR/EV-EBIT/PFCR): 거래일 D 의 종가 × 상장주식수
    (market_data 사용 — 스펙 지시). 상장주식수 = ownership.listed_shares(2026-09-23
    225종목) 우선, 없으면 stocks.market_cap ÷ 최신 종가로 역산. 실측 2026-09-25 조회:
    ownership 225종목 **전부** 역산값과 정확히 일치(225/225 — 시총이 KRX 일별 공표값이라
    listed_shares 와 같은 원천이기 때문).

기간유형 구분 (연간/반기/분기 혼재 금지):
  · report_date 의 월로 period_type 을 나눈다: 12-31=annual, 06-30=semi,
    03-31=quarter(Q1), 09-30=quarter(Q3). 그 외 기간유형은 계산하지 않는다(현 DB엔 없음).
  · 누적 손익의 연율화(TTM 근사): annual ×1, semi ×2, Q1 ×4, Q3 ×4/3 — 기존
    refresh_valuation_ratios.py ANNUALIZE 규칙(2026-09-24 수립)과 동일 정의.
    연율화 없이는 반기 보고서 수령일마다 ROA/ROE/PER 이 절반으로 튄다
    (실측 삼성전자 2026-06-30 revenue 171.5조 = FY2025 333.6조의 약 절반).
  · **성장률 계열(quality_asset_growth/debt_ratio_change/op_growth, f_score 의 매출
    비교, PFCR 의 재투자 프록시)은 같은 기간유형의 직전 행과만** 비교한다. 반기
    2026-06-30 행은 직전 반기(2025-06-30)가 원천에 없어 성장률 NULL(값을 만들지 않음).
  · quality_earnings_volatility = 동일 기간유형 ni_ttm 이력(최대 8기)의 표준편차
    (기존 factor_features 는 연간/반기 ni 를 뒤섞어 std 를 냈던 것의 수정).

비율 정의 (factor_features.py · refresh_valuation_ratios.py 와 동일 규율):
  · quality_roe = roe = ni_ttm/자기자본×100, quality_roa = ni_ttm/총자산×100
  · value_per = per_current = 시총/ni_ttm (ni_ttm>0), value_pbr = pbr_current =
    시총/자기자본 (자본>0), value_psr = 시총/rev_ttm, value_pcr = 시총/ocf_ttm,
    value_ev_ebit = 시총/op_ttm, value_pfcr = 시총/(ocf_ttm − max(0, 자산−직전동일유형자산))
  · quality_cp_to_assets = ocf_ttm/총자산, quality_op_to_equity = op_ttm/자기자본
  · quality_f_score = 5점(roe>0, 영업이익률>0, 순이익률>0, 부채비율<100, 매출>직전동일유형)
    — 결측 성분은 0점(factor_features 규칙). 부채비율 = total_debt/총자산×100
    (실측 2026-09-25: 컬럼 debt_ratio 와 이 식이 10,117/10,128행 일치).
  · value_ncav: financial_statements 에 current_assets 컬럼이 없어(실측 조회
    information_schema.columns = 0) NCAV 계산 불가 → **NULL 유지**
    (factor_features.py 의 기존 결정과 동일 — 유동자산 컬럼이 생기면 재실행으로 살아난다).
  · value_per/per_current 와 quality_roe/roe 가 같은 정의인 이유: 파이프라인이
    company_features(기본 비율)와 factor_features(밸류/퀄리티)의 **이중 경로**로 읽기
    때문 — 두 경로 모두 살리는 것이 R12 의 목적(실측: 두 경로 모두 현재 0).

멱등성: 재실행 안전 — financial_ratio_features 는 이 빌더 전용이므로 DELETE 후 COPY.
feature_coverage 는 19개 행을 ON CONFLICT(feature_name) DO UPDATE 로 갱신
(계산식은 feature_coverage_report.compute_coverage 와 동일 정의, 격자 전체를 분모로 실측).
※ feature_coverage_report.py(훈련 패널 경유 스냅샷)와의 연결은 R14 소관 — R12.note.

자기신고: dq_claim.record_claim(소스 행수 = 재무제표 10,370 + 접수일 매칭 건수 /
생성·적재 행수 / 저장 후 테이블 행수). source_rows>0 이고 persisted==0 이면 exit 4
(2026-09-24 파서 키 불일치 유형 재발 방지).

as-of 자체 검증: ① 표본 (종목, 거래일) 쌍을 SQL ground truth(rcept_dt <= D 최신 보고서)와
대조 ② 적재 후 `rcept_dt > trade_date` / `report_date > trade_date` 행수 = 0 확인.
위반이 있으면 출력에 남기고 exit 5 (R12 성공 기준 "dq_asof_violation_rows 0 유지").

사용 (호스트):
  cd /home/jhshi/analyist_dd
  set -a && . ./.env && set +a
  export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434
  /usr/bin/python3 scripts/build_financial_ratio_features.py
  /usr/bin/python3 scripts/build_financial_ratio_features.py --dry-run --limit-stocks 50
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

TABLE = "financial_ratio_features"
DDL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "init-scripts", "postgres", "20_financial_ratio_features.sql")

FEATURES = [
    "value_per", "value_pbr", "value_psr", "value_pcr", "value_ncav",
    "value_ev_ebit", "value_pfcr",
    "quality_cp_to_assets", "quality_op_to_equity", "quality_roe",
    "quality_roa", "quality_f_score", "quality_asset_growth",
    "quality_debt_ratio_change", "quality_op_growth",
    "quality_earnings_volatility",
    "roe", "per_current", "pbr_current",
]

# 시가총액이 필요한 피처 (종가×상장주식수, 거래일별)
MARKET_BASES = {
    "value_per": "per_base", "value_pbr": "pbr_base", "value_psr": "psr_base",
    "value_pcr": "pcr_base", "value_ev_ebit": "ev_base", "value_pfcr": "pfcr_base",
    "per_current": "per_base", "pbr_current": "pbr_base",
}
# 재무제표 행만으로 정해지는 피처 (기간유형 구분 완료된 행 값)
LEVEL_FEATURES = [
    "quality_cp_to_assets", "quality_op_to_equity", "quality_roe",
    "quality_roa", "quality_f_score", "quality_asset_growth",
    "quality_debt_ratio_change", "quality_op_growth",
    "quality_earnings_volatility", "roe",
]

# 파이프라인과 동일한 유효성 필터(feature_pipeline.MARKET_DATA_VALID)
MARKET_DATA_VALID = "NOT (open_price = 0 AND high_price = 0 AND low_price = 0)"

# disclosures 보고서명 → 기간말 파싱 (사업보고서 (2024.12) → 2024-12-31)
DISCLOSURE_PARSE = """
WITH d AS (
  SELECT stock_code,
         make_date((regexp_match(report_nm, '^(사업보고서|반기보고서|분기보고서) \\(([0-9]{4})\\.([0-9]{2})\\)'))[2]::int,
                   (regexp_match(report_nm, '^(사업보고서|반기보고서|분기보고서) \\(([0-9]{4})\\.([0-9]{2})\\)'))[3]::int, 1)
         + interval '1 month - 1 day' AS period_end,
         MIN(rcept_dt) AS rcept_dt
  FROM disclosures
  WHERE report_nm ~ '^(사업보고서|반기보고서|분기보고서) \\([0-9]{4}\\.[0-9]{2}\\)'
  GROUP BY 1, 2
)
"""

FIN_SQL = DISCLOSURE_PARSE + """
SELECT f.stock_code, f.report_date::text, d.rcept_dt::text,
       f.revenue, f.operating_profit, f.net_income, f.total_assets,
       f.total_equity, f.total_debt, f.operating_cash_flow
FROM financial_statements f
LEFT JOIN d ON d.stock_code = f.stock_code AND d.period_end = f.report_date
ORDER BY f.stock_code, f.report_date
"""


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def as_d64(series):
    """date 객체 컬럼 → datetime64[D] numpy 배열."""
    return np.asarray(pd.to_datetime(series).dt.normalize(), dtype="datetime64[D]")


def period_type_factor(rd):
    """report_date → (period_type, 연율화 계수). 미지원 기간은 (None, None)."""
    m = rd.month
    if m == 12:
        return "annual", 1.0
    if m == 6:
        return "semi", 2.0
    if m == 3:
        return "quarter", 4.0
    if m == 9:
        return "quarter", 4.0 / 3.0
    return None, None


def load_grid(cur, since, limit_stocks=0):
    """시세 격자 로드(종목·거래일·종가) — 피처 기준 격자이자 feature_coverage 분모."""
    q = (f"SELECT stock_code, trade_date, close_price FROM market_data "
         f"WHERE trade_date >= %s AND {MARKET_DATA_VALID}")
    params = [since]
    if limit_stocks:
        q += (" AND stock_code IN (SELECT stock_code FROM market_data GROUP BY 1 "
              f"ORDER BY 1 LIMIT {int(limit_stocks)})")
    cur.execute(q + " ORDER BY stock_code, trade_date", params)
    df = pd.DataFrame(cur.fetchall(), columns=["stock_code", "trade_date", "close_price"])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df


def load_statements(cur):
    """financial_statements × disclosures(rcept_dt) — (종목, 기간말) 정합."""
    cur.execute(FIN_SQL)
    df = pd.DataFrame(cur.fetchall(),
                      columns=["stock_code", "report_date", "rcept_dt",
                               "revenue", "operating_profit", "net_income",
                               "total_assets", "total_equity", "total_debt",
                               "operating_cash_flow"])
    df["report_date"] = pd.to_datetime(df["report_date"])
    df["rcept_dt"] = pd.to_datetime(df["rcept_dt"], errors="coerce")
    for c in ("revenue", "operating_profit", "net_income", "total_assets",
              "total_equity", "total_debt", "operating_cash_flow"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def load_shares(cur):
    """상장주식수: ownership.listed_shares 우선, 없으면 stocks.market_cap÷최신종가 역산.

    역산의 실측 근거(2026-09-25): ownership 225종목 전부에서
    round(market_cap/최신종가) == listed_shares (225/225).
    """
    shares = {}
    cur.execute("""SELECT o.stock_code, o.listed_shares FROM ownership o
                   WHERE o.trade_date = (SELECT MAX(trade_date) FROM ownership)
                     AND o.listed_shares IS NOT NULL AND o.listed_shares > 0""")
    for c, v in cur.fetchall():
        shares[c] = float(v)
    cur.execute("""SELECT s.stock_code, s.market_cap::numeric, md.close_price
                   FROM stocks s JOIN market_data md ON md.stock_code = s.stock_code
                     AND md.trade_date = (SELECT MAX(trade_date) FROM market_data)
                   WHERE s.market_cap IS NOT NULL AND md.close_price > 0""")
    implied = 0
    for c, cap, px in cur.fetchall():
        if c not in shares:
            shares[c] = round(float(cap) / float(px))
            implied += 1
    return shares, implied


def build_statements(sdf, grid_start):
    """재무제표 행 → 기간유형 구분·연율화·성장률(동일 유형 직전 대비)·f_score 등 계산.

    반환: 각 재무제표 행(가시 시점 vis 포함)의 수준별 피처 값 DataFrame.
    rcept_dt 가 없고 report_date < grid_start → vis = grid_start (전 구간 가시),
    rcept_dt 가 없고 report_date >= grid_start → 제외 (가시 시점 증명 불가).
    """
    rows = []
    for stock, g in sdf.groupby("stock_code", sort=False):
        g = g.sort_values("report_date").reset_index(drop=True)
        prev, hist = {}, {}
        for _, r in g.iterrows():
            rd = r["report_date"]
            typ, factor = period_type_factor(rd)
            if typ is None:
                continue
            rcept = r["rcept_dt"]
            if pd.isna(rcept):
                if rd >= grid_start:
                    continue  # 접수일 미확보 + 격자 내 보고서 → 가시 시점 증명 불가
                vis = grid_start
                rcept_out = pd.NaT
            else:
                vis = rcept
                rcept_out = rcept

            ni_ttm = r["net_income"] * factor if pd.notna(r["net_income"]) else np.nan
            op_ttm = r["operating_profit"] * factor if pd.notna(r["operating_profit"]) else np.nan
            rev_ttm = r["revenue"] * factor if pd.notna(r["revenue"]) else np.nan
            ocf_ttm = (r["operating_cash_flow"] * factor
                       if pd.notna(r["operating_cash_flow"]) else np.nan)
            assets = float(r["total_assets"]) if pd.notna(r["total_assets"]) else np.nan
            equity = float(r["total_equity"]) if pd.notna(r["total_equity"]) else np.nan
            debt = float(r["total_debt"]) if pd.notna(r["total_debt"]) else np.nan

            # ── 수준 비율 (재무상태표 시점값은 그대로, 손익은 연율화) ──
            roe_r = ni_ttm / equity * 100.0 if equity > 0 else np.nan
            roa = ni_ttm / assets * 100.0 if assets > 0 else np.nan
            debt_ratio = debt / assets * 100.0 if assets > 0 else np.nan
            cp_to_assets = ocf_ttm / assets if assets > 0 else np.nan
            op_to_equity = op_ttm / equity if equity > 0 else np.nan
            per_base = 1.0 / ni_ttm if ni_ttm > 0 else np.nan
            pbr_base = 1.0 / equity if equity > 0 else np.nan
            psr_base = 1.0 / rev_ttm if rev_ttm > 0 else np.nan
            pcr_base = 1.0 / ocf_ttm if ocf_ttm > 0 else np.nan
            ev_base = 1.0 / op_ttm if op_ttm > 0 else np.nan

            p = prev.get(typ)
            # PFCR: FCF = 연율화 OCF − 재투자 프록시(동일 유형 직전 대비 순자산 증가 —
            # CAPEX 컬럼이 없어서 쓰는 프록시, factor_features.py 와 동일 정의)
            reinvest = max(0.0, assets - p["assets"]) if (p and p["assets"] > 0) else 0.0
            fcf = ocf_ttm - reinvest
            pfcr_base = 1.0 / fcf if fcf > 0 else np.nan

            # ── 성장률 계열: 같은 기간유형의 직전 행과만 비교 ──
            if p is not None:
                asset_growth = ((assets - p["assets"]) / p["assets"] * 100.0
                                if p["assets"] > 0 else np.nan)
                debt_change = (debt_ratio - p["debt_ratio"]
                               if pd.notna(debt_ratio) and pd.notna(p["debt_ratio"])
                               else np.nan)
                op_growth = ((op_ttm - p["op_ttm"]) / p["op_ttm"] * 100.0
                             if p["op_ttm"] > 0 and pd.notna(op_ttm) else np.nan)
            else:
                asset_growth = debt_change = op_growth = np.nan

            # ── f_score (결측 성분은 0점 — factor_features 규칙) ──
            op_margin = op_ttm / rev_ttm * 100.0 if rev_ttm > 0 else np.nan
            net_margin = ni_ttm / rev_ttm * 100.0 if rev_ttm > 0 else np.nan
            f_score = 0.0
            if pd.notna(roe_r) and roe_r > 0:
                f_score += 1.0
            if pd.notna(op_margin) and op_margin > 0:
                f_score += 1.0
            if pd.notna(net_margin) and net_margin > 0:
                f_score += 1.0
            if pd.notna(debt_ratio) and debt_ratio < 100.0:
                f_score += 1.0
            if p is not None and p["rev"] > 0 and pd.notna(rev_ttm) and rev_ttm > p["rev"]:
                f_score += 1.0

            # ── 이익 변동성: 같은 기간유형 ni_ttm 이력(최대 8기) ──
            h = hist.setdefault(typ, [])
            h.append(ni_ttm)
            earn_vol = np.nan
            vals = [x for x in h[-8:] if pd.notna(x)]
            if len(vals) >= 3:
                earn_vol = float(np.std(vals))

            rows.append({
                "stock_code": stock, "vis": vis, "report_date": rd,
                "rcept_dt": rcept_out, "period_type": typ,
                "roe_r": roe_r, "roa": roa, "cp_to_assets": cp_to_assets,
                "op_to_equity": op_to_equity, "f_score": f_score,
                "asset_growth": asset_growth, "debt_change": debt_change,
                "op_growth": op_growth, "earn_vol": earn_vol,
                "per_base": per_base, "pbr_base": pbr_base, "psr_base": psr_base,
                "pcr_base": pcr_base, "ev_base": ev_base, "pfcr_base": pfcr_base,
                "ni_ttm": ni_ttm,
            })
            prev[typ] = {"assets": assets, "debt_ratio": debt_ratio,
                         "op_ttm": op_ttm, "rev": rev_ttm}
    return pd.DataFrame(rows)


def statements_dict(sdf):
    """문장별 계산 결과 → 종목별 numpy 배열 dict (격자 매핑용)."""
    out = {}
    for stock, g in sdf.groupby("stock_code", sort=False):
        out[stock] = {
            "vis": as_d64(g["vis"]),
            "ptype": g["period_type"].to_numpy(object),
            "rdate": g["report_date"].dt.strftime("%Y-%m-%d").to_numpy(object),
            "rcept": g["rcept_dt"].apply(
                lambda v: None if pd.isna(v) else v.strftime("%Y-%m-%d")
            ).to_numpy(object),
            "roe": g["roe_r"].to_numpy(float),
            "quality_roe": g["roe_r"].to_numpy(float),
            "quality_roa": g["roa"].to_numpy(float),
            "quality_cp_to_assets": g["cp_to_assets"].to_numpy(float),
            "quality_op_to_equity": g["op_to_equity"].to_numpy(float),
            "quality_f_score": g["f_score"].to_numpy(float),
            "quality_asset_growth": g["asset_growth"].to_numpy(float),
            "quality_debt_ratio_change": g["debt_change"].to_numpy(float),
            "quality_op_growth": g["op_growth"].to_numpy(float),
            "quality_earnings_volatility": g["earn_vol"].to_numpy(float),
            "per_base": g["per_base"].to_numpy(float),
            "pbr_base": g["pbr_base"].to_numpy(float),
            "psr_base": g["psr_base"].to_numpy(float),
            "pcr_base": g["pcr_base"].to_numpy(float),
            "ev_base": g["ev_base"].to_numpy(float),
            "pfcr_base": g["pfcr_base"].to_numpy(float),
        }
    return out


def apply_to_grid(grid, sdict, shares):
    """격자 행마다 vis <= D 최신 문장의 비율을 붙인다 (as-of 조인)."""
    codes = grid["stock_code"].to_numpy()
    dates = as_d64(grid["trade_date"])
    closes = pd.to_numeric(grid["close_price"], errors="coerce").to_numpy(float)
    n = len(grid)
    feats = {f: np.full(n, np.nan) for f in FEATURES}
    ptype = np.full(n, "", dtype=object)
    rdate = np.full(n, "", dtype=object)
    rcept = np.full(n, "", dtype=object)

    starts = np.concatenate(([0], np.flatnonzero(codes[1:] != codes[:-1]) + 1))
    ends = np.concatenate((starts[1:], [n]))
    for s, e in zip(starts, ends):
        st = sdict.get(codes[s])
        if st is None:
            continue
        d = dates[s:e]
        idx = np.searchsorted(st["vis"], d, side="right") - 1
        ok = idx >= 0
        if not ok.any():
            continue
        i = idx[ok]
        for f in LEVEL_FEATURES:
            feats[f][s:e][ok] = st[f][i]
        sh = shares.get(codes[s])
        if sh:
            cap = closes[s:e][ok] * sh
            cap = np.where(np.isfinite(cap) & (cap > 0), cap, np.nan)
            for f, base in MARKET_BASES.items():
                feats[f][s:e][ok] = st[base][i] * cap
        ptype[s:e][ok] = st["ptype"][i]
        rdate[s:e][ok] = st["rdate"][i]
        rcept[s:e][ok] = st["rcept"][i]

    grid = grid.copy()
    for f in FEATURES:
        grid[f] = feats[f]
    grid["period_type"] = ptype
    grid["report_date"] = rdate
    grid["rcept_dt"] = rcept
    return grid


def compute_coverage_rows(grid, window_days, computed_at):
    """feature_coverage_report.compute_coverage 와 동일 정의로 격자 전체 커버리지 계산."""
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


def sample_pairs(grid, sdict, rng):
    """as-of 대조 표본: 접수일 경계 전후의 (종목, 거래일, 빌더가 붙인 report_date)."""
    pairs = []
    grid_codes = set(grid["stock_code"])
    candidates = [c for c in sdict if c in grid_codes
                  and np.isfinite(sdict[c]["vis"]).any()]
    for stock in rng.choice(sorted(candidates), size=min(8, len(candidates)),
                            replace=False):
        st = sdict[stock]
        g = grid[grid["stock_code"] == stock].sort_values("trade_date")
        if g.empty:
            continue
        d = as_d64(g["trade_date"])
        idx = np.searchsorted(st["vis"], d, side="right") - 1
        ok = idx >= 0
        rd = np.full(len(g), None, dtype=object)
        rd[ok] = st["rdate"][idx[ok]]
        # 접수일 경계: vis 직전/이후 거래일 1쌍씩 (경계가 격자 밖이면 생략)
        for v in np.unique(st["vis"]):
            lo = d[d < v]
            hi = d[d >= v]
            for dd in (([lo[-1:]] if len(lo) else []) +
                       ([hi[:1]] if len(hi) else [])):
                j = np.searchsorted(d, dd)
                pairs.append((stock, str(np.datetime_as_string(d[j[0]], unit="D")),
                              rd[j[0]]))
        # 추가: 중간 + 마지막 거래일
        for j in (len(d) // 2, len(d) - 1):
            pairs.append((stock, str(np.datetime_as_string(d[j], unit="D")), rd[j]))
    return pairs


def asof_sweep(cur, pairs, grid_start):
    """SQL ground truth 대조 — (종목, D) 의 가시 보고서가 빌더 값과 같은지."""
    viol = []
    for stock, d, expected in pairs:
        cur.execute(DISCLOSURE_PARSE + """
            SELECT f.report_date::text
            FROM financial_statements f
            LEFT JOIN d ON d.stock_code = f.stock_code AND d.period_end = f.report_date
            WHERE f.stock_code = %s
              AND COALESCE(d.rcept_dt, CASE WHEN f.report_date < %s::date
                                            THEN %s::date END) <= %s::date
            ORDER BY f.report_date DESC LIMIT 1""",
            (stock, grid_start, grid_start, d))
        row = cur.fetchone()
        got = row[0] if row else None
        if got != expected:
            viol.append(f"{stock}@{d}: builder={expected} db={got}")
    return viol


def main():
    ap = argparse.ArgumentParser(description="재무 비율 피처 19개 빌더 (R12)")
    ap.add_argument("--since", default="2025-06-16", help="피처 시작일(격자 하한)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit-stocks", type=int, default=0, help="디버그용 종목 수 제한")
    a = ap.parse_args()
    t0 = time.time()
    grid_start = np.datetime64(a.since, "D")

    conn = psycopg2.connect(**PG)
    cur = conn.cursor()

    # ── DDL 적용(단일 원천: init-scripts/postgres/20_*.sql) ──────────────
    with open(DDL_PATH, "r", encoding="utf-8") as fp:
        cur.execute(fp.read())
    conn.commit()

    # ── 1) 격자(종목×거래일, market_data 기준 — R10/R11 과 동일 분모) ──────
    grid = load_grid(cur, a.since, a.limit_stocks)
    if grid.empty:
        log("격자가 비었다 — market_data 확인")
        return 1
    grid_rows = len(grid)
    log(f"격자 {grid_rows}행 ({grid['stock_code'].nunique()}종목 × "
        f"{grid['trade_date'].nunique()}거래일, "
        f"{grid['trade_date'].min().date()} ~ {grid['trade_date'].max().date()})")

    # ── 2) 원천 로드 ───────────────────────────────────────────────────────
    sdf = load_statements(cur)
    n_fin = len(sdf)
    n_matched = int(sdf["rcept_dt"].notna().sum())
    shares, n_implied = load_shares(cur)
    log(f"원천: financial_statements {n_fin}행 (rcept_dt 매칭 {n_matched}) / "
        f"상장주식수 {len(shares)}종목 (ownership {len(shares) - n_implied} + "
        f"시총 역산 {n_implied})")

    # ── 3) 기간유형 구분 + 문장별 비율 계산 ────────────────────────────────
    stmts = build_statements(sdf, grid_start)
    n_excl = n_fin - len(stmts)
    by_type = stmts.groupby("period_type")["stock_code"].nunique().to_dict()
    log(f"문장별 계산: {len(stmts)}행 (기간유형별 종목수 {by_type} / "
        f"제외 {n_excl}행 = 접수일 미확보 격자 내 보고서)")
    sdict = statements_dict(stmts)

    # ── 4) as-of 조인 (vis <= D 최신 문장) ────────────────────────────────
    grid = apply_to_grid(grid, sdict, shares)
    log(f"피처 계산 완료 ({time.time() - t0:.1f}s)")

    # ── 5) 격자 전체 커버리지(분모 = 격자 행수, R10/R11 동일 정의) ─────────
    window_days = int((grid["trade_date"].max() - grid["trade_date"].min()).days)
    computed_at = datetime.now(timezone.utc)
    cov_rows = compute_coverage_rows(grid, window_days, computed_at)
    alive = [r for r in cov_rows if r["nonzero_ratio"] > 0]
    log(f"커버리지(격자 전체): nonzero 피처 {len(alive)}/19")
    for r in cov_rows:
        log(f"  {r['feature_name']:<28} nonzero={r['nonzero_ratio']:.4f} "
            f"null={r['null_ratio']:.4f} std={r['std']:.3f}")

    # ── 6) as-of 자체 검증 (SQL ground truth, 표본 (종목, D) 쌍) ──────────
    rng = np.random.default_rng(20260925)
    pairs = sample_pairs(grid, sdict, rng)
    viol = asof_sweep(cur, pairs, str(grid_start))
    if viol:
        log(f"as-of 위반 {len(viol)}건: " + "; ".join(viol[:8]))
    else:
        log(f"as-of 검증 통과 (SQL 대조 {len(pairs)}쌍, 위반 0)")

    # ── 7) 적재: 19개 중 하나라도 값이 있는 행만 저장(전부 NULL 행 = 미가시 종목) ──
    keep = grid[FEATURES].notna().any(axis=1)
    out = grid.loc[keep].copy()
    out["trade_date"] = out["trade_date"].dt.strftime("%Y-%m-%d")
    out = out[["stock_code", "trade_date", "period_type", "report_date",
               "rcept_dt"] + FEATURES]
    generated = len(out)
    log(f"생성 {generated}행 (전부 NULL 인 {int((~keep).sum())}행 제외) / "
        f"기간유형별: {out.groupby('period_type').size().to_dict()}")

    if a.dry_run:
        log("dry-run: DB 쓰기 생략")
        cur.close(); conn.close()
        return 0

    buf = io.StringIO()
    buf.write(out.to_csv(sep="\t", header=False, index=False, na_rep="\\N"))
    cur.execute(f"DELETE FROM {TABLE}")
    deleted = cur.rowcount
    buf.seek(0)
    cur.copy_expert(f"COPY {TABLE} (stock_code, trade_date, period_type, report_date, "
                    f"rcept_dt, {', '.join(FEATURES)}) FROM STDIN", buf)
    conn.commit()
    cur.execute(f"SELECT COUNT(*) FROM {TABLE}")
    persisted = cur.fetchone()[0]

    # ── 8) 적재 후 불변식: 접수일/보고서일이 거래일 이후인 행 = 룩어헤드 ────
    cur.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE rcept_dt IS NOT NULL "
                f"AND rcept_dt > trade_date")
    fut_rcept = cur.fetchone()[0]
    cur.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE report_date > trade_date")
    fut_rep = cur.fetchone()[0]
    cur.execute(f"SELECT period_type, COUNT(*), COUNT(DISTINCT stock_code) "
                f"FROM {TABLE} GROUP BY 1 ORDER BY 1")
    cls_rows = cur.fetchall()
    log(f"적재 완료: 삭제 {deleted}행 → 삽입 {persisted}행 / "
        f"클래스별(period_type): {cls_rows} / "
        f"불변식 위반: rcept>D {fut_rcept}건, report>D {fut_rep}건")
    if fut_rcept or fut_rep:
        viol.append(f"table: rcept>D={fut_rcept} report>D={fut_rep}")

    # ── 9) feature_coverage 재계산(19개 행, 동일 computed_at — R10 method) ──
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
    log(f"feature_coverage 19행 갱신 (computed_at={computed_at.isoformat()})")

    # ── 10) 자기신고 (소스 수신 / 파서 생성 / 실제 저장 3분리) ────────────
    try:
        from dq_claim import record_claim
        record_claim(conn, "build_financial_ratio_features", TABLE,
                     claimed_rows=generated, persisted_rows=persisted,
                     source_rows=n_fin + n_matched,
                     note=f"grid={grid_rows} fin={n_fin} rcept_matched={n_matched} "
                          f"excluded_no_rcept={n_excl} shares={len(shares)} "
                          f"alive={len(alive)}/19 since={a.since} "
                          f"window_days={window_days} asof_pairs={len(pairs)} "
                          f"asof_violations={len(viol)} classes={by_type} "
                          f"ncav=null(no_current_assets)")
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        log(f"자기신고 생략: {exc}")

    if n_fin > 0 and persisted == 0:
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

#!/usr/bin/env python3
"""애크먼 스크리너 (Ackman Screener) — 테제원장(Thesis Ledger) M3 매수 후보 선별.

빌 애크먼식 장기 보유 매수 후보를 선별하는 스크리너. 유니버스(코스피+코스닥,
거래대금/가격 필터)에서 **하드 베토 5종**(부채비율 한도·감사의견·횡령/분식·
CB 남발·거래정지)을 먼저 걸러내고, 살아남은 종목에
**AckmanScore = Quality × Valuation × Catalyst** (각 축 [0,1])를 산출해
상위 N 후보를 CSV로 출력한다 (docs/테제원장_PLAN.md §5).

전략 요약 (.omo/plans/ackman-screener-m3.md §Execution strategy 결정본):
- 유니버스: KOSPI+KOSDAQ (market_data 20거래일 이상 보유 종목)
- Quality  : ROIC 일관성(0.35) + FCF 마진 안정성(0.20) + 이익 변동성(0.20)
              + 부채비율 점수(0.25)
- Valuation: FCF yield z-score(0.40) + 업종 PER/PBR 백분위(0.30)
              + 단순 DCF MoS(0.30)
- Catalyst : 6개월 이벤트 가중 합(자사주 1.00 > 배당 0.80 > …)의 정규화
- 하드 베토: 부채비율 > 200 / 감사의견 비적정·한정 / 횡령·분식 키워드 /
              CB·BW 2건 이상 / 거래정지 이벤트·거래 공백 10일 초과

사용법:
  python3 scripts/ackman_screener.py --top-n 15
  python3 scripts/ackman_screener.py --date 2026-08-19 --output out.csv

이 모듈은 todo 1 산출물(골격+상수+유니버스/가격 로딩)까지 포함하며,
스코어 축·베토·합산·run_screener·main은 후속 todo에서 추가된다.
"""
import argparse
import logging
import math
import os
import re
import sys
from datetime import datetime, date, timedelta

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("ackman_screener")

# ── PostgreSQL 접속 (close_screener 관례: env-driven) ──────────────────
PG_HOST = os.environ.get("POSTGRES_HOST", "postgres")
PG_PORT = int(os.environ.get("POSTGRES_PORT", 5432))
PG_DB = os.environ.get("POSTGRES_DB", "stock_trading")
PG_USER = os.environ.get("POSTGRES_USER", "stock_user")
PG_PASS = os.environ.get("POSTGRES_PASSWORD", "")

# ── 출력 CSV 컬럼 (결정본 §복합/랭킹/출력) ───────────────────────────────
OUTPUT_COLUMNS = [
    "rank", "stock_code", "stock_name", "sector", "signal_date", "close_price",
    "ackman_score", "quality_score", "valuation_score", "catalyst_score", "reason",
]

# ── 유니버스/데이터 필터 상수 (결정본 §유니버스) ─────────────────────────
MIN_TRADING_VALUE = 300_000_000  # 최근 20거래일 평균 거래대금 하한 (3억원)
MIN_PRICE = 1_000                # 최근 종가 하한 (1,000원)
MIN_MARKET_ROWS = 20             # market_data 최소 보유 거래일 수
MIN_FUND_YEARS = 3               # 재무제표 최소 보유 연수 (미달 시 스코어 불가)

# ── Quality 축 상수 (결정본 §Quality) ───────────────────────────────────
QUALITY_W = (0.35, 0.20, 0.20, 0.25)  # roic_consistency/fcf_score/vol_score/debt_score 가중치
ROIC_FLOOR = 0.05                     # ROIC 일관성 기준선 (5%)
ROIC_STD_CAP = 0.15                   # ROIC 변동성 정규화 분모 (std 15% → vol_score 0)
DEBT_SCORE_HI = 150                   # 부채비율 150% → debt_score 0.0 (50% → 1.0)
DEBT_VETO = 200                       # 부채비율 하드 베토 임계 (200%)
FCF_MARGIN_TARGET = 0.10              # FCF 마진 정규화 목표 (10%)

# ── Valuation 축 상수 (결정본 §Valuation) ───────────────────────────────
VALUATION_W = (0.40, 0.30, 0.30)      # z_score/pct_score/mos_score 가중치
MOF_WACC = 0.10                       # 단순 DCF 할인율 (10%)
MOF_G = 0.02                          # 단순 DCF 영구성장률 (2%)
MOF_FULL = 0.5                        # MoS 정규화 분모 (MoS 50% → 1.0)

# ── Catalyst 축 상수 (결정본 §Catalyst) ─────────────────────────────────
CATALYST_DAYS = 182                   # 촉매 이벤트 윈도우 (6개월)
CATALYST_HALF_LIFE = 90               # 시간 감쇠 반감기 (일, exp(-days/90))
CATALYST_NORM = 5.0                   # 촉매 원점수 정규화 분모

# ── 하드 베토 상수 (결정본 §하드 베토 5종) ──────────────────────────────
CB_VETO_COUNT = 2                     # 6개월 내 CB·BW 이벤트 베토 임계 (2건)
TRADE_GAP_DAYS = 10                   # 거래 공백 베토 임계 (signal_date − 최근거래일 > 10일)
AUDIT_OPINION_VETO = ("비적정", "한정")  # 감사의견 베토 키워드 (값 없음 → fail-open, PLAN §8)

# ── 촉매 이벤트 타입 가중치 (결정본 §Catalyst, DB CHECK 20종과 1:1) ──────
# 계층: 자사주 > 밸류업(→'배당' 근사) > 배당 > M&A/지분변동 > … , 부정·중립 타입 0.00.
# 0.00 타입은 촉매 크레딧에 기여하지 않지만 베토(횡령/분식·CB·거래정지) 판정에는 사용된다.
CATALYST_EVENT_WEIGHTS = {
    "자사주": 1.00,
    "배당": 0.80,
    "M&A": 0.70,
    "지분변동": 0.70,
    "수주": 0.60,
    "임원변경": 0.55,
    "실적발표": 0.50,
    "신제품": 0.50,
    "특허": 0.45,
    "파트너십": 0.45,
    "유상증자·감자": 0.00,
    "CB·BW": 0.00,
    "규제": 0.00,
    "소송": 0.00,
    "부도·상폐·거래정지": 0.00,
    "리콜": 0.00,
    "거시경제": 0.00,
    "시장지수·유동성": 0.00,
    "자연재해": 0.00,
    "기타": 0.00,
}

# ── 횡령/분식 베토 키워드 (결정본 §하드 베토) ────────────────────────────
FRAUD_KEYWORDS = ("횡령", "분식", "배임", "조작", "검찰", "금융위", "제재")


def get_pg_conn():
    """psycopg2 연결 생성 (lazy import — 테스트 환경에 psycopg2 없어도 import 가능)."""
    import psycopg2
    return psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PASS
    )


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


def normalize_code(code):
    """종목코드를 6자리 숫자 문자열로 정규화 (pandas가 선행 0을 자르는 것 방지)."""
    s = str(code).strip()
    if s.isdigit():
        return s.zfill(6)
    return s


def load_universe(pg_conn, trade_date):
    """유니버스 로드 → [(code, name, sector, latest_date), ...] 4-튜플 리스트.

    KOSPI+KOSDAQ 전체 종목 중 market_data를 20거래일 이상 보유한 종목
    (swing_screener 관례, 양 시장). trade_date 파라미터는 인터페이스 일치용 —
    거래대금/가격 필터(MIN_TRADING_VALUE/MIN_PRICE)와 as-of 필터는
    run_screener/load_market_history에서 적용한다.
    """
    cur = pg_conn.cursor()
    cur.execute("""
        SELECT s.stock_code, s.stock_name, COALESCE(s.sector, 'Unknown') as sector,
               MAX(md.trade_date) as latest_date
        FROM stocks s
        JOIN market_data md ON s.stock_code = md.stock_code
        WHERE s.market IN ('KOSPI', 'KOSDAQ')
        GROUP BY s.stock_code, s.stock_name, s.sector
        HAVING COUNT(*) >= %s
        ORDER BY s.stock_code
    """, (MIN_MARKET_ROWS,))
    rows = cur.fetchall()
    cur.close()
    return rows  # [(code, name, sector, latest_date), ...]


def load_market_history(pg_conn, trade_date_str, lookback=20):
    """종목별 최근 lookback 거래일 OHLCV 로드 (쿼리 1회) → DataFrame.

    ROW_NUMBER() 윈도우로 종목별 최근 lookback행만 가져온다
    (close_screener.load_price_history 패턴). 반환 컬럼:
    [stock_code(str), trade_date, open_price, high_price, low_price,
     close_price(float), volume(float), trading_value(float)].
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


def load_stock_meta(pg_conn):
    """종목 마스터 전체 로드 → {code: {"stock_name":..., "sector":..., "market_cap":...}}.

    dict 선택 사유: run_screener에서 종목별 이름/섹터/시가총액을 O(1)로 조회하고,
    유니버스 필터링 후 필요한 종목만 꺼내 쓸 수 있다. 키는 normalize_code 적용 str.
    market_cap은 BIGINT/결측 → float 또는 None.
    """
    cur = pg_conn.cursor()
    cur.execute("""
        SELECT stock_code, stock_name, sector, market_cap
        FROM stocks
    """)
    rows = cur.fetchall()
    cur.close()
    meta = {}
    for code, name, sector, market_cap in rows:
        meta[normalize_code(code)] = {
            "stock_name": name,
            "sector": sector,
            "market_cap": float(market_cap) if market_cap is not None else None,
        }
    return meta


# ── 재무/피어/이벤트 로딩 (todo 2) ──────────────────────────────────────


def _to_float(v):
    """숫자 컬럼 → float 변환 (NULL·변환 불가 → None, 0.0 대체 없음).

    변환 실패를 None으로 반환하는 사유: 스코어 축들이 결측을 fail-low로 처리하므로,
    여기서 0.0으로 뭉개면 결측과 실제 0을 구분할 수 없다.
    """
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_fundamentals(pg_conn, code, years=5):
    """종목 재무제표 최근 years개 연간 행 로드 (report_date DESC) → dict 리스트.

    반환 키: report_date(str|date 원형 보존), operating_profit, net_income,
    total_equity, debt_ratio, revenue, operating_cash_flow, per, pbr
    (숫자는 float 또는 None — 변환 불가·NULL → None, 스코어 축이 fail-low 처리).
    capex 컬럼 부재(01_schema.sql)로 FCF 마진 proxy(operating_cash_flow/revenue)는
    Quality 축에서 계산한다.
    """
    cur = pg_conn.cursor()
    cur.execute("""
        SELECT report_date, operating_profit, net_income, total_equity, debt_ratio,
               revenue, operating_cash_flow, per, pbr
        FROM financial_statements
        WHERE stock_code = %s
        ORDER BY report_date DESC
        LIMIT %s
    """, (code, years))
    rows = cur.fetchall()
    cur.close()
    out = []
    for row in rows:
        (report_date, operating_profit, net_income, total_equity, debt_ratio,
         revenue, operating_cash_flow, per, pbr) = row
        out.append({
            "report_date": report_date,
            "operating_profit": _to_float(operating_profit),
            "net_income": _to_float(net_income),
            "total_equity": _to_float(total_equity),
            "debt_ratio": _to_float(debt_ratio),
            "revenue": _to_float(revenue),
            "operating_cash_flow": _to_float(operating_cash_flow),
            "per": _to_float(per),
            "pbr": _to_float(pbr),
        })
    return out


def load_sector_peers(pg_conn, sector):
    """업종 내 PER/PBR 교차단면 로드 → [{stock_code, per, pbr}, ...] dict 리스트.

    company_features.get_percentile_features 패턴: 종목별 최신 report_date 행만,
    per/pbr > 0만 포함. Valuation 축의 업종 백분위 계산 입력으로 사용된다
    (백분위 자체 계산은 score_valuation의 _pct_score에서 수행).
    """
    cur = pg_conn.cursor()
    cur.execute("""
        SELECT fs.stock_code, fs.per, fs.pbr
        FROM financial_statements fs
        JOIN stocks s ON fs.stock_code = s.stock_code
        WHERE s.sector = %s
          AND fs.per > 0 AND fs.pbr > 0
          AND fs.report_date = (
              SELECT MAX(report_date) FROM financial_statements
              WHERE stock_code = fs.stock_code
          )
    """, (sector,))
    rows = cur.fetchall()
    cur.close()
    return [
        {"stock_code": r[0], "per": _to_float(r[1]), "pbr": _to_float(r[2])}
        for r in rows
    ]


def load_events(pg_conn, code, since):
    """종목의 뉴스 이벤트 로드 (created_at >= since, DESC) → dict 리스트.

    since는 date/datetime — 호출자가 trade_date - timedelta(days=CATALYST_DAYS) 전달.
    반환 키: event_type(str), sentiment_score, importance (float 또는 None),
    core_event_text(str), created_at(원형 보존). 음수 감정 필터·시간 감쇠는
    Catalyst 축(score_catalyst)에서 적용한다.
    """
    cur = pg_conn.cursor()
    cur.execute("""
        SELECT event_type, sentiment_score, importance, core_event_text, created_at
        FROM news_event_extraction
        WHERE stock_code = %s
          AND created_at >= %s
        ORDER BY created_at DESC
    """, (code, since))
    rows = cur.fetchall()
    cur.close()
    out = []
    for event_type, sentiment_score, importance, core_event_text, created_at in rows:
        out.append({
            "event_type": event_type,
            "sentiment_score": _to_float(sentiment_score),
            "importance": _to_float(importance),
            "core_event_text": core_event_text,
            "created_at": created_at,
        })
    return out


# ── Quality 축 순수 함수 (todo 3, §결정본 Quality) ─────────────────────
# 전부 DB 접근 없는 순수 함수 — dict 픽스처로 직접 테스트 가능.
# 결측(None)은 0점/제외 fail-low 처리, 마법 숫자 없음(상수 참조만).


def _clamp(x, lo, hi):
    """x를 [lo, hi]로 클램프 (close_screener.py:248-249 관례)."""
    return max(lo, min(hi, x))


def _roic_consistency(roics):
    """ROIC 5년 일관성 → [0,1].

    결정본: (5년 중 ROIC >= ROIC_FLOOR 연도 수) / 5 — 분모는 기준 5년 고정.
    결측 연도(None)는 0점 (누락 자체가 불이익). len > 5는 클램프로 흡수.
    """
    above = sum(1 for r in roics if r is not None and r >= ROIC_FLOOR)
    return _clamp(above / 5.0, 0.0, 1.0)


def _fcf_stability(margins):
    """FCF 마진 안정성 → [0,1] = 0.5×margin + 0.5×stability.

    결정본: margin = clamp(mean(m)/FCF_MARGIN_TARGET, 0, 1);
    cv = std(m)/|mean| (mean=0 → cv=1); stability = 1 − clamp(cv, 0, 1).
    std는 np.std(모집단 ddof=0, close_screener 관례). None(결측 연도)은
    평균/std 계산에서 제외, 유효 마진 없음 → 0.0 (fail-low).
    """
    valid = [float(m) for m in margins if m is not None]
    if not valid:
        return 0.0
    mean = float(np.mean(valid))
    margin = _clamp(mean / FCF_MARGIN_TARGET, 0.0, 1.0)
    if mean == 0.0:
        cv = 1.0
    else:
        cv = float(np.std(valid)) / abs(mean)
    stability = 1.0 - _clamp(cv, 0.0, 1.0)
    return 0.5 * margin + 0.5 * stability


def _vol_score(roics):
    """ROIC 이익 변동성 점수 → [0,1] (낮을수록 좋음).

    결정본: clamp(1 − std(ROIC)/ROIC_STD_CAP, 0, 1) — std 15% 이상 → 0.0.
    None(결측 연도)은 std 계산에서 제외, 유효 ROIC 없음 → 0.0 (fail-low).
    """
    valid = [float(r) for r in roics if r is not None]
    if not valid:
        return 0.0
    return _clamp(1.0 - float(np.std(valid)) / ROIC_STD_CAP, 0.0, 1.0)


def _debt_score(debt_ratio):
    """부채비율 점수 → [0,1] (낮을수록 좋음).

    결정본: clamp((DEBT_SCORE_HI − debt_ratio)/100, 0, 1) —
    50% → 1.0, 150% → 0.0, 200% → 0.0 (단조 감소). None → 0.0 (fail-low).
    """
    if debt_ratio is None:
        return 0.0
    return _clamp((DEBT_SCORE_HI - float(debt_ratio)) / 100.0, 0.0, 1.0)


def _annual_roic(row):
    """연간 ROIC proxy = NOPAT / 투하자본 → float|None (1개 연도).

    결정본: NOPAT_y = operating_profit_y × (1 − tax_y);
    tax_y = clamp(1 − net_income_y/operating_profit_y, 0.0, 0.35)
    (operating_profit ≤ 0 → tax_y = 0.25; net_income 결측 → tax_y = 0.25 기본세율);
    IC_y = total_equity_y × (1 + debt_ratio_y/100) (부채비율 % 기준).
    operating_profit/total_equity/debt_ratio 결측(None) 또는 IC ≤ 0 →
    None (계산 불가 — 호출자가 결측 연도로 처리).
    """
    op = row.get("operating_profit")
    equity = row.get("total_equity")
    debt = row.get("debt_ratio")
    if op is None or equity is None or debt is None:
        return None
    if op <= 0.0 or row.get("net_income") is None:
        tax = 0.25
    else:
        tax = _clamp(1.0 - row["net_income"] / op, 0.0, 0.35)
    ic = equity * (1.0 + debt / 100.0)
    if ic <= 0.0:
        return None
    return op * (1.0 - tax) / ic


def _annual_fcf_margin(row):
    """연간 FCF 마진 proxy = operating_cash_flow / revenue → float|None.

    capex 컬럼 부재(01_schema.sql)로 영업현금흐름/매출 proxy (PLAN §8 데이터 갭).
    operating_cash_flow/revenue 결측(None) 또는 revenue ≤ 0 → None (계산 불가).
    OCF 음수는 유효(실제 마이너스 FCF, 평균/std에 그대로 반영).
    """
    ocf = row.get("operating_cash_flow")
    rev = row.get("revenue")
    if ocf is None or rev is None or rev <= 0.0:
        return None
    return ocf / rev


def score_quality(fundamentals):
    """Quality 축 → [0,1] (결정본 §Quality 가중 합).

    quality = 0.35×roic_consistency + 0.20×fcf_score + 0.20×vol_score
              + 0.25×debt_score  (QUALITY_W 순서)
    fundamentals는 report_date DESC(최신 우선, load_fundamentals) — 부채비율은
    최신 행(첫 행) 사용.

    fail-low 규칙 (정확히): 아래 중 하나라도 해당하면 0.0을 반환한다.
    (1) fundamentals 행 수 < MIN_FUND_YEARS (데이터 부족)
    (2) 유효 ROIC 0건 — 전 연도 NOPAT/투하자본 계산 불가 (vol_score 불가)
    (3) 유효 FCF 마진 0건 — 전 연도 OCF/revenue 결측 (fcf_score 불가)
    (4) 최신 부채비율 None (debt_score 불가)
    """
    if len(fundamentals) < MIN_FUND_YEARS:
        return 0.0
    roics = [_annual_roic(row) for row in fundamentals]
    margins = [_annual_fcf_margin(row) for row in fundamentals]
    debt_latest = fundamentals[0].get("debt_ratio")
    if (not any(r is not None for r in roics)
            or not any(m is not None for m in margins)
            or debt_latest is None):
        return 0.0
    roic_consistency = _roic_consistency(roics)
    fcf_score = _fcf_stability(margins)
    vol_score = _vol_score(roics)
    debt_score = _debt_score(debt_latest)
    quality = (QUALITY_W[0] * roic_consistency
               + QUALITY_W[1] * fcf_score
               + QUALITY_W[2] * vol_score
               + QUALITY_W[3] * debt_score)
    return _clamp(quality, 0.0, 1.0)


# ── Valuation 축 순수 함수 (todo 4, §결정본 Valuation) ─────────────────
# 전부 DB 접근 없는 순수 함수 — dict 픽스처로 직접 테스트 가능.
# 결측(None)은 fail-low/중립 처리, 마법 숫자 없음(상수 참조만).
# 프록시 한계(§결정본 문서화): FCF yield 분모는 현재 market_cap(자기 과거 대비
# z-score의 문서화된 proxy), MoS의 norm_fcf는 capex 컬럼 부재(01_schema.sql)로
# operating_cash_flow 그대로 사용.


def _fcf_yield_z(yields):
    """FCF yield 시계열 z-score → [0,1] (최신 yield가 클수록 좋음).

    결정본: z = (yield_latest − mean) / (std + 1e-9), z = clamp(z, −2, 2),
    z_score = (z + 2) / 4 — 최고 yield → 1.0, 최저 → 0.0, ±2 클램프.
    입력은 오래된 연도 → 최신 연도 순서(최신 = 마지막 요소), None 결측 제외.
    mean/std는 np.mean/np.std(ddof=0, close_screener 관례) — 자기 과거 대비
    z-score이므로 모집단 std 그대로.
    유효 yield < 2개 → 0.0 (z-score 불가, fail-low).
    std == 0 (전부 동일) → z = 0 → 0.5 (자기 과거 대비 차이 없음 — 중립).
    1e-9: z-score 분모 안정화 상수 (결정본 허용 — std 0 방지).
    """
    valid = [y for y in yields if y is not None]
    if len(valid) < 2:
        return 0.0
    mean = float(np.mean(valid))
    std = float(np.std(valid))  # ddof=0 (모집단, close_screener 관례)
    if std == 0.0:
        return 0.5
    z = (valid[-1] - mean) / (std + 1e-9)  # 1e-9: 분모 안정화
    z = _clamp(z, -2.0, 2.0)
    return (z + 2.0) / 4.0


def _percentile(own, others):
    """own이 others 교차단면에서 차지하는 백분위 [0,100] (낮을수록 좋음).

    company_features.py:122-127 패턴: rank = sum(1 for v in others if v <= own);
    백분위 = rank / len(others) × 100. others 빈 리스트는 호출자가 0.5 중립으로
    처리 (여기서는 호출자 보장).
    """
    rank = sum(1 for v in others if v <= own)
    return rank / len(others) * 100.0


def _pct_score(per_pct, pbr_pct):
    """업종 PER/PBR 백분위 점수 → [0,1] (백분위 낮을수록 좋음).

    결정본: pct_score = 1 − (per_pct/100 + pbr_pct/100) / 2, [0,1] 클램프.
    백분위 0 → 1.0 (업종 내 최저 PER/PBR), 백분위 100 → 0.0.
    """
    return _clamp(1.0 - (per_pct / 100.0 + pbr_pct / 100.0) / 2.0, 0.0, 1.0)


def _mos_score(norm_fcf, market_cap):
    """단순 DCF 안전마진(MoS) 점수 → [0,1].

    결정본: intrinsic = norm_fcf × (1 + MOF_G) / (MOF_WACC − MOF_G)
    (MOF_WACC > MOF_G — 분모 0 아님); MoS = intrinsic / market_cap − 1;
    mos_score = clamp(MoS / MOF_FULL, 0, 1) — MoS ≤ 0 → 0.0, MoS ≥ 50% → 1.0.
    프록시 한계: norm_fcf는 capex 컬럼 부재(01_schema.sql)로 operating_cash_flow
    그대로 사용 (FCF 대신 OCF — §결정본 문서화된 proxy).
    norm_fcf None/≤ 0 또는 market_cap None/≤ 0 → 0.0 (fail-low).
    """
    if norm_fcf is None or norm_fcf <= 0.0:
        return 0.0
    if market_cap is None or market_cap <= 0.0:
        return 0.0
    intrinsic = norm_fcf * (1.0 + MOF_G) / (MOF_WACC - MOF_G)
    mos = intrinsic / market_cap - 1.0
    return _clamp(mos / MOF_FULL, 0.0, 1.0)


def score_valuation(fundamentals, peers, market_cap):
    """Valuation 축 → [0,1] (결정본 §Valuation 가중 합).

    valuation = 0.40×z_score + 0.30×pct_score + 0.30×mos_score (VALUATION_W 순서)
    fundamentals는 report_date DESC(최신 우선, load_fundamentals) — PER/PBR·OCF는
    최신 행(첫 행) 기준. market_cap은 load_stock_meta 출력(float|None),
    peers는 load_sector_peers 출력([{stock_code, per, pbr}], None 가능).

    - z 항: FCF yield = operating_cash_flow / market_cap (현재 market_cap 분모 —
      자기 과거 대비 z-score의 문서화된 proxy) 시계열을 오래된→최신 순서로
      _fcf_yield_z에 전달 (유효 yield < 2 → 0.0, fail-low).
    - pct 항: 최신 행 per/pbr의 업종 내 백분위(낮을수록 좋음, company_features
      패턴). 피어 부재(None/빈 리스트)·per/pbr 값 부재(빈 리스트)·
      own per/pbr None/≤ 0 → 0.5 중립 (결정본 "섹터/피어 부재 → 0.5 중립").
    - mos 항: 최근 3년(fundamentals[:3]) OCF 중앙값 = norm_fcf로 단순 DCF MoS.
      최근 3년 OCF 0건 → mos 항 0.0.

    fail-low 규칙 (정확히): 아래 중 하나라도 해당하면 0.0을 반환한다.
    (1) fundamentals 행 수 < MIN_FUND_YEARS (데이터 부족)
    (2) market_cap None 또는 ≤ 0 (yield·MoS 분모 불가)
    (3) 전 연도 operating_cash_flow가 전부 None (z·mos 항 불가)
    """
    if len(fundamentals) < MIN_FUND_YEARS:
        return 0.0
    if market_cap is None or market_cap <= 0.0:
        return 0.0
    if not any(r.get("operating_cash_flow") is not None for r in fundamentals):
        return 0.0
    # z 항 — 오래된→최신 순서 FCF yield 시계열 (최신 = 마지막 요소)
    yields_chrono = [
        ocf / market_cap
        for row in reversed(fundamentals)
        if (ocf := row.get("operating_cash_flow")) is not None
    ]
    z_score = _fcf_yield_z(yields_chrono)
    # pct 항 — 최신 행 per/pbr의 업종 백분위 (낮을수록 좋음)
    own_per = fundamentals[0].get("per")
    own_pbr = fundamentals[0].get("pbr")
    if (not peers
            or own_per is None or own_per <= 0.0
            or own_pbr is None or own_pbr <= 0.0):
        pct_score = 0.5
    else:
        per_vals = [p["per"] for p in peers if p.get("per") is not None]
        pbr_vals = [p["pbr"] for p in peers if p.get("pbr") is not None]
        if not per_vals or not pbr_vals:
            pct_score = 0.5
        else:
            per_pct = _percentile(own_per, per_vals)
            pbr_pct = _percentile(own_pbr, pbr_vals)
            pct_score = _pct_score(per_pct, pbr_pct)
    # mos 항 — 최근 3년 OCF 중앙값 → 단순 DCF MoS
    ocfs = [r.get("operating_cash_flow") for r in fundamentals[:3]
            if r.get("operating_cash_flow") is not None]
    if not ocfs:
        mos_score = 0.0
    else:
        norm_fcf = float(np.median(ocfs))
        mos_score = _mos_score(norm_fcf, market_cap)
    valuation = (VALUATION_W[0] * z_score
                 + VALUATION_W[1] * pct_score
                 + VALUATION_W[2] * mos_score)
    return _clamp(valuation, 0.0, 1.0)


# ── Catalyst 축 순수 함수 (todo 5, §결정본 Catalyst) ──────────────────
# 전부 DB 접근 없는 순수 함수 — dict 픽스처로 직접 테스트 가능.
# 결측(None)은 중립 처리(감정·중요도), 미지 이벤트 타입은 가중 0.0, 마법 숫자 없음.


def _as_date(v):
    """created_at 정규화 → date (date/datetime/ISO str 입력).

    datetime → v.date(); date → 그대로; str → date.fromisoformat(str(v)[:10])
    (ISO 'YYYY-MM-DD' 접두사만 사용 — 시각·타임존이 붙은 문자열도 허용).
    그 외 타입 → ValueError.
    """
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        return date.fromisoformat(str(v)[:10])
    raise ValueError("created_at 정규화 불가 타입: %r" % type(v))


def _catalyst_raw(events, as_of):
    """촉매 이벤트 원점수 (결정본 §Catalyst 가중 합, 정규화 전).

    결정본: event_weight_i = W(event_type) × (0.5 + 0.5×importance)
             × exp(−days_ago / CATALYST_HALF_LIFE);
    catalyst_raw = Σ event_weight_i (윈도우·감정 필터 통과 이벤트만).

    - W = CATALYST_EVENT_WEIGHTS.get(event_type, 0.0) — 미지 타입 → 0.0.
      0.00 가중 10종은 크레딧 0 기여 (베토 판정은 Todo 6 몫).
    - 윈도우: as_of − CATALYST_DAYS일 ≤ created_at ≤ as_of. 계획 §결정본은
      "created_at >= as_of − 182일"만 명시하나, as_of 이후 이벤트는 미래 정보
      (룩어헤드)라 상한도 적용 — §결정본 편차, 여기 명시.
    - 감정 필터: sentiment_score < 0 → 제외, None → 포함 (결정본).
    - importance None → 0.5 중립 (DB 컬럼 DECIMAL(5,4) [0,1] — 스케일 범위 [0.5, 1.0]).
    - days_ago = (as_of − created_at일).days — 윈도우 필터로 ≥ 0 보장.

    필터 통과 이벤트 0건 → 0.0 (촉매 필수 원칙 — PLAN §5.1).
    """
    cutoff = as_of - timedelta(days=CATALYST_DAYS)
    total = 0.0
    for ev in events:
        created = _as_date(ev.get("created_at"))
        if created < cutoff or created > as_of:
            continue
        sentiment = ev.get("sentiment_score")
        if sentiment is not None and sentiment < 0:
            continue
        weight = CATALYST_EVENT_WEIGHTS.get(ev.get("event_type"), 0.0)
        if weight == 0.0:
            continue
        importance = ev.get("importance")
        importance_scale = 0.5 + 0.5 * (importance if importance is not None else 0.5)
        days_ago = (as_of - created).days
        decay = math.exp(-days_ago / CATALYST_HALF_LIFE)
        total += weight * importance_scale * decay
    return total


def score_catalyst(events, as_of):
    """Catalyst 축 → [0,1] (결정본 §Catalyst 정규화).

    결정본: catalyst_score = clamp(catalyst_raw / CATALYST_NORM, 0, 1).
    이벤트 0건 → catalyst_raw 0.0 → 점수 0.0 (촉매 필수 원칙 — PLAN §5.1).
    CATALYST_EVENT_WEIGHTS 0.00 타입(유상증자·감자/CB·BW/규제/소송/부도·상폐·거래정지/
    리콜/거시경제/시장지수·유동성/자연재해/기타)은 촉매 크레딧 0 기여
    (베토 판정은 Todo 6 몫).
    """
    return _clamp(_catalyst_raw(events, as_of) / CATALYST_NORM, 0.0, 1.0)


# ── 하드 베토 5종 순수 함수 (todo 6, §결정본 하드 베토) ────────────────
# DB 접근·로깅 없는 순수 함수 — ctx dict만 받아 트리거된 사유 리스트 반환
# (빈 리스트 = 통과). 로그는 호출자(run_screener, Todo 8) 몫 — Metis n3.


def apply_vetoes(ctx):
    """하드 베토 5종 판정 → 트리거된 사유 문자열 리스트 (결정본 §하드 베토 5종).

    ctx dict만 받는 순수 함수 — 내부 로깅 없음 (로그는 호출자 run_screener 몫).
    빈 리스트 = 통과. 반환 순서는 아래 판정 순서 고정.
    ctx 키: debt_ratio(float|None), audit_opinion(str|None), events(load_events
    출력 shape: event_type/sentiment_score/importance/core_event_text/created_at),
    latest_trade_date(date|str|None), signal_date(date|str) — date류는 _as_date 정규화.

    1. 부채비율_한도: debt_ratio > DEBT_VETO. None → 통과 (fail-open).
    2. 감사의견_비적정한정: opinion에 AUDIT_OPINION_VETO 키워드 부분 포함.
       None/빈 문자열 → 통과 (fail-open) — 스키마에 감사의견 컬럼 없음
       (PLAN §8 데이터 갭), M5 DART 확장 전 한계.
    3. 횡령_분식: 윈도우 내 event_type in ('규제','소송') 이벤트의 core_event_text가
       FRAUD_KEYWORDS 중 하나라도 부분 포함. core_event_text None → 해당 이벤트 스킵.
    4. CB_남발: 윈도우 내 event_type == 'CB·BW' 건수 >= CB_VETO_COUNT.
    5. 거래정지: (a) 윈도우 내 event_type == '부도·상폐·거래정지' 존재, 또는
       (b) (signal_date − latest_trade_date).days > TRADE_GAP_DAYS
       (거래 공백 proxy — 휴장 무시 단순화, 가정 5). latest_trade_date None →
       (b) 스킵 (fail-open).

    이벤트 판정(3~5)은 6개월 윈도우 [signal_date − CATALYST_DAYS일, signal_date]
    내 이벤트만 사용 (결정본 "6개월 내" — CATALYST_DAYS 재사용으로 이벤트 윈도우
    통일). signal_date 없음 → 5번 판정 스킵, 나머지 판정은 윈도우 없이 수행
    (.get 사용 — KeyError 없음).
    """
    vetoes = []
    debt_ratio = ctx.get("debt_ratio")
    if debt_ratio is not None and debt_ratio > DEBT_VETO:
        vetoes.append("부채비율_한도")
    opinion = ctx.get("audit_opinion")
    if opinion and any(k in str(opinion) for k in AUDIT_OPINION_VETO):
        vetoes.append("감사의견_비적정한정")
    signal_date = ctx.get("signal_date")
    signal = _as_date(signal_date) if signal_date is not None else None
    events = ctx.get("events") or []
    fraud = False
    cb_count = 0
    halt = False
    for ev in events:
        created = _as_date(ev.get("created_at"))
        if signal is not None and (
                created < signal - timedelta(days=CATALYST_DAYS) or created > signal):
            continue  # 6개월 윈도우 밖(룩어헤드 포함) 이벤트 스킵
        etype = ev.get("event_type")
        if etype in ("규제", "소송"):
            text = ev.get("core_event_text")
            if text and any(k in str(text) for k in FRAUD_KEYWORDS):
                fraud = True
        if etype == "CB·BW":
            cb_count += 1
        if etype == "부도·상폐·거래정지":
            halt = True
    if fraud:
        vetoes.append("횡령_분식")
    if cb_count >= CB_VETO_COUNT:
        vetoes.append("CB_남발")
    if signal is not None:  # 5. 거래정지 — signal_date 없으면 판정 스킵 (fail-open)
        gap = halt
        if not gap:
            latest = ctx.get("latest_trade_date")
            if latest is not None:
                gap = (signal - _as_date(latest)).days > TRADE_GAP_DAYS
        if gap:
            vetoes.append("거래정지")
    return vetoes

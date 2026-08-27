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
import os
import re
import sys
from datetime import datetime, date

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

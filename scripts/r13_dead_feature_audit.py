#!/usr/bin/env python3
"""R13 — '원천 부재 피처 35개'를 한 덩어리로 세지 않고 **원인별로 분해**한다. (읽기 전용)

WHY: 북극성 지표(살아/죽은)는 `feature_coverage.nonzero_ratio > 0` 만 본다. 그래서 '죽은 35개'가
① 원천 자체가 없는 피처(발굴·승인 필요) ② 원천은 있는데 빌더가 안 돌아 값이 0 인 피처(부활 후보)
③ 원천이 시장 단위라 종목 횡단면 피처로 쓸 수 없는 피처(계약 #6) ④ 커버리지가 얇은 피처
를 구분하지 못한다. 결과적으로 "죽은 35"라는 숫자는 (a) 실제로는 살릴 수 있는 피처를 죽은 것으로
보이게 하고 (b) 되살릴 수 없는 피처를 '부활 대상'으로 착각하게 한다. R13 의 질문은 "무엇을 더
수집해야 하는가"이므로, 답은 분해표여야 한다.

⚠ 이 스크립트는 `feature_coverage` 에 아무것도 쓰지 않는다(R16 과 동일 이유 — 현재상태 테이블을
   덮으면 전역 기준선이 뒤집힌다). 산출물은 JSON + 표뿐이다.

분류는 두 축의 교차검증이다: ① 사람이 읽는 매핑(FEATURE_MAP) ② DB 실측 프로브(SOURCES).
매핑이 '원천 있음'이라는데 프로브가 그 테이블을 못 찾으면 **미분류로 떨어뜨린다** — 죽은 피처가
늘었는데 아무도 모르는 상태(조용한 공전)를 check 가 잡을 수 있게 하려는 것이다.

사용:
  /usr/bin/python3 scripts/r13_dead_feature_audit.py           # 분해표 + JSON
  /usr/bin/python3 scripts/r13_dead_feature_audit.py --check   # 마지막 줄에 미분류 개수(정수)
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import psycopg2  # noqa: E402
import researcher_cycle as rc  # noqa: E402  (호스트 DB 좌표 정규화 재사용 — 로직 중복 금지)

OUT = os.path.join(PROJ, "data/reports/r13_dead_feature_audit.json")

# 분류 버킷
ABSENT = "source_absent"      # 원천 테이블/컬럼 자체가 없다 → 발굴·승인 필요
COVERAGE = "coverage_low"     # 원천 있으나 종목/기간 커버리지가 얇다 → 수집 확대
BUILDABLE = "buildable"       # 원천 있고 종목 레벨로 만들 수 있다 → 빌더/스냅샷 문제(부활 후보)
MKTLEVEL = "market_level"     # 원천이 시장 단위(종목 변별력 0) → 계약 #6: 횡단면 피처 부적격

# ① 매핑: 피처 → (버킷, 원천 키, 근거)
FEATURE_MAP = {
    # 원천 있음(종목 레벨) — 빌더가 안 돌았거나 커버리지 스냅샷이 낡았다
    "atr_pct": (BUILDABLE, "market_data", "일봉 종목 레벨 — ATR 계산 가능"),
    "quality_beta": (BUILDABLE, "market_data", "일봉 종목 레벨 — 베타 계산 가능"),
    "quality_price_volatility_60d": (BUILDABLE, "market_data", "일봉 종목 레벨 — 60일 변동성"),
    "authenticity_avg": (BUILDABLE, "news_analysis", "authenticity_score 결측 아님"),
    "similarity_std": (BUILDABLE, "stock_vectors", "종목 벡터 존재"),
    "twin_count": (BUILDABLE, "stock_vectors", "종목 벡터 존재"),
    "twin_avg_correlation": (BUILDABLE, "stock_vectors", "종목 벡터 존재"),
    "bayes_momentum_1d": (BUILDABLE, "stock_vectors", "종목 벡터 존재"),
    "bayes_momentum_5d": (BUILDABLE, "stock_vectors", "종목 벡터 존재"),
    "bayes_volatility": (BUILDABLE, "stock_vectors", "종목 벡터 존재"),
    "bayes_gain_uncertainty": (BUILDABLE, "stock_vectors", "종목 벡터 존재"),
    # 원천 있으나 커버리지가 얇다
    "short_interest_ratio": (COVERAGE, "krx_short_selling", "공매도 종목 수 부족"),
    "days_to_cover": (COVERAGE, "krx_short_selling", "공매도 종목 수 부족"),
    "sector_count": (COVERAGE, "stocks", "섹터 결측 종목 존재"),
    "sector_momentum": (COVERAGE, "stocks", "섹터 결측 종목 존재"),
    "sns_bot_filtered_count": (COVERAGE, "sns_post_features", "SNS 피처 적재일 얇음"),
    "sns_sentiment_score_best_lag": (COVERAGE, "sns_post_features", "SNS 피처 적재일 얇음"),
    "sns_sentiment_score_lag_sign": (COVERAGE, "sns_post_features", "SNS 피처 적재일 얇음"),
    # 원천이 시장 단위 → 종목 횡단면 피처로 쓸 수 없다(계약 #6)
    "basis": (MKTLEVEL, "krx_derivatives", "선물 지수 단위 — stock_code 없음"),
    "basis_change_5d": (MKTLEVEL, "krx_derivatives", "선물 지수 단위 — stock_code 없음"),
    "futures_premium": (MKTLEVEL, "krx_derivatives", "선물 지수 단위 — stock_code 없음"),
    "derivatives_volume": (MKTLEVEL, "krx_derivatives", "선물 지수 단위 — stock_code 없음"),
    "program_trading_ratio": (MKTLEVEL, "krx_program_trading", "시장 단위 집계 — stock_code 없음"),
    "event_macro_5d": (MKTLEVEL, "economic_events", "거시 이벤트는 전 종목 동일값"),
    "event_market_liquidity_5d": (MKTLEVEL, "economic_events", "유동성 이벤트는 전 종목 동일값"),
    # 원천 자체가 없다
    "credit_balance_change": (ABSENT, "credit_balance", "신용잔고 테이블 없음"),
    "credit_spread": (ABSENT, "credit_balance", "신용잔고 테이블 없음"),
    "margin_balance_change": (ABSENT, "credit_balance", "신용융자 테이블 없음"),
    "etf_flow_5d": (ABSENT, "etf_flow", "ETF 수급 테이블 없음"),
    "theme_count": (ABSENT, "theme", "테마 테이블 없음(그래프 전용)"),
    "theme_exposure_5d": (ABSENT, "theme", "테마 테이블 없음(그래프 전용)"),
    "theme_max_relevance": (ABSENT, "theme", "테마 테이블 없음(그래프 전용)"),
    "theme_momentum": (ABSENT, "theme", "테마 테이블 없음(그래프 전용)"),
    "value_ncav": (ABSENT, "financial_statements", "유동자산·유동부채 컬럼 없음"),
    "institution_ownership_pct": (ABSENT, "ownership", "KIS 현재가에 기관 지분율 필드 없음(R2 확정)"),
}

# ② 프로브: 원천 키 → 실측 SQL. 마지막 값이 없거나 0행이면 매핑이 낡은 것이다.
PROBES = {
    "market_data": "SELECT COUNT(*)::text||'|'||MIN(trade_date)::text||'|'||MAX(trade_date)::text FROM market_data",
    "news_analysis": ("SELECT COUNT(*) FILTER (WHERE authenticity_score IS NOT NULL)::text||'/'||COUNT(*)::text"
                      "||'|'||MAX(published_at)::date::text FROM news_analysis"),
    "stock_vectors": ("SELECT COUNT(*)::text||'|'||COUNT(DISTINCT stock_code)::text||'|'"
                      "||MAX(updated_at)::date::text FROM stock_vectors"),
    "krx_short_selling": ("SELECT COUNT(*)::text||'|'||COUNT(DISTINCT stock_code)::text||'|'"
                          "||MAX(trade_date)::text FROM krx_short_selling"),
    "stocks": "SELECT COUNT(*) FILTER (WHERE sector IS NOT NULL AND sector<>'')::text||'/'||COUNT(*)::text FROM stocks",
    "sns_post_features": ("SELECT COUNT(*)::text||'|'||COUNT(DISTINCT stock_code)::text||'|'"
                          "||COUNT(DISTINCT trade_date)::text FROM sns_post_features"),
    "krx_derivatives": ("SELECT (has_sc.stock_code_col)::text||'|'||COUNT(*)::text||'|'||MAX(trade_date)::text"
                        " FROM krx_derivatives, (SELECT COUNT(*)>0 AS stock_code_col FROM information_schema.columns"
                        " WHERE table_name='krx_derivatives' AND column_name='stock_code') has_sc"
                        " GROUP BY has_sc.stock_code_col"),
    "krx_program_trading": ("SELECT (has_sc.stock_code_col)::text||'|'||COUNT(*)::text||'|'||MAX(trade_date)::text"
                            " FROM krx_program_trading, (SELECT COUNT(*)>0 AS stock_code_col FROM information_schema.columns"
                            " WHERE table_name='krx_program_trading' AND column_name='stock_code') has_sc"
                            " GROUP BY has_sc.stock_code_col"),
    "economic_events": "SELECT COUNT(*)::text||'|'||COUNT(DISTINCT category)::text FROM economic_events",
    "financial_statements": ("SELECT (has_ca.current_assets)::text||'|'||COUNT(*)::text FROM financial_statements,"
                             " (SELECT COUNT(*)>0 AS current_assets FROM information_schema.columns"
                             " WHERE table_name='financial_statements' AND column_name IN ('current_assets','total_current_assets')) has_ca"
                             " GROUP BY has_ca.current_assets"),
    "ownership": ("SELECT (has_col.institution_pct)::text||'|'||COUNT(*)::text FROM ownership,"
                  " (SELECT COUNT(*)>0 AS institution_pct FROM information_schema.columns"
                  " WHERE table_name='ownership' AND column_name='institution_ownership_pct') has_col"
                  " GROUP BY has_col.institution_pct"),
    # 아래 셋은 '테이블이 존재하는가' 자체를 묻는다(absence 근거)
    "credit_balance": "SELECT COUNT(*)::text FROM information_schema.tables WHERE table_name ~ 'credit|margin'",
    "etf_flow": "SELECT COUNT(*)::text FROM information_schema.tables WHERE table_name ~ 'etf'",
    "theme": "SELECT COUNT(*)::text FROM information_schema.tables WHERE table_name ~ 'theme|topic'",
}

# 원천이 있어야 하는 버킷(프로브 실패 시 미분류로 떨어뜨린다)
NEEDS_SOURCE = (BUILDABLE, COVERAGE, MKTLEVEL)


def query(conn, sql):
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            row = cur.fetchone()
            return "|".join("-" if v is None else str(v) for v in row)
    except Exception as exc:  # 프로브 실패는 미분류 사유가 된다(조용히 넘기지 않는다)
        # 실패한 문장은 트랜잭션을 abort 시킨다 → 다음 프로브를 위해 되돌린다(읽기전용이라 무해).
        conn.rollback()
        return "ERR:" + str(exc).splitlines()[0][:80]


def probe_all(conn):
    return {key: query(conn, sql) for key, sql in PROBES.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="마지막 줄에 미분류 개수만 출력")
    a = ap.parse_args()

    conn = psycopg2.connect(host=rc.HOST_DB_ENV["POSTGRES_HOST"], port=rc.HOST_DB_ENV["POSTGRES_PORT"],
                            user=rc.HOST_DB_ENV["POSTGRES_USER"], password=rc.HOST_DB_ENV["POSTGRES_PASSWORD"],
                            dbname=rc.HOST_DB_ENV["POSTGRES_DB"])
    conn.set_session(readonly=True)
    cur = conn.cursor()
    cur.execute("SELECT feature_name FROM feature_coverage WHERE nonzero_ratio = 0 ORDER BY feature_name")
    dead = [r[0] for r in cur.fetchall()]
    probes = probe_all(conn)

    rows, unknown = [], []
    for name in dead:
        bucket, src, why = FEATURE_MAP.get(name, (None, None, None))
        if bucket is None:
            unknown.append((name, "매핑 없음 — 원인 미확정"))
            continue
        pb = probes.get(src or "", "MISSING")
        empty = pb in ("MISSING", "ERR") or pb.startswith("0") or pb.startswith("f|0")
        if bucket in NEEDS_SOURCE and empty:
            # 매핑은 '원천 있음'이라는데 실측이 비었다 → 원인 재조사 대상
            if bucket == MKTLEVEL and src in ("krx_derivatives", "krx_program_trading") and pb.startswith("f|"):
                pass   # stock_code 없음(f) 은 시장레벨 판정의 근거 그 자체 — 정상
            else:
                unknown.append((name, f"매핑={bucket} 인데 프로브 {src}={pb} (원천 소실/드리프트)"))
                continue
        rows.append({"feature": name, "bucket": bucket, "source": src, "why": why, "probe": pb})

    buckets = {}
    for r in rows:
        buckets.setdefault(r["bucket"], []).append(r["feature"])
    report = {
        "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "dead_total": len(dead),
        "buckets": {k: {"n": len(v), "features": v} for k, v in sorted(buckets.items())},
        "unclassified": [{"feature": f, "why": w} for f, w in unknown],
        "probes": probes,
        "features": rows,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    if not a.check:
        print(f"[R13] 죽은 피처 분해 — 전체 {len(dead)}개 ({report['ts']})")
        for k in (BUILDABLE, COVERAGE, MKTLEVEL, ABSENT):
            v = buckets.get(k, [])
            print(f"  {k:14s} {len(v):2d}  {', '.join(v[:5])}{' …' if len(v) > 5 else ''}")
        for f, w in unknown:
            print(f"  ⚠ 미분류 {f}: {w}")
        print(f"  JSON: {os.path.relpath(OUT, PROJ)}")
    # check 는 **마지막 수치**로 판정된다(eval_check 는 stdout 의 마지막 숫자를 읽는다) → 개수를 마지막에.
    print(f"R13 미분류 {len(unknown)}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

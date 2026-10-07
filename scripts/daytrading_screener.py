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
  python3 scripts/daytrading_screener.py --json-out reports/daytrading_latest.json

WHY --json-out (2026-10-07, docs/spec_daytrading_feed.md §2):
  피드(data/feed/screener_latest.json)의 candidates 가 {close, swing} 뿐이라 트레이더가
  단타 후보를 평가조차 못 했다(저널 실측 2026-10-07: decisions = swing 2,483건 ·
  daytrading 0건). 이 JSON 을 reports/daytrading_latest.json 로 두면 feed_export 가
  --daytrading 기본 경로로 읽어 candidates.daytrading 을 발행한다.
  점수는 0~100 복합점수이므로 score_kind="composite" 로 명시하고(calibrated_prob 변환
  금지), 모델 확률은 model_prob 필드로 분리 보존한다(모델 미가용 시 null).
  CSV 저장 동작은 그대로 유지한다.
"""
import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

from day_trading_engine import (ChampionPredictor, DbDailyProvider,
                                OUTPUT_COLUMNS, run_screener)

try:
    from zoneinfo import ZoneInfo

    KST = ZoneInfo("Asia/Seoul")
except Exception:  # pragma: no cover
    KST = timezone(timedelta(hours=9))

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


def build_json_payload(rows, generated_at=None):
    """CSV 출력 행 리스트 → 피드 소스 JSON dict (--json-out 계약, spec_daytrading_feed.md §2).

    스키마: {"generated_at","source","items":[ {stock_code, stock_name, close_price,
    score, score_kind, signal_date, model_prob, slope_permille, volume_ratio}, ... ]}
    - score_kind 은 항상 "composite": 점수는 0~100 복합점수(칼만30+모델30+거래량20+변동성20)
      라 feed_export 가 calibrated_prob 변환 없이 그대로 발행하게 한다.
    - model_prob 는 복합점수의 구성요소인 모델 확률을 분리 보존한 값(모델 미가용이면 null).
    - slope_permille 는 kalman_slope 의 ‰ 표기(build_output_rows 가 ×1000 라운딩한 값),
      volume_ratio 는 volume_surge(평균 대비 배수)다.
    """
    items = []
    for r in rows:
        prob = None
        mp = r.get("model_prob")
        if isinstance(mp, str) and mp.strip():
            try:
                prob = float(mp)
            except ValueError:
                prob = None
        elif isinstance(mp, (int, float)):
            prob = float(mp)
        items.append({
            "stock_code": r["stock_code"],
            "stock_name": r.get("stock_name", ""),
            "close_price": round(float(r["close_price"]), 2),
            "score": round(float(r["score"]), 1),
            "score_kind": "composite",
            "signal_date": str(r["signal_date"]),
            "model_prob": None if prob is None else round(prob, 4),
            "slope_permille": round(float(r["kalman_slope"]), 3),
            "volume_ratio": round(float(r["volume_surge"]), 2),
        })
    return {
        "generated_at": (generated_at or datetime.now(KST)).isoformat(timespec="seconds"),
        "source": "analyist_dd",
        "items": items,
    }


def write_json_out(payload, path):
    """--json-out 원자적 저장(tmp+os.replace) — 재실행 안전(멱등) + 폴링 중 반쪽 파일 방지."""
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return path


def _record_claim(source_rows, claimed_rows, persisted_rows, note=""):
    """단타스크리너 1회 실행의 자기신고(3분리)를 dq_runner_claim 에 남긴다.

    source_rows   : 엔진(run_screener)이 만들어낸 후보 행수(수신)
    claimed_rows  : build_output_rows 가 만들어낸 행수(파서 생성)
    persisted_rows: 실제 저장한 행수(json items)
    자기신고 실패가 스크리닝을 깨면 안 된다(dq_claim 설계 규약) → WARN 만 남긴다.
    """
    try:
        from dq_claim import _open_conn, record_claim  # noqa: PLC0415 — psycopg2 지연 import 규약
        conn = _open_conn()
        try:
            record_claim(conn, runner="daytrading_screener",
                         table_name="daytrading_candidates",
                         source_rows=source_rows, claimed_rows=claimed_rows,
                         persisted_rows=persisted_rows, note=note)
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("자기신고(3분리) 기록 실패(%s: %s) — 스크리닝에는 영향 없음",
                       type(e).__name__, e)


def main():
    ap = argparse.ArgumentParser(
        description="단타스크리너 — 칼만 노이즈 제거 + 챔피언 모델 기반 당일 매수 후보 선별")
    ap.add_argument("--top-n", type=int, default=20, help="상위 N개 후보 (기본 20)")
    ap.add_argument("--date", type=str, default=None, help="시그널 기준일 YYYY-MM-DD (기본: 최근 거래일)")
    ap.add_argument("--output", type=str, default=None, help="출력 CSV 경로")
    ap.add_argument("--json-out", type=str, default=None,
                    help="피드 소스 JSON 경로(spec_daytrading_feed.md §2 — "
                         "reports/daytrading_latest.json 로 두면 feed_export 가 발행)")
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
            # 후보가 없어도 JSON 산출물은 빈 items 로 갱신한다: 이전 산출물이 남아 있으면
            # feed_export 가 **어제 후보**를 신선한 것처럼 발행한다(신선도 게이트는
            # generated_at 만 본다 — "오늘은 0건"이라는 사실 자체가 발행돼야 경로가 닫힌다).
            if args.json_out:
                write_json_out(build_json_payload([]), args.json_out)
                logger.info(f"JSON 저장(빈 items): {args.json_out}")
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

        json_items = 0
        if args.json_out:
            write_json_out(build_json_payload(rows), args.json_out)
            json_items = len(rows)
            logger.info(f"JSON 저장: {args.json_out} ({json_items}건)")
        _record_claim(source_rows=len(ranked), claimed_rows=len(rows),
                      persisted_rows=json_items or len(rows),
                      note=f"json_out={args.json_out} csv={out_path} "
                           f"top_n={args.top_n} signal_date={rows[0]['signal_date']}")

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

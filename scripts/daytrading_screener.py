#!/usr/bin/env python3
"""단타스크리너 (Day-Trading Screener) — 칼만 노이즈 제거 + 챔피언 모델 · 당일 매수 후보 선별.

HFT 시장조성이 만드는 스프레드 바운스 노이즈를 칼만(RTS) 평활화로 제거한 뒤,
노이즈 없는 추세 강도 + 챔피언 모델 예측 + 거래량/변동성 정합으로 당일 매수 후보를
점수화(0~100)하여 랭킹한다. (docs/단타스크리너_PLAN.md)

전략 요약:
- 유니버스: KOSDAQ 전체 (market_data 20거래일 이상 보유)
- 노이즈 제거: 일봉 종가열 → Rauch–Tung–Striebel 칼만 평활화 (O(n))
- 점수: 칼만 추세 25 + 거래량 급증 30 + 변동성 정합 45 (모델 확률은 참고용, 점수 기여 없음)
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

레짐 게이트(2026-11-20 추가, docs/spec_regime_filter.md §2):
  WHY — docs/EXP_rule_expectancy_verdict.md 실측: 2026년 KOSDAQ −31.7%(945 → 646),
  유니버스 기준선 −0.691%/일(비용 후), 상승 추격 규칙 R3/R4/R5 초과수익 전부 음.
  레짐이 나쁠 때 롱 진입을 계속하는 것 자체가 손실의 주원인 → 진입을 멈추는 최소 장치.
  scripts/market_regime.py 가 쓰는 data/state/market_regime.json 의 state=="risk_off"
  이고 asof 가 오늘(또는 직전 거래일)이면 후보를 만들지 않고 빈 결과를 쓰되
  reason("regime_off: KOSDAQ close<ma20" 형태)을 콘솔·JSON·자기신고에 남긴다
  (조용히 0건을 내면 "후보 없음"과 구분되지 않으므로 — 스펙 §2 ※).
  파일 없음·state unknown·오래된 파일은 fail-open(경고 1줄 + 기존 동작 유지).
  --regime-block off 로 해제 가능. --dry-run 은 산출물(CSV/JSON)·자기신고를
  기록하지 않고 콘솔 출력만 한다(스펙 §4 검증용).
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

# 레짐 게이트(spec_regime_filter.md §2) — 기본 경로는 리포 루트 기준
# data/state/market_regime.json (scripts/market_regime.py 의 기본 --json-out 과 동일).
REGIME_DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                   "data", "state", "market_regime.json")
_HOLIDAYS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "data", "krx_holidays.json")


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
            "trading_value": (None if pd.isna(r.get("trading_value")) else float(r["trading_value"])),
            "volume": (None if pd.isna(r.get("volume")) else float(r["volume"])),
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
            "trading_value": (None if r.get("trading_value") is None else float(r["trading_value"])),
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


def _load_universe_codes(path):
    """유니버스 압축 파일 → set[str] 또는 None (fail-open).

    docs/spec_rank_universe.md §2: 파일이 없거나 ok:false/codes:[] 면 경고만 남기고
    **제한 없이** 전체 유니버스로 실행한다(피드가 비어 매매가 멈추는 것보다 낫다).
    """
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except FileNotFoundError:
        logger.warning("유니버스 파일 없음(%s) — 전체 유니버스로 실행", path)
        return None
    except Exception as exc:  # noqa: BLE001 (fail-open: 어떤 파손이든 전체 유니버스)
        logger.warning("유니버스 파일 파손(%s: %s) — 전체 유니버스로 실행", path, exc)
        return None
    codes = payload.get("codes") if isinstance(payload, dict) else None
    if not isinstance(codes, list) or not codes:
        logger.warning("유니버스 파일 비어 있음(ok=%s) — 전체 유니버스로 실행",
                       payload.get("ok") if isinstance(payload, dict) else "?")
        return None
    out = {str(c).strip() for c in codes if str(c).strip()}
    logger.info("유니버스 압축 파일 적용: %d 코드 (%s)", len(out), path)
    return out


def _load_rank_band(path):
    """상승률 대역 파일 → (exclude_codes, bonus_codes) 또는 (None, None).

    docs/spec_rank_universe.md §2(개정): 교집합 압축이 아니라
      · excluded_codes(+12~30%) → 유니버스에서 **제외**(추격 방지)
      · codes(2~12%)            → **정렬 가점**(score 값 불변)
    파일이 없거나 파손이면 (None, None) → 기존 동작(전체 유니버스).
    """
    if not path:
        return None, None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception as exc:  # noqa: BLE001 (fail-open)
        logger.warning("상승률 대역 파일 사용 불가(%s: %s) — 기존 동작", path, exc)
        return None, None
    if not isinstance(payload, dict):
        return None, None
    excl = payload.get("excluded_codes") or []
    bonus = payload.get("codes") or []
    logger.info("상승률 대역 적용: 가점 %d코드 · 추격배제 %d코드 (ok=%s)",
                len(bonus), len(excl), payload.get("ok"))
    return (set(map(str, excl)) or None), (set(map(str, bonus)) or None)


def _default_csv_path():
    """기본 CSV 경로 — data/reports/daytrading_candidates_<타임스탬프>.csv (기존 동작 그대로)."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "data", "reports"
    )
    return os.path.join(out_dir, f"daytrading_candidates_{ts}.csv")


def _load_regime(path):
    """레짐 파일 → dict | None. 파일 없음·파손이면 경고 1줄 + None(fail-open — 스펙 §2)."""
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except FileNotFoundError:
        logger.warning("레짐 파일 없음(%s) — 기존 동작 유지(fail-open)", path)
        return None
    except Exception as exc:  # noqa: BLE001 (fail-open: 어떤 파손이든 기존 동작)
        logger.warning("레짐 파일 파손(%s: %s) — 기존 동작 유지(fail-open)", path, exc)
        return None
    return payload if isinstance(payload, dict) else None


def _prev_trading_day(today):
    """today 기준 직전 거래일 — 주말 + krx_holidays.json 휴장일 제외(스펙 §2 '직전 거래일').

    휴장일 파일이 없거나 파손이면 주말만 제외하고 돌려준다(경고 1줄).
    """
    holidays = None
    try:
        with open(_HOLIDAYS_PATH, "r", encoding="utf-8") as fh:
            holidays = set(json.load(fh))
    except Exception as exc:  # noqa: BLE001
        logger.warning("휴장일 파일 사용 불가(%s) — 주말만 제외", exc)
    d = today - timedelta(days=1)
    while d.weekday() >= 5 or (holidays is not None and d.isoformat() in holidays):
        d -= timedelta(days=1)
    return d


def _regime_gate(regime, today=None):
    """스펙 §2 게이트: state=="risk_off" + asof 가 오늘(또는 직전 거래일) → (True, reason).

    risk_on·unknown·asof 없음·오래된 파일 → (False, "") — fail-open(경고 1줄).
    reason 은 스펙 형식: "regime_off: KOSDAQ close<ma20".
    """
    if regime is None:
        return False, ""
    state = regime.get("state")
    if state != "risk_off":
        if state in (None, "unknown"):
            logger.warning("레짐 state=%s — 기존 동작 유지(fail-open)", state)
        return False, ""
    asof_str = regime.get("asof")
    try:
        asof = datetime.strptime(str(asof_str), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        logger.warning("레짐 asof 없음/형식 오류(%s) — 기존 동작 유지(fail-open)", asof_str)
        return False, ""
    today = today or datetime.now(KST).date()
    if asof != today and asof != _prev_trading_day(today):
        logger.warning("레짐 파일 오래됨(asof=%s, 오늘=%s) — 기존 동작 유지(fail-open)",
                       asof, today)
        return False, ""
    idx = regime.get("index") or "KOSDAQ"
    reason = "regime_off: %s %s" % (idx, regime.get("reason") or "")
    return True, reason.strip()


def _theme_points(pg, date_str, hot_up_ratio=0.65, hot_ret=1.5, points=8.0, mild=4.0):
    """theme_daily 기준 '뜨거운 테마' 소속 종목에 정렬 가점을 준다(2026-11-24).

    up_ratio >= hot_up_ratio 그리고 avg_ret_pct >= hot_ret 이면 points,
    up_ratio >= 0.55 이면 mild. 테마 테이블이 없으면 빈 dict(fail-open).
    """
    if pg is None:
        return {}, {}
    try:
        cur = pg.cursor()
        cur.execute("""
            SELECT theme, up_ratio, avg_ret_pct FROM theme_daily
            WHERE trade_date = COALESCE(%s::date, (SELECT max(trade_date) FROM theme_daily))
        """, (date_str,))
        rows = cur.fetchall()
        if not rows:
            return {}, {}
        hot, mild_themes = [], []
        for theme, up_ratio, avg_ret in rows:
            if up_ratio is None or avg_ret is None:
                continue
            if float(up_ratio) >= hot_up_ratio and float(avg_ret) >= hot_ret:
                hot.append(theme)
            elif float(up_ratio) >= 0.55:
                mild_themes.append(theme)
        cur.execute("SELECT stock_code, theme FROM stock_themes")
        members = cur.fetchall()
    except Exception as exc:
        logger.warning("테마 가점 계산 실패(무시): %s", exc)
        return {}, {}
    hot_set, mild_set = set(hot), set(mild_themes)
    out = {}
    for code, theme in members:
        if theme in hot_set:
            out[code] = max(out.get(code, 0.0), float(points))
        elif theme in mild_set:
            out[code] = max(out.get(code, 0.0), float(mild))
    info = {"hot": sorted(hot_set), "mild": sorted(mild_set), "codes": len(out)}
    return out, info


def main():
    ap = argparse.ArgumentParser(
        description="단타스크리너 — 칼만 노이즈 제거 + 챔피언 모델 기반 당일 매수 후보 선별")
    ap.add_argument("--top-n", type=int, default=20, help="상위 N개 후보 (기본 20)")
    ap.add_argument("--date", type=str, default=None, help="시그널 기준일 YYYY-MM-DD (기본: 최근 거래일)")
    ap.add_argument("--output", type=str, default=None, help="출력 CSV 경로")
    ap.add_argument("--json-out", type=str, default=None,
                    help="피드 소스 JSON 경로(spec_daytrading_feed.md §2 — "
                         "reports/daytrading_latest.json 로 두면 feed_export 가 발행)")
    ap.add_argument("--min-trading-value", type=float, default=5_000_000_000, help="최소 거래대금 (기본 50억)")
    ap.add_argument("--min-price", type=float, default=1000, help="최소 종가 (기본 1000원)")
    ap.add_argument("--min-atr-pct", type=float, default=0.0,
                    help="최소 ATR%%(14일/종가, 퍼센트). 4.75 = 하위 20%% 배제 (기본 0=필터 없음)")
    ap.add_argument("--max-atr-pct", type=float, default=0.0,
                    help="최대 ATR%%. 10.3 초과는 순기대가 다시 떨어진다(실측). 기본 0=상한 없음")
    ap.add_argument("--lookback", type=int, default=20, help="칼만 평활화 룩백 거래일 (기본 20)")
    ap.add_argument("--rank-band-file", dest="rank_band_file", default=None,
                    help="상승률 대역 파일(scripts/rank_universe.py 산출): "
                         "excluded_codes=추격 배제, codes=정렬 가점")
    ap.add_argument("--rank-bonus", type=float, default=5.0,
                    help="상승률 대역 가점(정렬 전용, score 값은 불변). 기본 5.0")
    ap.add_argument("--universe-file", dest="universe_file", default=None,
                    help="유니버스 압축 파일(scripts/rank_universe.py 산출 JSON). "
                         "파일이 없거나 ok:false/codes:[] 면 제한 없이 실행한다(fail-open)")
    ap.add_argument("--regime-file", dest="regime_file", default=REGIME_DEFAULT_PATH,
                    help="시장 레짐 파일(scripts/market_regime.py 산출). "
                         "state==risk_off + 최신(asof=오늘/직전 거래일)이면 후보를 "
                         "만들지 않는다(spec_regime_filter.md §2 — 기본 data/state/market_regime.json)")
    ap.add_argument("--regime-block", choices=("on", "off"), default="on",
                    help="레짐 게이트 on/off (기본 on — spec_regime_filter.md §2)")
    ap.add_argument("--dry-run", action="store_true",
                    help="콘솔 출력만 — CSV/JSON 산출물·자기신고를 기록하지 않는다(검증용)")
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
        if args.regime_block == "on":
            regime = _load_regime(args.regime_file)
            blocked, regime_reason = _regime_gate(regime)
            if blocked:
                logger.warning("레짐 게이트 차단: %s", regime_reason)
                out_path = args.output or _default_csv_path()
                if args.dry_run:
                    print(f"\n후보 없음 — {regime_reason} (dry-run: 산출물·자기신고 미기록)")
                    return
                write_csv([], out_path)  # 빈 후보(헤더만) — 조용한 0건과 구분되도록
                logger.info("CSV 저장(빈 후보): %s — %s", out_path, regime_reason)
                if args.json_out:
                    payload = build_json_payload([])
                    payload["reason"] = regime_reason  # 빈 items + 차단 사유(스펙 §2 ※)
                    write_json_out(payload, args.json_out)
                    logger.info("JSON 저장(빈 items + reason): %s", args.json_out)
                _record_claim(source_rows=0, claimed_rows=0, persisted_rows=0,
                              note=regime_reason)
                print(f"\n후보 없음 — {regime_reason} (빈 결과 저장: {out_path})")
                return

        provider = DbDailyProvider(pg_conn=pg)
        predictor = ChampionPredictor()
        if not predictor.available:
            logger.warning("챔피언 모델 미가용 — 칼만/거래량/변동성 점수만 사용")

        allow_codes = _load_universe_codes(args.universe_file)
        excl, bonus = _load_rank_band(args.rank_band_file)
        theme_pts, theme_info = _theme_points(pg, args.date)
        if theme_pts:
            logger.info("테마 가점 대상 %d종목 (뜨거운 테마 %s)", len(theme_pts), theme_info.get("hot"))
        ranked = run_screener(
            provider,
            top_n=args.top_n,
            lookback=args.lookback,
            min_history=20,
            min_trading_value=args.min_trading_value,
            min_price=args.min_price,
            min_atr_pct=args.min_atr_pct,
            max_atr_pct=args.max_atr_pct,
            date_str=args.date,
            predictor=predictor,
            allow_codes=allow_codes,
            exclude_codes=excl,
            bonus_codes=bonus,
            bonus_points=args.rank_bonus,
            theme_points=theme_pts,
        )

        rows = build_output_rows(ranked)
        if not rows:
            logger.warning("후보 없음 — 조건을 만족하는 종목 없음")
            # 후보가 없어도 JSON 산출물은 빈 items 로 갱신한다: 이전 산출물이 남아 있으면
            # feed_export 가 **어제 후보**를 신선한 것처럼 발행한다(신선도 게이트는
            # generated_at 만 본다 — "오늘은 0건"이라는 사실 자체가 발행돼야 경로가 닫힌다).
            if args.json_out and not args.dry_run:
                write_json_out(build_json_payload([]), args.json_out)
                logger.info(f"JSON 저장(빈 items): {args.json_out}")
            print("\n후보 없음 (필터 조건을 만족하는 종목이 없습니다).")
            return

        if args.output:
            out_path = args.output
        else:
            out_path = _default_csv_path()
        if not args.dry_run:
            write_csv(rows, out_path)
            logger.info(f"CSV 저장: {out_path}")

        json_items = 0
        if args.json_out and not args.dry_run:
            write_json_out(build_json_payload(rows), args.json_out)
            json_items = len(rows)
            logger.info(f"JSON 저장: {args.json_out} ({json_items}건)")
        if not args.dry_run:
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
        if args.dry_run:
            print("(dry-run: CSV/JSON 산출물·자기신고 미기록 — 검증용 실행)")
    finally:
        if pg is not None:
            try:
                pg.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()

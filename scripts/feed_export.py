#!/usr/bin/env python3
"""analyist_dd → trader-agent 피드 스냅샷 발행 (trader-agent/FEED_CONTRACT.md 준수).

무엇을: 종가/스윙 스크리너 산출물(reports/*_latest.json)을 계약 형식
        (data/feed/screener_latest.json)으로 합쳐 원자적으로 교체한다.
왜:     trader-agent 의 ScreenerFeedClient 가 이 URL/파일만 폴링한다. 계약을 벗어나면
        (신선도·가격·점수 누락) 엔진 게이트가 후보를 조용히 버린다.

계약 준수 포인트:
- generated_at 은 발행 시각(KST, tz 포함 ISO-8601) — 과거 값 재사용 시 전 후보 차단
- 전략별 키 분리: close / swing (섞지 않는다)
- close_price 는 주문 지정가로 그대로 쓰인다 → 누락 시 market_data 최근 종가로 채운다
- score 내림차순 정렬(엔진이 위에서부터 상위 N 만 집행)
- 6자리 숫자 코드만. 위반 항목은 버리고 로그로 남긴다

사용:
    python3 scripts/feed_export.py                 # reports/*_latest.json → data/feed/
    python3 scripts/feed_export.py --dry-run       # 쓰지 않고 검증/분포만 출력
"""
import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 유니버스 파일 형식은 스크리너와 이 발행측이 **같은 모듈**을 쓴다(형식 어긋남 방지).
from screener_universe import load_scores, percentile_of  # noqa: E402
try:
    from zoneinfo import ZoneInfo

    KST = ZoneInfo("Asia/Seoul")
except Exception:  # pragma: no cover
    KST = timezone(timedelta(hours=9))

FEED_DIR = os.path.join(PROJ, "data", "feed")
FEED_PATH = os.path.join(FEED_DIR, "screener_latest.json")
CODE_RE = re.compile(r"^\d{6}$")
DEFAULT_SOURCES = {
    "close": os.path.join(PROJ, "reports", "close_latest.json"),
    "swing": os.path.join(PROJ, "reports", "swing_latest.json"),
}
STATS_CANDIDATES = [
    os.path.join(PROJ, "data", "reports", "screener_stats.json"),
    os.path.join(PROJ, "reports", "screener_stats.json"),
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("feed_export")


def _num(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        v = value.strip().replace(",", "")
        if not v:
            return None
        try:
            return float(v)
        except ValueError:
            return None
    try:
        return float(value)   # Decimal 등 DB numeric 타입
    except (TypeError, ValueError):
        return None


def load_prev_closes(codes, upto_date=None):
    """market_data에서 종목별 최근 종가 (close_price 누락 보정용)."""
    if not codes:
        return {}
    try:
        import psycopg2
    except ImportError:  # pragma: no cover
        return {}
    host = os.environ.get("POSTGRES_HOST", "127.0.0.1")
    if host in ("postgres", "db"):
        host = "127.0.0.1"
    port = int(os.environ.get("POSTGRES_PORT", "5434") or 5434)
    if port == 5432:
        port = 5434
    try:
        conn = psycopg2.connect(
            host=host, port=port,
            user=os.environ.get("POSTGRES_USER", "stock_user"),
            password=os.environ.get("POSTGRES_PASSWORD", ""),
            dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
            connect_timeout=5,
        )
    except Exception as e:
        logger.warning("DB 연결 실패 — 종가 보정 생략: %s", e)
        return {}
    try:
        cur = conn.cursor()
        if upto_date:
            cur.execute(
                """
                SELECT DISTINCT ON (stock_code) stock_code, close_price
                FROM market_data
                WHERE stock_code = ANY(%s) AND trade_date <= %s
                ORDER BY stock_code, trade_date DESC
                """, (list(codes), upto_date))
        else:
            cur.execute(
                """
                SELECT DISTINCT ON (stock_code) stock_code, close_price
                FROM market_data
                WHERE stock_code = ANY(%s)
                ORDER BY stock_code, trade_date DESC
                """, (list(codes),))
        rows = cur.fetchall()
        cur.close()
        return {str(c): _num(p) for c, p in rows}
    except Exception as e:
        logger.warning("종가 조회 실패 — 보정 생략: %s", e)
        return {}
    finally:
        conn.close()


UNIVERSE_TEMPLATES = {
    # 스크리너 → 전체 스코어 유니버스 파일 경로 템플릿. 산출물이 있으면 후보별 rank_pct 를
    # 계산해 보낸다(피드 계약 v1.1): 확률 절대값 문턱은 모델 분포가 이동하면 의미를 잃지만
    # 백분위는 "모델 자체 순위에서 상위 몇 %"라 강건하다.
    "swing": [
        os.path.join(PROJ, "reports", "swing_universe_{date}.json"),
    ],
}


def load_universe(screener, dates):
    """스크리너 전체 스코어 유니버스 → {code: confidence}. 없으면 빈 dict(추정하지 않는다)."""
    for template in UNIVERSE_TEMPLATES.get(screener, []):
        for date in [d for d in dates if d]:
            path = template.format(date=str(date)[:10])
            if not os.path.exists(path):
                continue
            dist = load_scores(path)
            if dist:
                logger.info("[%s] 유니버스 %d종목 로드: %s", screener, len(dist), path)
                return dist
    return {}


def _signal_date(value, fallback=None):
    """signal_date 를 date 로 파싱 (실패하면 fallback, 없으면 None)."""
    for candidate in (value, fallback):
        text = str(candidate or "").strip()
        if not text:
            continue
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                return datetime.strptime(text[:10], "%Y-%m-%d").date()
            except ValueError:
                continue
    return None


def valid_until_for(screener, signal_date, publish_dt):
    """후보 만료 시각(ISO-8601, KST) — 소비자가 후보별 신선도를 판단한다.

    계약은 ``generated_at``(발행 시각)만 봤기 때문에, 산출물이 새것이어도
    **종목별 신호가 며칠 전 것인지**는 소비자가 알 수 없었다
    (실측 2026-09-29: close 후보 signal_date=09-23 인데 발행은 09-28 → 3거래일 지난
    패턴이 신선한 것처럼 나갔다).

    * close  : 발행일 15:30 KST — 그날 종가 진입창(14:50-15:25)까지만 유효
    * swing  : signal_date + 5일 15:30 KST (모델 라벨 지평 h5 와 같은 길이)
    """
    if screener == "close":
        base = publish_dt.replace(hour=15, minute=30, second=0, microsecond=0)
    else:
        anchor = _signal_date(signal_date) or publish_dt.date()
        base = datetime.combine(anchor, datetime.min.time(), tzinfo=KST).replace(
            hour=15, minute=30) + timedelta(days=5)
    return base.isoformat(timespec="seconds")


def build_items(screener, payload, prev_closes, publish_dt=None, universe=None,
                score_mode="native"):
    """스크리너 산출물 → 계약 형식 items (score 내림차순, 잘못된 항목 제외).

    ``score`` 의 **의미**를 ``score_kind`` 로 함께 선언한다(계약 §5):
    모델 확률(calibrated_prob)과 스크리너 점수(screener)는 스케일이 달라서
    소비자의 R1 문턱이 한 값으로 판단하면 경로가 조용히 닫힌다.

    ``universe`` ({code: confidence}) 가 주어지면 후보별 ``rank_pct``(모델 분포 내 백분위)와
    ``universe_size`` 를 붙인다. ``score_mode="rank_pct"`` 면 ``score`` 자체를 백분위로 바꾼다
    (소비자는 ``r1_min_avg_rank_pct`` 또는 기존 점수 문턱으로 판단 — 둘 다 0~100 스케일).
    """
    publish_dt = publish_dt or datetime.now(KST)
    universe = universe or {}
    raw = payload.get("candidates") or []
    items, dropped = [], []
    for row in raw:
        code = str(row.get("stock_code") or "").strip().lstrip("A")
        if not CODE_RE.match(code):
            dropped.append((code or "?", "6자리 코드 아님"))
            continue
        price = _num(row.get("close_price"))
        if price is None or price <= 0:
            price = prev_closes.get(code)
        if price is None or price <= 0:
            dropped.append((code, "가격 없음(게이트 6 차단 대상)"))
            continue
        conf = None
        if screener == "swing":
            conf = _num(row.get("confidence"))
            if conf is not None and 0 <= conf <= 1:
                score, score_kind = conf * 100.0, "calibrated_prob"
            elif conf is not None:
                # 확률 범위(0~1) 밖 값은 이미 점수 스케일로 온 것으로 본다.
                score, score_kind = conf, "screener"
            else:
                score, score_kind = _num(row.get("score")), "screener"
        else:
            score, score_kind = _num(row.get("score")), "screener"
        if score is None:
            dropped.append((code, "score 없음(R1/게이트 6 판단 불가)"))
            continue
        score = max(0.0, min(100.0, score))
        signal_date = row.get("signal_date") or payload.get("date") or ""
        item = {
            "stock_code": code,
            "stock_name": row.get("stock_name") or "",
            "close_price": str(int(round(price))),
            "score": round(score, 2),
            "score_kind": score_kind,
            "signal_date": signal_date,
            "valid_until": valid_until_for(screener, signal_date, publish_dt),
            "reason": (row.get("reason") or "").strip(),
        }
        if row.get("rank") is not None:
            item["rank"] = int(_num(row.get("rank")) or 0)
        for extra in ("sector", "volume_surge", "close_strength", "ret_3d_pct",
                      "ret_5d_pct", "day_change_pct", "expected_return", "confidence"):
            if row.get(extra) not in (None, ""):
                item[extra] = row[extra]
        # 모델 메타: 소비자가 사이징·청산에 쓸 수 있게 1급 필드로 올린다.
        if conf is not None and 0 <= conf <= 1:
            item["ml_prob"] = round(float(conf), 4)
        if payload.get("auc") not in (None, ""):
            item["ml_auc"] = payload.get("auc")
        # 백분위(모델 분포 내 상대 순위) — 유니버스 산출물이 있을 때만 계산한다.
        if universe:
            conf_for_rank = conf if (conf is not None and 0 <= conf <= 1) else score / 100.0
            rank_pct = percentile_of(conf_for_rank, list(universe.values()))
            if rank_pct is not None:
                item["rank_pct"] = rank_pct
                item["universe_size"] = len(universe)
                if score_mode == "rank_pct":
                    item["score"] = rank_pct
                    item["score_kind"] = "rank_pct"
        items.append(item)
    items.sort(key=lambda x: x["score"], reverse=True)
    if dropped:
        logger.warning("[%s] 제외 %d건: %s", screener, len(dropped),
                       ", ".join(f"{c}({r})" for c, r in dropped[:8]))
    return items


def load_stats():
    for path in STATS_CANDIDATES:
        if os.path.exists(path):
            try:
                data = json.load(open(path, encoding="utf-8"))
                stats = data.get("screener_stats", data)
                if isinstance(stats, dict) and stats:
                    logger.info("켈리 사전확률 통계 포함: %s", path)
                    return stats
            except Exception as e:
                logger.warning("통계 파일 읽기 실패(%s): %s", path, e)
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="trader-agent 피드 스냅샷 발행")
    ap.add_argument("--close", default=DEFAULT_SOURCES["close"])
    ap.add_argument("--swing", default=DEFAULT_SOURCES["swing"])
    ap.add_argument("--output", default=FEED_PATH)
    ap.add_argument("--dry-run", action="store_true", help="쓰지 않고 검증/분포만 출력")
    ap.add_argument("--no-stats", action="store_true", help="scoring_summary 생략")
    ap.add_argument("--max-source-age-days", type=float, default=3.0,
                    help="스크리너 산출물이 이보다 오래되면 해당 전략을 비워 발행(기본 3일)")
    ap.add_argument("--allow-degenerate-scores", action="store_true",
                    help="점수 퇴화(대부분 동일값) 항목도 그대로 발행")
    ap.add_argument("--max-items", type=int, default=20,
                    help="전략별 발행 상위 N (점수 내림차순, 기본 20)")
    ap.add_argument("--min-items", type=int, default=3,
                    help="퇴화 항목 제거 후 이보다 적으면 그 전략을 비운다(기본 3)")
    ap.add_argument("--degenerate-ratio", type=float, default=0.8,
                    help="한 점수가 이 비율 이상을 차지하면 퇴화로 보고 그 항목만 제외(기본 0.8)")
    ap.add_argument("--min-swing-confidence", type=float, default=0.5,
                    help="swing 후보의 최소 confidence (기본 0.5). 모델이 하락 우위로 평가한"
                         " 종목(확률<0.5)을 매수하지 않도록 기본값에서 잘라낸다."
                         " 0 으로 두면 필터 없음.")
    ap.add_argument("--swing-score-mode", choices=("native", "rank_pct"), default="native",
                    help="swing 점수 스케일. native=확률×100(기본). rank_pct=모델 분포 내 "
                         "백분위(0~100)로 바꿔 발행 — 문턱이 분포 이동에 강건해진다. "
                         "reports/swing_universe_<date>.json 이 있을 때만 동작한다.")
    args = ap.parse_args(argv)

    sources = {"close": args.close, "swing": args.swing}
    payloads = {}
    for key, path in sources.items():
        if not os.path.exists(path):
            logger.warning("[%s] 산출물 없음: %s (이 전략은 빈 리스트로 발행)", key, path)
            continue
        try:
            payload = json.load(open(path, encoding="utf-8"))
        except Exception as e:
            logger.error("[%s] 읽기 실패: %s", key, e)
            continue
        # 신선도 가드: 계약은 generated_at(발행 시각)만 보므로, 산출물 자체가 오래되면
        # 낡은 종목이 신선한 것처럼 나간다 → 오래된 전략은 비워서 보낸다.
        stamp = payload.get("date") or payload.get("generated_at")
        age_days = None
        if stamp:
            try:
                dt = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=KST)
                age_days = (datetime.now(KST) - dt).total_seconds() / 86400.0
            except ValueError:
                age_days = None
        if age_days is not None and age_days > args.max_source_age_days:
            logger.warning("[%s] 산출물이 %.1f일 지났다(> %.1f일) — 이 전략은 빈 리스트로 발행: %s",
                           key, age_days, args.max_source_age_days, path)
            continue
        if age_days is not None:
            logger.info("[%s] 산출물 기준일 %s (%.2f일 전)", key, stamp, age_days)
        payloads[key] = payload

    codes = set()
    for p in payloads.values():
        for row in (p.get("candidates") or []):
            c = str(row.get("stock_code") or "").strip().lstrip("A")
            if CODE_RE.match(c):
                codes.add(c)
    upto = max((p.get("date") for p in payloads.values() if p.get("date")), default=None)
    prev_closes = load_prev_closes(codes, upto)

    candidates = {}
    publish_dt = datetime.now(KST)
    for key in ("close", "swing"):
        payload = payloads.get(key, {})
        universe = load_universe(key, [payload.get("date"), publish_dt.date().isoformat()])
        mode = args.swing_score_mode if key == "swing" else "native"
        items = (build_items(key, payload, prev_closes, publish_dt, universe, mode)
                 if key in payloads else [])
        if universe:
            ranked = sum(1 for item in items if item.get("rank_pct") is not None)
            logger.info("[%s] rank_pct 부여 %d/%d건 (universe_size=%d, score_mode=%s)",
                        key, ranked, len(items), len(universe), mode)
        # 후보별 신호 나이 가시화: 산출물 파일이 새것이어도 안에 든 신호는 며칠 전일 수
        # 있다. 계약 v1.1 의 valid_until·signal_date 로 소비자가 차단하므로, 발행 시점에
        # 로그로 남겨 "왜 매매가 없었는지"를 발행 기록만으로 추적할 수 있게 한다.
        if items:
            source_date = _signal_date(payloads.get(key, {}).get("date"))
            ages = []
            for it in items:
                sd = _signal_date(it.get("signal_date"), source_date)
                if sd is not None:
                    ages.append((publish_dt.date() - sd).days)
            if ages:
                logger.info("[%s] signal_date %d~%d일 전 (발행일 %s, valid_until예: %s)",
                            key, min(ages), max(ages), publish_dt.date(),
                            items[0].get("valid_until"))
                if max(ages) > 3:
                    logger.warning(
                        "[%s] 종목별 신호가 최대 %d일 전 — 소비자(루프)의 후보별 신선도 "
                        "게이트가 이 후보를 차단한다. 스크리너가 signal_date 를 갱신하지 "
                        "않는지 확인할 것.", key, max(ages))
        # 순위 기반 선별 (모델 확률이 0.5 근처에 몰려 절대 임계값이 무의미한 구간 대응):
        # 점수 내림차순 정렬 후 상위 N 만 발행한다. 스크리너가 이미 정렬해 주지만 여기서
        # 다시 보장한다(계약: trader-agent 는 순서를 신뢰하고 상위 N 을 집행).
        if items:
            items.sort(key=lambda i: i["score"], reverse=True)
            items = items[: args.max_items]
        # 점수 퇴화 가드: 한 점수가 대부분을 차지하면 '그 항목만' 제외한다.
        # (예전에는 전략 전체를 비웠는데, 그러면 살아있는 소수 후보까지 사라진다.
        #  실측 2026-09-24: swing 20건 중 17건이 동일값 → 전략이 통째로 비어 거래 기회 0.)
        if items and not args.allow_degenerate_scores:
            scores = [i["score"] for i in items]
            modal = max(set(scores), key=scores.count)
            same = sum(1 for s in scores if s == modal)
            if len(items) >= 5 and same / len(items) >= args.degenerate_ratio:
                kept = [i for i in items if i["score"] != modal]
                logger.warning(
                    "[%s] 점수 퇴화 — %d/%d건이 동일값 %.2f → 그 항목만 제외하고 %d건 발행",
                    key, same, len(items), modal, len(kept))
                items = kept
        # swing 품질 게이트: 모델이 하락 우위로 평가한 후보(confidence < 문턱)는
        # 매수 대상이 아니다. (실측 2026-09-24: swing 20건 전부 0.40~0.43 —
        #  순위만으로는 '가장 덜 약세'인 종목이 뽑혀 매수 신호로 오해된다.)
        if key == "swing" and items and args.min_swing_confidence > 0:
            thr = args.min_swing_confidence
            kept = [i for i in items
                    if i.get("confidence") is None or float(i["confidence"]) >= thr]
            dropped = len(items) - len(kept)
            if dropped:
                logger.info("[swing] confidence < %.2f 후보 %d건 제외 (매수 대상 아님)",
                            thr, dropped)
            items = kept
        if items and not args.allow_degenerate_scores and len(items) < args.min_items:
            logger.warning(
                "[%s] 유효 후보 %d건(< 최소 %d건) → 이 전략은 빈 리스트로 발행",
                key, len(items), args.min_items)
            items = []
        candidates[key] = {"items": items}

    feed = {
        "generated_at": publish_dt.isoformat(timespec="seconds"),
        "source": "analyist_dd",
        "candidates": candidates,
    }
    stats = None if args.no_stats else load_stats()
    if stats:
        feed["scoring_summary"] = {"screener_stats": stats}

    # 검증 + 분포 요약 (계약 §5: 점수가 좁게 뭉치면 문턱·상위 N 선별이 무의미)
    ok = True
    for key, block in candidates.items():
        items = block["items"]
        scores = [i["score"] for i in items]
        if scores:
            scores_sorted = sorted(scores)
            median = scores_sorted[len(scores_sorted) // 2]
            spread = round(max(scores) - min(scores), 2)
            logger.info("[%s] %d건 | score min=%.1f median=%.1f max=%.1f spread=%.1f",
                        key, len(items), min(scores), median, max(scores), spread)
            if spread < 5:
                logger.warning("[%s] 점수 분포가 좁다(spread %.1f) — 문턱 선별이 무의미할 수 있음",
                               key, spread)
        else:
            logger.info("[%s] 후보 0건", key)

    text = json.dumps(feed, ensure_ascii=False, indent=1)
    if args.dry_run:
        print(text)
        logger.info("dry-run — 파일 쓰기 생략 (%s)", args.output)
        return 0 if ok else 1

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    tmp = args.output + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, args.output)   # 원자적 교체 (폴링 중 반쪽 파일 방지)
    logger.info("발행 완료: %s (%d bytes, generated_at=%s)",
                args.output, len(text), feed["generated_at"])
    return 0


if __name__ == "__main__":
    sys.exit(main())

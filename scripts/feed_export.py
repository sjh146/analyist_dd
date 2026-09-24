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


def build_items(screener, payload, prev_closes):
    """스크리너 산출물 → 계약 형식 items (score 내림차순, 잘못된 항목 제외)."""
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
        if screener == "swing":
            conf = _num(row.get("confidence"))
            score = conf * 100.0 if conf is not None and 0 <= conf <= 1 else conf
        else:
            score = _num(row.get("score"))
        if score is None:
            dropped.append((code, "score 없음(R1/게이트 6 판단 불가)"))
            continue
        score = max(0.0, min(100.0, score))
        item = {
            "stock_code": code,
            "stock_name": row.get("stock_name") or "",
            "close_price": str(int(round(price))),
            "score": round(score, 2),
            "signal_date": row.get("signal_date") or payload.get("date") or "",
            "reason": (row.get("reason") or "").strip(),
        }
        if row.get("rank") is not None:
            item["rank"] = int(_num(row.get("rank")) or 0)
        for extra in ("sector", "volume_surge", "close_strength", "ret_3d_pct",
                      "ret_5d_pct", "day_change_pct", "expected_return", "confidence"):
            if row.get(extra) not in (None, ""):
                item[extra] = row[extra]
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
    for key in ("close", "swing"):
        items = build_items(key, payloads.get(key, {}), prev_closes) if key in payloads else []
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
        "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
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

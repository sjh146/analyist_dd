#!/usr/bin/env python3
"""종토방 **심층** 백필 러너 — 얕은(5페이지) 커버리지를 과거로 넓힌다.

실행(stock_news_analyzer 컨테이너, cwd=/app):
    python -u scripts/sns_backfill_depth.py [--min-date 2026-07-01]
                                            [--max-pages-per-stock 60]
                                            [--enrich-recent-days 15]
                                            [--max-minutes 60] [--limit 250]
                                            [--codes 005930,000660] [--dry-run]

왜 필요한가
-----------
``sns_collect_runner.py`` 기본값은 종목당 5페이지(=최대 500건)다. 실측하면
게시글 밀도가 종목마다 33~250건/일이라 **500건 = 2~15일**밖에 못 덮는다
(예: 000660 은 2일). 학습 패널은 수백 종목 × 120~180일이므로 과거가 필요하다.

핵심 설계 (모두 실측 근거)
--------------------------
1. **커서 재개**: 응답의 ``lastOffset``(=전역 게시글 id 음수값)을 진행파일에
   저장해 두고 다음 실행은 이어서 과거로 파고든다. 재실행이 최신 페이지를
   다시 훑지 않으므로 같은 요청 예산으로 깊이가 계속 늘어난다.
2. **최근 N일만 보강(enrich)**: reactions/comment-counts 호출은 게시글 100건당
   2회로, 전체 백필을 보강하면 요청이 3배가 된다(깊이가 1/3). 그래서 기본은
   최근 15일 구간만 보강한다. 과거 구간은 목록 응답만 쓰므로 ``comment_count``/
   ``like_count`` 가 0 → ``author_quality_score`` 가 0 에 가까워진다(주의).
   ``--enrich-recent-days 9999`` 로 전 구간 보강(느림)도 가능.
3. **싼 종목 우선**(게시글/일 밀도 오름차순) → 같은 페이지 예산으로 가장 많은
   '일(day)'을 얻는다. 마감에 걸려 중단돼도 커버리지 이득이 큰 종목부터 채워진다.
   (``--order thin`` 은 커버 일수 짧은 순인데, 그런 종목은 게시글이 폭주하는
   비싼 종목이라 기본값으로 쓰지 않는다 — 실측: 000660 은 250건/일.)
4. **종목 단위 즉시 저장**(``ON CONFLICT DO NOTHING``) + 진행파일 갱신 →
   중단/재실행에 안전하고 멱등하다.
5. 요청 간 최소 0.7초는 수집기 ``NaverBoardCollector._rate_limit``(REQUEST_DELAY
   =0.7)이 전역으로 강제한다(초당 2회 초과 금지 — 네이버 예의).

진행파일: ``/app/data/sns_backfill_depth.json`` (호스트 data/disclosures/).
"""
import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

import psycopg2

import aiohttp

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sns_backfill")

from app.collectors.sns_naver_board import NaverBoardCollector  # noqa: E402

KST = timezone(timedelta(hours=9))
PROGRESS_PATH = os.environ.get("SNS_BACKFILL_PROGRESS", "/app/data/sns_backfill_depth.json")


def _connect():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
    )


def load_progress() -> dict:
    try:
        with open(PROGRESS_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {"stocks": {}, "runs": []}


def save_progress(data: dict) -> None:
    tmp = PROGRESS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, PROGRESS_PATH)


def coverage(conn) -> dict:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT stock_code, count(*) AS posts, count(DISTINCT posted_at::date) AS days,
               min(posted_at::date)::text, max(posted_at::date)::text
        FROM sns_posts
        WHERE stock_code IS NOT NULL AND posted_at IS NOT NULL
        GROUP BY stock_code
        """
    )
    out = {r[0]: {"posts": r[1], "days": r[2], "first": r[3], "last": r[4]}
           for r in cur.fetchall()}
    cur.close()
    return out


def universe(conn, limit: int, order: str = "cheap") -> list:
    """확장 비용이 싼 순으로 정렬.

    ``cheap``: 게시글/일 비율(=게시글 밀도) 오름차순 → 같은 페이지 예산으로
    가장 많은 '일(day)'을 얻는 종목부터. 실측상 게시글 밀도가 2.4~250건/일로
    100배 차이나므로 이 순서가 커버리지 이득/요청 비율이 가장 좋다.
    ``thin``: 커버 일수 오름차순(비싼 종목이 먼저 오므로 기본값 아님).
    """
    cur = conn.cursor()
    if order == "thin":
        cur.execute(
            """
            SELECT stock_code FROM sns_posts
            WHERE stock_code IS NOT NULL AND posted_at IS NOT NULL
            GROUP BY stock_code
            ORDER BY count(DISTINCT posted_at::date) ASC, count(*) ASC
            LIMIT %s
            """,
            (limit,),
        )
    else:
        cur.execute(
            """
            SELECT stock_code FROM sns_posts
            WHERE stock_code IS NOT NULL AND posted_at IS NOT NULL
            GROUP BY stock_code
            ORDER BY (count(*)::float / GREATEST(count(DISTINCT posted_at::date), 1)) ASC,
                     stock_code ASC
            LIMIT %s
            """,
            (limit,),
        )
    codes = [r[0] for r in cur.fetchall()]
    cur.close()
    return codes


def known_post_ids(conn, stock_code) -> set:
    cur = conn.cursor()
    cur.execute("SELECT post_id FROM sns_posts WHERE stock_code = %s", (stock_code,))
    ids = {r[0] for r in cur.fetchall()}
    cur.close()
    return ids


def save_posts(posts) -> int:
    if not posts:
        return 0
    conn = _connect()
    cur = conn.cursor()
    saved = 0
    for p in posts:
        cur.execute(
            """
            INSERT INTO sns_posts
              (source, post_id, stock_code, author_id, author_name, author_followers,
               posted_at, text, comment_count, like_count, retweet_count, raw_json)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT DO NOTHING
            """,
            (
                p.source, p.post_id, p.stock_code, p.author_id, p.author_name,
                p.author_followers, p.posted_at, p.text, p.comment_count,
                p.like_count, p.retweet_count,
                json.dumps(p.raw_json, ensure_ascii=False, default=str),
            ),
        )
        saved += cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    return saved


async def collect_deep(
    collector, session, stock_code, *,
    min_date, enrich_cutoff, max_pages, size=100,
    start_offset=None, known_ids=None,
):
    """커서를 따라 ``min_date`` 까지(또는 페이지 상한까지) 과거로 내려가며 수집.

    Returns
    -------
    (posts, resume_offset, pages_done, reached_min_date)
    """
    known_ids = known_ids if known_ids is not None else set()
    collected, enrich_targets = [], []
    offset = start_offset
    pages_done = 0
    reached = False

    while pages_done < max_pages:
        payload = await collector._get_json(
            session, collector.api_posts_url(stock_code, offset, size)
        )
        pages_done += 1
        if not isinstance(payload, dict):
            break
        page_posts = collector.parse_api_posts(payload, stock_code)
        if not page_posts:
            break

        new_posts = [p for p in page_posts if p.post_id not in known_ids]
        for p in new_posts:
            known_ids.add(p.post_id)
        collected.extend(new_posts)
        for p in new_posts:
            if p.posted_at is not None and p.posted_at.date() >= enrich_cutoff:
                enrich_targets.append(p)

        oldest = collector._oldest_date(page_posts)
        if oldest is not None and oldest <= min_date:
            reached = True
            offset = None  # 도달 → 다음 실행은 최신부터 다시 시작하지 않고 종료 처리
            break
        next_offset = payload.get("lastOffset")
        if not next_offset or str(next_offset) == str(offset):
            offset = None
            break
        offset = str(next_offset)

    if enrich_targets:
        try:
            await collector._enrich(session, enrich_targets)
        except Exception as e:  # fail-open
            log.debug("enrich 실패 %s: %s", stock_code, e)

    return collected, offset, pages_done, reached


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-date", type=str, default="2026-07-01",
                    help="이 날짜까지 과거로 내려간다")
    ap.add_argument("--max-pages-per-stock", type=int, default=60)
    ap.add_argument("--enrich-recent-days", type=int, default=15,
                    help="최근 N일 게시글만 reactions/comment 보강 (9999=전 구간)")
    ap.add_argument("--page-size", type=int, default=100)
    ap.add_argument("--limit", type=int, default=250, help="대상 종목 수")
    ap.add_argument("--order", choices=["cheap", "thin"], default="cheap",
                    help="cheap=게시글 밀도 낮은(요청 효율 좋은) 순, thin=커버 일수 짧은 순")
    ap.add_argument("--max-minutes", type=float, default=60.0,
                    help="이 시간을 넘기면 남은 종목은 다음 실행으로")
    ap.add_argument("--codes", type=str, default=None, help="쉼표 구분 종목코드")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    min_date = date.fromisoformat(args.min_date)
    enrich_cutoff = datetime.now(KST).date() - timedelta(days=args.enrich_recent_days)

    conn = _connect()
    before = coverage(conn)
    codes = ([c.strip() for c in args.codes.split(",") if c.strip()]
             if args.codes else universe(conn, args.limit, args.order))
    conn.close()

    progress = load_progress()
    log.info(
        "대상 %d종목 / min_date=%s / 종목당 최대 %d페이지 / 최근 %d일만 보강 / "
        "요청 간 %.1fs / 마감 %.0f분",
        len(codes), min_date, args.max_pages_per_stock, args.enrich_recent_days,
        NaverBoardCollector.REQUEST_DELAY, args.max_minutes,
    )
    log.info("시작 커버리지: 종목 %d개, 게시글 %d건",
             len(before), sum(v["posts"] for v in before.values()))

    collector = NaverBoardCollector()
    started = time.time()
    requests_est = 0
    deadline = started + args.max_minutes * 60
    done = added = skipped = 0

    async with aiohttp.ClientSession() as session:
        for i, code in enumerate(codes, 1):
            if time.time() > deadline:
                log.info("마감 도달 — %d/%d 종목 처리, 나머지는 재실행 시 이어짐",
                         i - 1, len(codes))
                break

            st = progress["stocks"].get(code) or {}
            start_offset = st.get("last_offset")
            if st.get("reached_min_date"):
                skipped += 1
                continue

            known = known_post_ids(_connect(), code)

            posts, resume, pages, reached = await collect_deep(
                collector, session, code,
                min_date=min_date, enrich_cutoff=enrich_cutoff,
                max_pages=args.max_pages_per_stock, size=args.page_size,
                start_offset=start_offset, known_ids=known,
            )
            requests_est += pages
            if args.dry_run:
                ds = sorted({p.posted_at.date() for p in posts if p.posted_at})
                log.info("[dry] %s: 신규 %d건 / %d일 (%s ~ %s), %d페이지%s",
                         code, len(posts), len(ds),
                         ds[0] if ds else "-", ds[-1] if ds else "-",
                         pages, " [min_date 도달]" if reached else "")
                continue

            saved = save_posts(posts)
            added += saved
            done += 1

            c3 = _connect()
            cur = c3.cursor()
            cur.execute(
                """SELECT count(*), count(DISTINCT posted_at::date),
                          min(posted_at::date)::text, max(posted_at::date)::text
                   FROM sns_posts WHERE stock_code = %s""",
                (code,),
            )
            row = cur.fetchone()
            cur.close()
            c3.close()

            prev = st.get("pages_run", 0)
            progress["stocks"][code] = {
                "last_offset": resume,
                "reached_min_date": reached,
                "pages_run": prev + pages,
                "posts": row[0], "days": row[1], "first": row[2], "last": row[3],
                "at": datetime.now().isoformat(timespec="seconds"),
            }
            save_progress(progress)

            if i % 3 == 0 or i == len(codes):
                el = (time.time() - started) / 60
                log.info("[%d/%d] %s: 신규 %d건 → 총 %d건/%d일 (%s~%s) | 누적 신규 %d건 | "
                         "%.1f분 | 요청간 %.2fs",
                         i, len(codes), code, saved, row[0], row[1], row[2], row[3],
                         added, el, (time.time() - started) / max(requests_est, 1))

    elapsed = (time.time() - started) / 60
    log.info("완료: %d종목 처리(스킵 %d), 신규 저장 %d건, 페이지 요청 %d, %.1f분",
             done, skipped, added, requests_est, elapsed)
    if not args.dry_run:
        conn = _connect()
        after = coverage(conn)
        conn.close()
        b_tot = sum(v["posts"] for v in before.values())
        a_tot = sum(v["posts"] for v in after.values())
        log.info("최종 커버리지: 종목 %d개, 게시글 %d건 (+%d)", len(after), a_tot, a_tot - b_tot)
        days = sorted(v["days"] for v in after.values())
        if days:
            log.info("종목별 커버 일수: min %d / p25 %d / median %d / p75 %d / max %d",
                     days[0], days[len(days) // 4], days[len(days) // 2],
                     days[3 * len(days) // 4], days[-1])
        progress.setdefault("runs", []).append({
            "at": datetime.now().isoformat(timespec="seconds"),
            "min_date": args.min_date,
            "enrich_recent_days": args.enrich_recent_days,
            "stocks_done": done, "added": added,
            "pages": requests_est, "minutes": round(elapsed, 1),
        })
        save_progress(progress)


if __name__ == "__main__":
    asyncio.run(main())

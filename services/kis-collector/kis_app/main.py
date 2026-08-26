"""KIS 데이터 수집기 CLI.

사용법 (services/kis-collector 디렉토리 기준):
  python3 -m kis_app.main --job daily  --date 20260825
  python3 -m kis_app.main --job minute --date 20260825
  python3 -m kis_app.main --job all    --date 20260825 --limit 10
  KIS_DRY_RUN=1 python3 -m kis_app.main --job daily --date 20260825 --limit 3

크론/스케줄 등록은 Hermes 담당 (지시서 제약 6 — 본 프로그램은 1회성 실행).
"""
import argparse
import logging
import sys
from datetime import datetime

from kis_app.client.kis_client import KisClient
from kis_app.collectors.daily_collector import DailyCollector
from kis_app.collectors.minute_collector import MinuteCollector
from kis_app.config import Config
from kis_app.storage.postgres_storage import NullStorage, PostgresStorage

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("kis_collector.main")


def build_client(config: Config) -> KisClient:
    return KisClient(
        appkey=config.KIS_APP_KEY,
        appsecret=config.KIS_APP_SECRET,
        base_url=config.KIS_BASE_URL,
        daily_tr_id=config.KIS_DAILY_TR_ID,
        minute_tr_id=config.KIS_MINUTE_TR_ID,
        delay=config.KIS_REQUEST_DELAY,
        jitter=config.KIS_REQUEST_JITTER,
        retry_max=config.KIS_RETRY_MAX,
        retry_base_delay=config.KIS_RETRY_BASE_DELAY,
        token_rate_limit_sleep=config.KIS_TOKEN_RATE_LIMIT_SLEEP,
        token_max_retries=config.KIS_TOKEN_MAX_RETRIES,
        token_path=config.KIS_TOKEN_PATH,
        http_timeout=config.KIS_HTTP_TIMEOUT,
        dry_run=config.KIS_DRY_RUN,
    )


def main(argv=None):
    ap = argparse.ArgumentParser(description="KIS 데이터 수집기 (일봉/분봉)")
    ap.add_argument("--job", choices=["daily", "minute", "all"], default="daily",
                    help="daily: 일봉, minute: 분봉, all: 둘 다")
    ap.add_argument("--date", default=None,
                    help="수집 대상일 YYYYMMDD (기본: 오늘)")
    ap.add_argument("--limit", type=int, default=None,
                    help="점검용 — 첫 N 종목만 처리")
    args = ap.parse_args(argv)

    config = Config()
    if not config.KIS_APP_KEY or not config.KIS_APP_SECRET:
        sys.exit("오류: KIS_APP_KEY / KIS_APP_SECRET 환경변수가 필요합니다")
    if config.KIS_DRY_RUN:
        logger.info("KIS_DRY_RUN=1 — 실제 HTTP/DB 호출 없이 흐름만 점검합니다")

    target = args.date or datetime.now().strftime("%Y%m%d")
    client = build_client(config)
    storage = NullStorage() if config.KIS_DRY_RUN else PostgresStorage(config)

    results = {}
    if args.job in ("daily", "all"):
        results["daily"] = DailyCollector(client, storage).collect(
            target, limit=args.limit)
    if args.job in ("minute", "all"):
        results["minute"] = MinuteCollector(client, storage).collect(
            target, limit=args.limit)

    print("\n" + "=" * 60)
    print(f"KIS 수집 완료 (job={args.job}, date={target}, "
          f"dry_run={config.KIS_DRY_RUN})")
    for name, s in results.items():
        print(f"  {name:8s} total={s['total']} ok={s.get('ok')} "
              f"no_data={s.get('no_data')} fail={s.get('fail')} "
              f"bars={s.get('bars', '-')}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())

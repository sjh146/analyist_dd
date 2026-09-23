"""KIS 데이터 수집기 CLI.

사용법 (services/kis-collector 디렉토리 기준):
  python3 -m kis_app.main --job daily  --date 20260825
  python3 -m kis_app.main --job minute --date 20260825
  python3 -m kis_app.main --job all    --date 20260825 --limit 10
  KIS_DRY_RUN=1 python3 -m kis_app.main --job daily --date 20260825 --limit 3

크론/스케줄 등록은 Hermes 담당 (지시서 제약 6 — 본 프로그램은 1회성 실행).
"""
import argparse
import json
import logging
import sys
import time
from datetime import datetime, timedelta

from kis_app.client.kis_client import KisApiError, KisClient
from kis_app.collectors.daily_collector import DailyCollector
from kis_app.collectors.minute_collector import MinuteCollector
from kis_app.config import Config
from kis_app.storage.postgres_storage import NullStorage, PostgresStorage

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("kis_collector.main")

# 자격증명 점검(--probe-token)에 쓰는 대표 종목 (KOSPI 삼성전자)
PROBE_SYMBOL = "005930"


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
    ap.add_argument("--universe-file", default=None,
                    help="수집 대상 종목 파일 (JSON 배열 또는 {코드: 시장} 객체). "
                         "전 종목 분봉은 하루 3~4만 콜이라 비현실적 — 우선순위 "
                         "유니버스로 좁힐 때 쓴다 (예: data/minute_universe.json)")
    ap.add_argument("--probe-token", action="store_true",
                    help="KIS 자격증명 점검: 토큰 발급(캐시 허용) + 시세 1콜 확인 후 종료")
    args = ap.parse_args(argv)

    config = Config()
    if not config.KIS_APP_KEY or not config.KIS_APP_SECRET:
        sys.exit("오류: KIS_APP_KEY / KIS_APP_SECRET 환경변수가 필요합니다")
    if config.KIS_DRY_RUN:
        logger.info("KIS_DRY_RUN=1 — 실제 HTTP/DB 호출 없이 흐름만 점검합니다")

    if args.probe_token:
        # 캐시된 토큰만 확인하면 앱키/시크릿이 틀려도 '정상'으로 보인다(실측).
        # 시세 1콜까지 해야 자격증명·도메인이 실제로 검증된다 — 앱키가 어긋나면
        # KIS가 401 → 토큰 재발급 시도 → EGW00103으로 드러난다.
        client = build_client(config)
        probe_day = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
        try:
            client.get_daily_chart(PROBE_SYMBOL, "J", probe_day, probe_day, count=1)
        except KisApiError as e:
            print(f"실패: KIS 자격증명/도메인 확인 불가 [{e.msg_cd}] {e.msg1} "
                  f"(http={e.http_status})")
            print(f"  도메인={config.KIS_BASE_URL} 앱키길이={len(config.KIS_APP_KEY)} "
                  f"시크릿길이={len(config.KIS_APP_SECRET)}")
            return 3
        expire = time.strftime("%Y-%m-%d %H:%M:%S",
                               time.localtime(client.tokens.token_expire_at))
        print(f"정상: 토큰 + 시세 1콜 확인 OK (만료 {expire}, 도메인 {config.KIS_BASE_URL})")
        return 0

    target = args.date or datetime.now().strftime("%Y%m%d")
    client = build_client(config)
    storage = NullStorage() if config.KIS_DRY_RUN else PostgresStorage(config)

    universe = None
    if args.universe_file:
        with open(args.universe_file, encoding="utf-8") as f:
            spec = json.load(f)
        if isinstance(spec, dict):                      # {코드: 시장}
            universe = [(str(c).strip(), str(m).strip() or "KOSPI")
                        for c, m in spec.items() if str(c).strip()]
        else:                                           # [코드, ...]
            known = {c: m for c, m in storage.get_universe()}
            universe = [(str(c).strip(), known.get(str(c).strip(), "KOSPI"))
                        for c in spec if str(c).strip()]
        logger.info("유니버스 파일 적용: %d종목 (%s)", len(universe), args.universe_file)

    results = {}
    if args.job in ("daily", "all"):
        results["daily"] = DailyCollector(client, storage).collect(
            target, limit=args.limit, universe=universe)
    if args.job in ("minute", "all"):
        results["minute"] = MinuteCollector(client, storage).collect(
            target, limit=args.limit, universe=universe)

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

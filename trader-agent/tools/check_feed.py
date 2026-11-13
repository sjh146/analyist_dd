"""피드 스냅샷 점검 도구 (브리지 없이 실행 가능).

analyist_dd 가 발행한 screener_latest.json 을 받아 **소비자 코드로 파싱**해
게이트에 걸리기 전 단계의 문제(신선도·코드·가격·점수)를 미리 드러낸다.

사용 (Windows, 64비트 코어 파이썬):
    C:\\Users\\jhshi\\Python312-64\\python.exe tools\\check_feed.py
    C:\\Users\\jhshi\\Python312-64\\python.exe tools\\check_feed.py --feed http://127.0.0.1:8090/screener_latest.json
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader_core.config import Config                      # noqa: E402
from trader_core.net import urlopen as local_urlopen        # noqa: E402
from trader_core.feed import FeedError, FeedSnapshot       # noqa: E402


def fetch(url_or_path: str) -> str:
    if "://" in url_or_path:
        with local_urlopen(url_or_path, timeout=10) as resp:
            return resp.read().decode("utf-8")
    return Path(url_or_path).read_text(encoding="utf-8")


def main() -> int:
    try:   # Windows 콘솔(cp949)에서도 깨지지 않게
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="analyist_dd 피드 점검 (읽기 전용)")
    ap.add_argument("--feed", default=None, help="URL 또는 파일 경로 (기본: Config.feed_url)")
    ap.add_argument("--max-age-hours", type=float, default=None)
    args = ap.parse_args()

    cfg = Config()
    target = args.feed or cfg.feed_url
    max_age = (args.max_age_hours * 3600.0) if args.max_age_hours else cfg.feed_max_age
    print(f"대상: {target}")
    try:
        raw = fetch(target)
    except Exception as e:
        print(f"실패: 가져오기 오류 - {e}")
        return 2
    try:
        snap = FeedSnapshot.from_text(raw)
    except FeedError as e:
        print(f"실패: 계약 위반 - {e}")
        return 3

    now = datetime.now(timezone(timedelta(hours=9)))
    fresh = snap.is_fresh(now, max_age)
    print(f"generated_at: {snap.generated_at}  ({'신선' if fresh else 'STALE - 게이트 1이 전 후보 차단'})")
    print(f"source: {snap.source or '(없음)'} | 전략 키: {', '.join(snap.candidates) or '(없음)'}")
    bad = 0
    for name, items in sorted(snap.candidates.items()):
        if not items:
            print(f"  [{name}] 0건")
            continue
        scores = [i.score for i in items if i.score is not None]
        prices_missing = sum(1 for i in items if not i.close_price)
        spread = (max(scores) - min(scores)) if len(scores) > 1 else 0.0
        print(f"  [{name}] {len(items)}건 | 가격누락 {prices_missing} | "
              f"score {min(scores):.1f}~{max(scores):.1f} (spread {spread:.1f})"
              if scores else f"  [{name}] {len(items)}건 | score 없음")
        if prices_missing:
            bad += prices_missing
        if spread and spread < 5:
            print(f"      주의: 점수 분포가 좁다 — 문턱({name}) 선별이 무의미해질 수 있음")
        top = items[0]
        print(f"      상위: {top.stock_code} {top.stock_name} 가격={top.close_price} score={top.score}")
    for name, stats in (snap.scoring_stats or {}).items():
        print(f"  켈리 사전확률[{name}]: {stats}")
    if not snap.scoring_stats:
        print("  켈리 사전확률: 없음 (엔진 자체 저널/기본값 사용)")
    print("결과:", "이상 없음" if (fresh and bad == 0) else "확인 필요")
    return 0 if fresh and bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

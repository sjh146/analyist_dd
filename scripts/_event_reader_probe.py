#!/usr/bin/env python3
"""_event_reader_probe.py — 이벤트 피처 리더가 실제로 event_features 를 읽는지 확인(읽기 전용).

WHY (2026-10-02): `event_*_5d` 18개가 죽어 있던 원인은 ① writer(event_features 빌더) 미실행
② 리더가 커버리지 9/200종목인 news_events 를 보던 것(수리: event_features 로 재배선).
수리 후 "특정 종목·날짜에서 0 → 0이 아닌 값"을 실측으로 확인해야 완료다(빌더·리더 양쪽 배선).

사용(컨테이너):
  docker exec -w /app stock_xgboost_ml python scripts/_event_reader_probe.py 090430:2026-10-01 ...
"""
import datetime as dt
import os
import sys

import psycopg2

sys.path.insert(0, "/app")
from app.feature_engine.news_event_features import NewsEventFeatures  # noqa: E402


def main(pairs):
    pg = dict(host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
              port=int(os.environ.get("POSTGRES_PORT", "5434")),
              user=os.environ.get("POSTGRES_USER", "stock_user"),
              password=os.environ.get("POSTGRES_PASSWORD", ""),
              dbname=os.environ.get("POSTGRES_DB", "stock_trading"))
    conn = psycopg2.connect(**pg)
    f = NewsEventFeatures()
    rc = 0
    for pair in pairs:
        code, d = pair.split(":") if ":" in pair else (pair, None)
        d = dt.date.fromisoformat(d) if d else None      # 리더는 date 객체를 받는다(str 아님)
        counts = f._get_event_counts_5d(code, conn, d)
        nz = {k: v for k, v in counts.items() if v}
        print(f"{code} {d} → nonzero {len(nz)}개 {dict(sorted(nz.items())[:5])}")
        if not nz:
            rc = 2
    conn.close()
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["090430:2026-10-01", "290650:2026-10-01", "090430:2026-09-23"]))

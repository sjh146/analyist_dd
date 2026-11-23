#!/usr/bin/env python3
"""오늘의 피드를 릴레이 저장소에 게시한다(고객 pull 대상).

- 원본: data/feed/screener_latest.json (파이프라인이 만든 발행 피드)
- 게시: data/feed_relay/feed/latest.json (원자적 교체)
- 신선도: generated_at 이 --max-age-hours(기본 18h) 보다 오래되면 거부
사용: python3 services/feed_relay/publish_feed.py [--allow-stale]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

FEED_SCHEMA_VERSION = 1


def now_kst() -> datetime:
    return datetime.now(timezone(timedelta(hours=9)))


def parse_ts(text: str) -> datetime | None:
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone(timedelta(hours=9)))
    return dt


def publish(src: str, dst: str, max_age_hours: float, allow_stale: bool) -> dict:
    with open(src, encoding="utf-8") as fh:
        feed = json.load(fh)
    generated = parse_ts(str(feed.get("generated_at") or ""))
    if generated is None:
        return {"ok": False, "reason": "generated_at 없음"}
    age_h = (now_kst() - generated).total_seconds() / 3600.0
    if age_h > max_age_hours and not allow_stale:
        return {"ok": False, "reason": "피드 오래됨 {0:.1f}h > {1}h".format(age_h, max_age_hours)}
    candidates = feed.get("candidates") or {}
    total = sum(len((v or {}).get("items") or []) for v in candidates.values())
    if total == 0:
        return {"ok": False, "reason": "후보 0건 — 게시 중단"}
    feed["feed_schema_version"] = FEED_SCHEMA_VERSION
    feed["published_at"] = now_kst().isoformat(timespec="seconds")
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(dst) or ".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(feed, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, dst)
    return {"ok": True, "published_at": feed["published_at"], "generated_at": feed["generated_at"],
            "age_h": round(age_h, 2), "candidates": {k: len((v or {}).get("items") or [])
                                                     for k, v in candidates.items()},
            "dst": dst}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="피드 게시")
    ap.add_argument("--src", default=os.path.join("data", "feed", "screener_latest.json"))
    ap.add_argument("--dst", default=os.path.join("data", "feed_relay", "feed", "latest.json"))
    ap.add_argument("--max-age-hours", type=float, default=18.0)
    ap.add_argument("--allow-stale", action="store_true")
    args = ap.parse_args(argv)
    out = publish(args.src, args.dst, args.max_age_hours, args.allow_stale)
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""피드의 단타(daytrading) 목록만 비운다 — 개장 게이트 차단 시 stale 후보 진입 방지.

다른 스크리너(close/swing) 항목과 regime 은 그대로 둔다.
사용: python3 scripts/feed_clear_daytrading.py [--dry-run] [--output data/feed/screener_latest.json]
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

FEED = os.path.join("data", "feed", "screener_latest.json")


def now_kst() -> str:
    return datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="seconds")


def clear(path: str, dry_run: bool = False) -> dict:
    with open(path, encoding="utf-8") as fh:
        feed = json.load(fh)
    candidates = feed.get("candidates") or {}
    before = len((candidates.get("daytrading") or {}).get("items") or [])
    candidates["daytrading"] = {"items": [], "reason": "blocked:open_gate", "cleared_at": now_kst()}
    feed["candidates"] = candidates
    if dry_run:
        return {"ok": True, "dry_run": True, "cleared_from": before}
    folder = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(feed, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return {"ok": True, "cleared_from": before, "path": path}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="피드 단타 목록 비우기")
    ap.add_argument("--output", default=FEED)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if not os.path.isfile(args.output):
        print(json.dumps({"ok": False, "error": "feed_not_found"}, ensure_ascii=False))
        return 1
    print(json.dumps(clear(args.output, args.dry_run), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

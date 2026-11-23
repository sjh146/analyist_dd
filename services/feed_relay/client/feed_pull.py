#!/usr/bin/env python3
"""고객 PC: 오늘의 추론 피드를 받아 트레이더 피드 경로에 원자적으로 쓴다.

실패하면 파일을 건드리지 않는다(어제 피드 유지). ack 은 성공했을 때만 보낸다.
사용: python3 feed_pull.py --config config.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone


def now_kst() -> str:
    return datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="seconds")


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def fetch_feed(cfg: dict, timeout: float = 20.0) -> dict:
    url = "{0}/v1/feed?customer_id={1}".format(cfg["base_url"].rstrip("/"), cfg["customer_id"])
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + cfg["token"]})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def write_atomic(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def post_json(url: str, token: str, payload: dict, timeout: float = 15.0) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Authorization": "Bearer " + token, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def pull(cfg: dict) -> dict:
    feed = fetch_feed(cfg)
    schema = int(feed.get("feed_schema_version") or 0)
    want = int(cfg.get("feed_schema_version") or schema or 1)
    if schema and schema != want:
        raise SystemExit("feed_schema 불일치: 서버 {0} != 클라이언트 {1}".format(schema, want))
    out = cfg["feed_out"]
    write_atomic(out, feed)
    ack = {"customer_id": cfg["customer_id"], "feed_generated_at": feed.get("generated_at"),
           "version": str(cfg.get("version") or ""), "acked_at": now_kst()}
    try:
        post_json(cfg["base_url"].rstrip("/") + "/v1/ack", cfg["token"], ack)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print("ack 실패(무시): {0}".format(exc), file=sys.stderr)
    return {"ok": True, "out": out, "generated_at": feed.get("generated_at"),
            "candidates": {k: len((v or {}).get("items") or [])
                           for k, v in (feed.get("candidates") or {}).items()}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="피드 pull")
    ap.add_argument("--config", default="config.json")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    print(json.dumps(pull(cfg), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

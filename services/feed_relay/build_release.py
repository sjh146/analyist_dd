#!/usr/bin/env python3
"""trader-agent 번들을 만들고 manifest 에 서명해 릴레이 저장소에 게시한다.

산출: <root>/releases/<version>/{bundle.zip, manifest.json}, <root>/channels.json 갱신
서명: HMAC-SHA256(release_key, canonical_json(manifest)). 고객 토큰과 분리된 키다.

사용:
  python3 services/feed_relay/build_release.py --version 1.0.0 --channel stable
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import sys
import zipfile
from datetime import datetime, timedelta, timezone

APP_DEFAULT = "/mnt/c/Users/jhshi/analyist_dd/trader-agent"
INCLUDE = (
    "trader_core",
    "runner",
    "run_market_loop.py",
    "config_live.json",
    "config.py",
)
SKIP_PARTS = {"__pycache__", ".git", "journal", "logs", "data"}
SKIP_SUFFIX = (".pyc", ".log", ".sqlite3", ".sqlite3-wal", ".sqlite3-shm", ".bak")
FEED_SCHEMA_VERSION = 1


def now_kst() -> str:
    return datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="seconds")


def iter_app_files(app_dir: str):
    for name in INCLUDE:
        full = os.path.join(app_dir, name)
        if os.path.isfile(full):
            yield full
            continue
        for dirpath, dirnames, filenames in os.walk(full):
            dirnames[:] = [d for d in dirnames if d not in SKIP_PARTS and not d.startswith(".")]
            for fn in sorted(filenames):
                if fn.endswith(SKIP_SUFFIX):
                    continue
                yield os.path.join(dirpath, fn)


def canonical(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign(manifest: dict, release_key: str) -> str:
    body = {k: v for k, v in manifest.items() if k != "signature"}
    return hmac.new(release_key.encode("utf-8"), canonical(body), hashlib.sha256).hexdigest()


def load_or_create_key(path: str) -> str:
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            key = fh.read().strip()
        if key:
            return key
        print("[build] 키 파일이 비어 있음 — 새로 만든다: " + path, file=sys.stderr)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    key = secrets.token_hex(32)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(key + "\n")
    os.chmod(path, 0o600)
    return key


def build(app_dir: str, root: str, version: str, channel: str, key_path: str) -> dict:
    if not os.path.isdir(app_dir):
        raise SystemExit("app dir 없음: " + app_dir)
    release_key = load_or_create_key(key_path)
    rel_dir = os.path.join(root, "releases", version)
    os.makedirs(rel_dir, exist_ok=True)
    bundle = os.path.join(rel_dir, "bundle.zip")

    files = []
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as zf:
        for full in iter_app_files(app_dir):
            rel = os.path.relpath(full, app_dir).replace("\\", "/")
            data = open(full, "rb").read()
            zf.writestr(rel, data)
            files.append({"path": rel, "sha256": hashlib.sha256(data).hexdigest(),
                          "size": len(data)})
    bundle_bytes = open(bundle, "rb").read()
    manifest = {
        "version": version,
        "channel": channel,
        "created_at": now_kst(),
        "feed_schema_version": FEED_SCHEMA_VERSION,
        "min_client_version": "1.0.0",
        "bundle_sha256": hashlib.sha256(bundle_bytes).hexdigest(),
        "bundle_size": len(bundle_bytes),
        "files": files,
        "alg": "hmac-sha256",
    }
    manifest["master_signature"] = sign(manifest, release_key)
    with open(os.path.join(rel_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)

    channels_path = os.path.join(root, "channels.json")
    channels = {}
    try:
        with open(channels_path, encoding="utf-8") as fh:
            channels = json.load(fh)
    except (OSError, ValueError):
        channels = {}
    channels[channel] = version
    with open(channels_path, "w", encoding="utf-8") as fh:
        json.dump(channels, fh, ensure_ascii=False, indent=2)
    return {"version": version, "channel": channel, "files": len(files),
            "bundle_kb": round(len(bundle_bytes) / 1024, 1)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="trader-agent 릴리스 번들 생성")
    ap.add_argument("--app-dir", default=APP_DEFAULT)
    ap.add_argument("--root", default=os.path.join("data", "feed_relay"))
    ap.add_argument("--version", required=True)
    ap.add_argument("--channel", default="stable", choices=("stable", "beta"))
    ap.add_argument("--key-path", default=os.path.join("data", "feed_relay", "release_key.txt"))
    args = ap.parse_args(argv)
    out = build(args.app_dir, args.root, args.version, args.channel, args.key_path)
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

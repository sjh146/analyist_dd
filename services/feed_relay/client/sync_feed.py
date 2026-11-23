#!/usr/bin/env python3
"""고객 PC: GitHub 배포 저장소를 pull 해 오늘 피드를 트레이더 경로에 놓는다.

- 최초 1회 clone(--depth 1), 이후 fetch+reset(전체 이력 없이 최신만)
- deploy/latest.json 무결성(meta.sha256)과 날짜(오늘, KST)를 검사
- 검사 실패 시 피드 파일을 건드리지 않고 종료코드 1 (거래 금지)

사용:
  python3 sync_feed.py --config C:\\TraderAgent\\config.json
  python3 sync_feed.py --config config.json --allow-stale
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

PRESERVE = {"config_live.json", "config.json", "version.json", "kill_switch.txt",
            "journal", "logs", "data", "__pycache__"}


def now_kst() -> datetime:
    return datetime.now(timezone(timedelta(hours=9)))


def git(args: list[str], cwd: str | None = None, timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)


def sync_repo(cfg: dict) -> dict:
    repo_dir = cfg["repo_dir"]
    branch = cfg.get("branch") or "main"
    if not os.path.isdir(os.path.join(repo_dir, ".git")):
        os.makedirs(os.path.dirname(os.path.abspath(repo_dir)) or ".", exist_ok=True)
        out = git(["clone", "--depth", "1", "--branch", branch, cfg["repo_url"], repo_dir])
        if out.returncode != 0:
            raise SystemExit("clone 실패: " + out.stderr.strip()[-300:])
    else:
        fetch = git(["-C", repo_dir, "fetch", "--depth", "1", "origin", branch])
        if fetch.returncode != 0:
            raise SystemExit("fetch 실패: " + fetch.stderr.strip()[-300:])
        reset = git(["-C", repo_dir, "reset", "--hard", "FETCH_HEAD"])
        if reset.returncode != 0:
            raise SystemExit("reset 실패: " + reset.stderr.strip()[-300:])
    head = git(["-C", repo_dir, "rev-parse", "HEAD"]).stdout.strip()
    return {"repo_dir": repo_dir, "head": head}


def verify_and_install(cfg: dict, allow_stale: bool) -> dict:
    repo_dir = cfg["repo_dir"]
    latest_path = os.path.join(repo_dir, "deploy", "latest.json")
    meta_path = os.path.join(repo_dir, "deploy", "meta.json")
    with open(latest_path, "rb") as fh:
        raw = fh.read()
    feed = json.loads(raw.decode("utf-8"))
    meta = {}
    try:
        with open(meta_path, encoding="utf-8") as fh:
            meta = json.load(fh)
    except (OSError, ValueError):
        meta = {}
    if meta.get("sha256") and hashlib.sha256(raw).hexdigest() != meta["sha256"]:
        raise SystemExit("무결성 실패: latest.json sha256 불일치")
    schema = int(feed.get("feed_schema_version") or 0)
    want = int(cfg.get("feed_schema_version") or schema or 1)
    if schema and schema != want:
        raise SystemExit("스키마 불일치: {0} != {1}".format(schema, want))
    generated = str(feed.get("generated_at") or "")
    try:
        gen_dt = datetime.fromisoformat(generated)
    except ValueError:
        raise SystemExit("generated_at 해석 실패: " + generated)
    if gen_dt.tzinfo is None:
        gen_dt = gen_dt.replace(tzinfo=timezone(timedelta(hours=9)))
    age_h = (now_kst() - gen_dt).total_seconds() / 3600.0
    if age_h > float(cfg.get("max_age_hours") or 18.0) and not allow_stale:
        raise SystemExit("피드 오래됨 {0:.1f}h — 거래 금지".format(age_h))
    out_path = cfg["feed_out"]
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(out_path) or ".", suffix=".tmp")
    with os.fdopen(fd, "wb") as fh:
        fh.write(raw)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, out_path)
    state = {"generated_at": generated, "synced_at": now_kst().isoformat(timespec="seconds"),
             "age_h": round(age_h, 2), "head": meta.get("publisher_head") or "",
             "candidates": {k: len((v or {}).get("items") or [])
                            for k, v in (feed.get("candidates") or {}).items()}}
    state_path = os.path.join(os.path.dirname(out_path), "sync_state.json")
    with open(state_path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
    return {"ok": True, "out": out_path, **state}


def read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def install_code(cfg: dict) -> dict:
    repo_app = os.path.join(cfg["repo_dir"], "app")
    if not os.path.isdir(repo_app):
        return {"code": "absent"}
    version = read_text(os.path.join(repo_app, "VERSION"))
    if not version:
        raise SystemExit("app/VERSION 없음 — 코드 번들 손상")
    app_dir = cfg["app_dir"]
    have = ""
    try:
        with open(os.path.join(app_dir, "version.json"), encoding="utf-8") as fh:
            have = str(json.load(fh).get("version") or "")
    except (OSError, ValueError):
        have = ""
    if have == version:
        return {"code": "unchanged", "version": version}
    parent = os.path.dirname(os.path.abspath(app_dir))
    os.makedirs(parent, exist_ok=True)
    staging = os.path.join(parent, ".app-new-" + time.strftime("%Y%m%d%H%M%S"))
    if os.path.isdir(staging):
        shutil.rmtree(staging)
    shutil.copytree(repo_app, staging, ignore=shutil.ignore_patterns(*PRESERVE))
    if not os.path.isfile(os.path.join(staging, "trader_core", "engine.py")):
        shutil.rmtree(staging, ignore_errors=True)
        raise SystemExit("코드 검증 실패: trader_core/engine.py 없음")
    backup = app_dir.rstrip("/\\") + ".bak-" + time.strftime("%Y%m%d%H%M%S")
    had_app = os.path.isdir(app_dir)
    if had_app:
        for name in PRESERVE:
            src = os.path.join(app_dir, name)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(staging, name))
        os.replace(app_dir, backup)
    try:
        os.replace(staging, app_dir)
        with open(os.path.join(app_dir, "version.json"), "w", encoding="utf-8") as fh:
            json.dump({"version": version,
                       "installed_at": now_kst().isoformat(timespec="seconds")}, fh)
    except OSError:
        shutil.rmtree(app_dir, ignore_errors=True)
        if had_app and os.path.isdir(backup):
            os.replace(backup, app_dir)
        raise
    if had_app:
        shutil.rmtree(backup, ignore_errors=True)
    return {"code": "installed", "version": version}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="피드 저장소 pull")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--allow-stale", action="store_true")
    ap.add_argument("--skip-git", action="store_true")
    ap.add_argument("--no-code", action="store_true")
    args = ap.parse_args(argv)
    with open(args.config, encoding="utf-8") as fh:
        cfg = json.load(fh)
    try:
        info = {"skipped_git": True} if args.skip_git else sync_repo(cfg)
        out = verify_and_install(cfg, args.allow_stale)
        if not args.no_code and cfg.get("install_code", True) and cfg.get("app_dir"):
            out.update(install_code(cfg))
    except SystemExit as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    out.update(info)
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

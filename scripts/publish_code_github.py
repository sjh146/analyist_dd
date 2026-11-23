#!/usr/bin/env python3
"""trader-agent 코드를 배포 저장소(trader-feed)의 app/ 에 게시한다.

고객 PC 는 장전 sync 한 번으로 피드(deploy/)와 코드(app/)를 함께 받는다.
게시 전 검사: 실행 진입점 존재, VERSION 형식, 금지 파일(로그·DB·비밀) 미포함.

사용:
  python3 scripts/publish_code_github.py --version 1.1.0
  python3 scripts/publish_code_github.py --version 1.1.0 --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

APP_DEFAULT = "/mnt/c/Users/jhshi/analyist_dd/trader-agent"
INCLUDE_DIRS = ("trader_core", "runner", "bridge")
INCLUDE_FILES = ("run_market_loop.py", "run_bridge.py", "strategy_params.json",
                 "config.example.json")
SKIP_PARTS = {"__pycache__", ".git", "tests", "journal", "logs", "data", ".pytest_cache"}
SKIP_SUFFIX = (".pyc", ".log", ".sqlite3", ".sqlite3-wal", ".sqlite3-shm", ".bak", ".tmp")
REQUIRED = ("trader_core/engine.py", "runner/loop.py", "run_market_loop.py")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def collect(app_dir: str) -> list[tuple[str, str]]:
    rows = []
    for name in INCLUDE_DIRS:
        root = os.path.join(app_dir, name)
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_PARTS and not d.startswith("."))
            for fn in sorted(filenames):
                if fn.endswith(SKIP_SUFFIX) or fn.endswith(".pyc"):
                    continue
                full = os.path.join(dirpath, fn)
                rows.append((os.path.relpath(full, app_dir).replace("\\", "/"), full))
    for name in INCLUDE_FILES:
        full = os.path.join(app_dir, name)
        if os.path.isfile(full):
            rows.append((name, full))
    return rows


def git(repo: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=180)


def publish(app_dir: str, repo_dir: str, version: str, dry_run: bool = False) -> dict:
    if not VERSION_RE.match(version):
        raise SystemExit("버전 형식 오류(X.Y.Z): " + version)
    rows = collect(app_dir)
    present = {rel for rel, _ in rows}
    missing = [r for r in REQUIRED if r not in present]
    if missing:
        raise SystemExit("필수 파일 누락: " + ", ".join(missing))
    if not os.path.isdir(os.path.join(repo_dir, ".git")):
        raise SystemExit("git 저장소 아님: " + repo_dir)

    dest_root = os.path.join(repo_dir, "app")
    if not dry_run and os.path.isdir(dest_root):
        shutil.rmtree(dest_root)
    staged = 0
    for rel, full in rows:
        dest = os.path.join(dest_root, rel)
        if dry_run:
            staged += 1
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(full, dest)
        staged += 1
    if not dry_run:
        with open(os.path.join(dest_root, "VERSION"), "w", encoding="utf-8") as fh:
            fh.write(version + "\n")
        info = {"version": version, "files": staged}
        with open(os.path.join(dest_root, "BUILD_INFO.json"), "w", encoding="utf-8") as fh:
            json.dump(info, fh, ensure_ascii=False, indent=2)

    if dry_run:
        return {"ok": True, "dry_run": True, "files": staged, "version": version}

    git(repo_dir, "add", "app")
    if git(repo_dir, "diff", "--cached", "--quiet").returncode == 0:
        return {"ok": True, "unchanged": True, "version": version, "files": staged}
    out = git(repo_dir, "commit", "-m", "code: v{0} ({1} files)".format(version, staged))
    if out.returncode != 0:
        raise SystemExit("commit 실패: " + out.stderr.strip()[-300:])
    push = git(repo_dir, "push", "origin", "main")
    if push.returncode != 0:
        raise SystemExit("push 실패: " + push.stderr.strip()[-300:])
    head = git(repo_dir, "rev-parse", "--short", "HEAD").stdout.strip()
    return {"ok": True, "version": version, "files": staged, "head": head}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="코드 번들 게시")
    ap.add_argument("--app-dir", default=APP_DEFAULT)
    ap.add_argument("--repo-dir", default=os.path.expanduser("~/trader-feed"))
    ap.add_argument("--version", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    try:
        out = publish(args.app_dir, args.repo_dir, args.version, args.dry_run)
    except SystemExit as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

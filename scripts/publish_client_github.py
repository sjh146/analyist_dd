#!/usr/bin/env python3
"""클라이언트 실행 파일을 배포 저장소(trader-feed)의 client/ 에 게시한다.

고객 PC 는 install.ps1 이 저장소에서 client/sync_feed.py·preflight.ps1 을 받아 쓰므로,
클라이언트 코드 수정도 git 으로 배포된다.

사용:
  python3 scripts/publish_client_github.py
  python3 scripts/publish_client_github.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = (
    ("services/feed_relay/client/sync_feed.py", "client/sync_feed.py"),
    ("deploy/customer/kit/install.ps1", "client/install.ps1"),
    ("deploy/customer/kit/preflight.ps1", "client/preflight.ps1"),
    ("deploy/customer/kit/uninstall.ps1", "client/uninstall.ps1"),
    ("deploy/customer/kit/README.md", "client/README.md"),
)


def git(repo: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=180)


def publish(repo_dir: str, dry_run: bool = False) -> dict:
    if not os.path.isdir(os.path.join(repo_dir, ".git")):
        raise SystemExit("git 저장소 아님: " + repo_dir)
    copied = []
    for src_rel, dst_rel in FILES:
        src = os.path.join(REPO, src_rel)
        if not os.path.isfile(src):
            raise SystemExit("원본 없음: " + src_rel)
        dst = os.path.join(repo_dir, dst_rel)
        copied.append(dst_rel)
        if dry_run:
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
    if dry_run:
        return {"ok": True, "dry_run": True, "files": copied}
    git(repo_dir, "add", "client")
    if git(repo_dir, "diff", "--cached", "--quiet").returncode == 0:
        return {"ok": True, "unchanged": True, "files": copied}
    out = git(repo_dir, "commit", "-m", "client: sync {0} files".format(len(copied)))
    if out.returncode != 0:
        raise SystemExit("commit 실패: " + out.stderr.strip()[-300:])
    push = git(repo_dir, "push", "origin", "main")
    if push.returncode != 0:
        raise SystemExit("push 실패: " + push.stderr.strip()[-300:])
    head = git(repo_dir, "rev-parse", "--short", "HEAD").stdout.strip()
    return {"ok": True, "files": copied, "head": head}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="클라이언트 파일 게시")
    ap.add_argument("--repo-dir", default=os.path.expanduser("~/trader-feed"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    try:
        out = publish(args.repo_dir, args.dry_run)
    except SystemExit as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

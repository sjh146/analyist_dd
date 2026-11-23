#!/usr/bin/env python3
"""~/.hermes/skills 의 analyist_dd 스킬을 리포 config/skills 로 미러하고 커밋한다.

WHY (2026-11-23 실측): 스킬 30개(quant 13 · trading 17, 1.8MB)가 ~/.hermes 아래에만 있어
버전관리·백업이 없었다. 하루에 5개가 갱신돼도 diff 가 남지 않아 무엇이 왜 바뀌었는지
추적할 수 없고, 잘못된 패치를 되돌릴 방법도 없었다.

사용:
  python3 scripts/sync_skills.py                 # 미러 + 변경 있으면 커밋/푸시
  python3 scripts/sync_skills.py --dry-run       # 무엇이 바뀔지만 출력
  python3 scripts/sync_skills.py --no-push       # 커밋까지만
종료코드: 0 정상(변경 없음 포함) · 1 미러/커밋 실패 · 2 푸시 실패
"""
from __future__ import annotations

import argparse
import filecmp
import os
import shutil
import subprocess
import sys

SRC_ROOT = os.path.join(os.path.expanduser("~"), ".hermes", "skills")
GROUPS = ("quant", "trading")
REPO = os.environ.get("PROJ_DIR", "/home/jhshi/analyist_dd")
DST_REL = os.path.join("config", "skills")
MANIFEST = "MANIFEST.json"


def _walk(root: str):
    out = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            full = os.path.join(dirpath, name)
            out.append(os.path.relpath(full, root))
    return sorted(out)


def diff_tree(src: str, dst: str) -> list[str]:
    """src 대비 dst 에서 추가/변경/삭제가 필요한 상대경로 목록(정렬)."""
    if not os.path.isdir(src):
        return []
    src_files = set(_walk(src))
    dst_files = set(_walk(dst)) if os.path.isdir(dst) else set()
    changed = set(src_files.symmetric_difference(dst_files))
    for rel in src_files & dst_files:
        if not filecmp.cmp(os.path.join(src, rel), os.path.join(dst, rel), shallow=False):
            changed.add(rel)
    return sorted(changed)


def sync_tree(src: str, dst: str, dry_run: bool = False) -> list[str]:
    """src 를 dst 로 미러(삭제 포함). 실제 복사한 변경 목록을 돌려준다."""
    changed = diff_tree(src, dst)
    if dry_run or not changed:
        return changed
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    return changed


def _run(cmd: list[str], cwd: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=env, timeout=180)


def commit_changes(repo: str, changed: list[str], push: bool = True) -> int:
    if not changed:
        return 0
    names = sorted({rel.split(os.sep)[0] for rel in changed})
    add = _run(["git", "add", DST_REL], repo)
    if add.returncode != 0:
        print(add.stderr.strip()[:300], file=sys.stderr)
        return 1
    status = _run(["git", "status", "--porcelain", DST_REL], repo)
    if not status.stdout.strip():
        return 0
    msg = "skills: 미러 갱신 ({0}개 파일, {1})".format(len(changed), ", ".join(names[:6]))
    commit = _run(["git", "commit", "-q", "-m", msg], repo)
    if commit.returncode != 0:
        print(commit.stderr.strip()[:300], file=sys.stderr)
        return 1
    print("커밋:", msg)
    if not push:
        return 0
    pushed = _run(["git", "push", "origin", "master"], repo)
    if pushed.returncode != 0:
        print("푸시 실패:", pushed.stderr.strip()[:300], file=sys.stderr)
        return 2
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="스킬 미러 + 커밋")
    ap.add_argument("--src-root", default=SRC_ROOT)
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    args = ap.parse_args(argv)

    total = []
    for group in GROUPS:
        src = os.path.join(args.src_root, group)
        dst = os.path.join(args.repo, DST_REL, group)
        changed = sync_tree(src, dst, dry_run=args.dry_run)
        if changed:
            print("{0}: {1}개 변경".format(group, len(changed)))
        total.extend(changed)

    if args.dry_run:
        for rel in total[:40]:
            print("  ", rel)
        return 0
    if not total:
        print("변경 없음")
        return 0
    return commit_changes(args.repo, total, push=not args.no_push)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""오늘의 추론 피드를 GitHub 배포 저장소(trader-feed)에 커밋·푸시한다.

고객 PC 는 이 저장소를 장 개시 전에 pull 한다. 게시 규칙:
  - generated_at 이 --max-age-hours(기본 18h) 이내
  - 후보 합계 1건 이상
  - 커밋 전 로컬 변경은 배포 파일만 staged (다른 변경은 건드리지 않음)

사용:
  python3 scripts/publish_feed_github.py --repo-dir ~/trader-feed
  python3 scripts/publish_feed_github.py --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

FEED_SCHEMA_VERSION = 1
DEPLOY_FILES = ("deploy/latest.json", "deploy/meta.json")


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
        return dt.replace(tzinfo=timezone(timedelta(hours=9)))
    return dt


def git(repo: str, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=timeout)


def prepare_payload(src: str, max_age_hours: float, allow_stale: bool) -> tuple[dict, dict]:
    with open(src, encoding="utf-8") as fh:
        feed = json.load(fh)
    generated = parse_ts(str(feed.get("generated_at") or ""))
    if generated is None:
        raise SystemExit("generated_at 없음: " + src)
    age_h = (now_kst() - generated).total_seconds() / 3600.0
    if age_h > max_age_hours and not allow_stale:
        raise SystemExit("피드 오래됨 {0:.1f}h > {1}h".format(age_h, max_age_hours))
    candidates = feed.get("candidates") or {}
    counts = {k: len((v or {}).get("items") or []) for k, v in candidates.items()}
    if sum(counts.values()) == 0:
        raise SystemExit("후보 0건 — 게시 중단")
    feed["feed_schema_version"] = FEED_SCHEMA_VERSION
    feed["published_at"] = now_kst().isoformat(timespec="seconds")
    raw = json.dumps(feed, ensure_ascii=False, indent=2).encode("utf-8")
    content = {k: v for k, v in feed.items() if k not in ("published_at",)}
    content_sha = hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    meta = {
        "published_at": feed["published_at"],
        "generated_at": feed["generated_at"],
        "feed_schema_version": FEED_SCHEMA_VERSION,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "content_sha256": content_sha,
        "candidates": counts,
        "age_h": round(age_h, 2),
        "publisher": os.uname().nodename,
    }
    return feed, meta


def write_files(repo: str, feed: dict, meta: dict) -> list[str]:
    latest = os.path.join(repo, "deploy", "latest.json")
    hist = os.path.join(repo, "deploy", "history",
                        str(feed.get("generated_at", ""))[:10] + ".json")
    meta_path = os.path.join(repo, "deploy", "meta.json")
    os.makedirs(os.path.dirname(hist), exist_ok=True)
    for path, payload in ((latest, feed), (hist, feed), (meta_path, meta)):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
    return ["deploy/latest.json", "deploy/meta.json",
            os.path.relpath(hist, repo).replace("\\", "/")]


def same_payload(repo: str, feed: dict, meta: dict) -> bool:
    """작업트리 파일이 새 페이로드와 같고 **HEAD 에도 반영돼 있는가**.

    WHY(2026-11-25): 이전 실행이 파일만 쓰고 커밋에 실패하면(실측: cron 이 root 로 돌아 git 의
    'dubious ownership' 거부 → 08:40·20:30 발행이 rc=1) 작업트리는 최신, HEAD 는 옛 상태로 남는다.
    그 상태에서 같은 페이로드가 다시 오면 파일 비교만으로는 '변경 없음'이 되어 **영원히 커밋되지 않고**
    고객 PC 는 낡은 피드를 계속 pull 한다(실측 17시간 지연). 그래서 미커밋 변경이 있으면 '같다'가 아니다.
    """
    path = os.path.join(repo, "deploy", "latest.json")
    try:
        with open(path, encoding="utf-8") as fh:
            old = json.load(fh)
        with open(os.path.join(repo, "deploy", "meta.json"), encoding="utf-8") as fh:
            old_meta = json.load(fh)
    except (OSError, ValueError):
        return False
    if not (old.get("generated_at") == feed.get("generated_at")
            and old_meta.get("content_sha256") == meta.get("content_sha256")):
        return False
    probe = git(repo, "status", "--porcelain", "--", *DEPLOY_FILES)
    if probe.returncode != 0 or probe.stdout.strip():
        return False
    return True


def publish(repo: str, feed: dict, meta: dict, remote: str, branch: str,
            dry_run: bool, message: str) -> dict:
    if not os.path.isdir(os.path.join(repo, ".git")):
        raise SystemExit("git 저장소 아님: " + repo)
    if same_payload(repo, feed, meta):
        return {"ok": True, "unchanged": True, "candidates": meta["candidates"]}
    paths = write_files(repo, feed, meta)
    if dry_run:
        return {"ok": True, "dry_run": True, "wrote": paths, "candidates": meta["candidates"]}
    git(repo, "add", *paths)
    diff = git(repo, "diff", "--cached", "--quiet")
    committed = False
    if diff.returncode != 0:
        out = git(repo, "commit", "-m", message)
        if out.returncode != 0:
            raise SystemExit("commit 실패: " + out.stderr.strip())
        committed = True
    pushed = False
    last_err = ""
    heads = git(repo, "ls-remote", "--heads", remote, branch).stdout.strip()
    for attempt in range(1, 4):
        if heads:
            pull = git(repo, "pull", "--rebase", "--autostash", remote, branch)
            if pull.returncode != 0:
                last_err = pull.stderr.strip()
                git(repo, "rebase", "--abort")
                continue
            push = git(repo, "push", remote, branch)
        else:
            push = git(repo, "push", "-u", remote, branch)
        if push.returncode == 0:
            pushed = True
            break
        last_err = push.stderr.strip()
    if not pushed:
        raise SystemExit("push 실패 3회: " + last_err)
    head = git(repo, "rev-parse", "--short", "HEAD").stdout.strip()
    return {"ok": True, "committed": committed, "pushed": pushed, "head": head,
            "wrote": paths, "candidates": meta["candidates"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="피드 GitHub 게시")
    ap.add_argument("--src", default=os.path.join("data", "feed", "screener_latest.json"))
    ap.add_argument("--repo-dir", default=os.path.expanduser("~/trader-feed"))
    ap.add_argument("--remote", default="origin")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--max-age-hours", type=float, default=18.0)
    ap.add_argument("--allow-stale", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--message", default="")
    args = ap.parse_args(argv)
    feed, meta = prepare_payload(args.src, args.max_age_hours, args.allow_stale)
    msg = args.message or "feed: {0} 후보 {1}".format(
        meta["generated_at"], sum(meta["candidates"].values()))
    try:
        out = publish(args.repo_dir, feed, meta, args.remote, args.branch, args.dry_run, msg)
    except SystemExit as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

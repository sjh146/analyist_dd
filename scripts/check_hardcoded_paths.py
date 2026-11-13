#!/usr/bin/env python3
"""하드코딩된 운영 경로를 찾는다 — 다른 사람이 클론했을 때 깨지는 지점.

  python3 scripts/check_hardcoded_paths.py            # 요약 + 상위 파일
  python3 scripts/check_hardcoded_paths.py --json
  python3 scripts/check_hardcoded_paths.py --strict   # 남아 있으면 exit 2

분류
  ops     : scripts/*.py(밑줄 없는 것), scripts/*.sh, config/, services/, tools/
  scratch : scripts/_*.py (연구용 일회성 — 남아 있어도 운영에는 영향 없음)
  docs    : *.md (문서 예시는 그대로 두는 편이 낫다)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from _paths import proj, proj_dir

LEGACY = proj_dir()
SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", "dumps", "log", "data"}
TEXT_EXT = {".py", ".sh", ".yml", ".yaml", ".service", ".json", ".toml", ".cfg", ".ini"}


def classify(rel: str) -> str:
    if rel.startswith("docs") or rel.endswith(".md"):
        return "docs"
    base = os.path.basename(rel)
    if base.startswith("_"):
        return "scratch"
    return "ops"


def scan(root: str):
    hits = {}
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if os.path.splitext(name)[1] not in TEXT_EXT:
                continue
            path = os.path.join(current, name)
            rel = os.path.relpath(path, root)
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as handle:
                    lines = handle.read().splitlines()
            except OSError:
                continue
            found = [(i + 1, line.strip()) for i, line in enumerate(lines) if LEGACY in line]
            if found:
                hits[rel] = found
    return hits


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="check_hardcoded_paths.py", description=__doc__)
    parser.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    parser.add_argument("--top", type=int, default=15)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true", help="ops 파일에 남아 있으면 exit 2")
    args = parser.parse_args(argv)

    hits = scan(args.root)
    buckets = {"ops": {}, "scratch": {}, "docs": {}}
    for rel, lines in hits.items():
        buckets[classify(rel)][rel] = lines

    total_ops = sum(len(v) for v in buckets["ops"].values())
    total_scratch = sum(len(v) for v in buckets["scratch"].values())
    total_docs = sum(len(v) for v in buckets["docs"].values())

    if args.json:
        print(json.dumps({
            "ops": {k: len(v) for k, v in sorted(buckets["ops"].items(), key=lambda kv: -len(kv[1]))},
            "scratch_files": len(buckets["scratch"]),
            "docs_files": len(buckets["docs"]),
            "counts": {"ops": total_ops, "scratch": total_scratch, "docs": total_docs},
        }, ensure_ascii=False, indent=1))
    else:
        print("하드코딩 경로 {0!r} 현황".format(LEGACY))
        print("  운영(ops)      : {0}건 / {1}파일".format(total_ops, len(buckets["ops"])))
        print("  일회성(scratch): {0}건 / {1}파일 — 운영 영향 없음".format(total_scratch, len(buckets["scratch"])))
        print("  문서(docs)     : {0}건 / {1}파일".format(total_docs, len(buckets["docs"])))
        if buckets["ops"]:
            print("\n운영 파일 상위 {0}개:".format(args.top))
            for rel, lines in sorted(buckets["ops"].items(), key=lambda kv: -len(kv[1]))[:args.top]:
                print("  {0:50s} {1:3d}건  (예: L{2})".format(rel, len(lines), lines[0][0]))
    if args.strict and total_ops:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

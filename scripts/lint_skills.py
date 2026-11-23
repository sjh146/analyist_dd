#!/usr/bin/env python3
"""Hermes 스킬 품질 린트 — frontmatter·트리거 문구·크기·references 링크.

WHY (2026-11-23 실측): 스킬 30개가 코드와 달리 린트 없이 갱신됐다. 큰 스킬은 107KB까지
자랐고(trading-stack-status-report), description 트리거가 비어 있으면 로딩 판단이 흔들린다.
코드에는 C001/C003 래칫이 있는데 스킬에만 규칙이 없었다.

검사(오류 = exit 1, 경고 = 출력만):
  E001 frontmatter 없음/깨짐
  E002 name 없음 또는 디렉터리명과 불일치
  E003 description 없음
  E004 description 이 트리거 문구('Use ...')로 시작하지 않음
  E005 references/ 링크가 실제 파일과 불일치
  W001 SKILL.md 40KB 초과(분리 권고)
  W002 본문 200줄 초과 참조 파일 링크 없음

사용: python3 scripts/lint_skills.py [--root ~/.hermes/skills] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

ROOT = os.path.join(os.path.expanduser("~"), ".hermes", "skills")
GROUPS = ("quant", "trading")
MAX_KB = 40
REF_RE = re.compile(r"references/([A-Za-z0-9_./\-]+\.md)")


def parse_frontmatter(text: str):
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    block = text[3:end]
    meta = {}
    for line in block.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, val = line.split(":", 1)
        meta[key.strip()] = val.strip().strip('"').strip("'")
    return meta


def lint_skill(skill_dir: str, known_refs: set[str] | None = None) -> dict:
    name = os.path.basename(skill_dir.rstrip(os.sep))
    path = os.path.join(skill_dir, "SKILL.md")
    errors, warnings = [], []
    if not os.path.isfile(path):
        return {"name": name, "errors": ["E001 SKILL.md 없음"], "warnings": [],
                "size_kb": 0}
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    meta = parse_frontmatter(text)
    if meta is None:
        errors.append("E001 frontmatter 없음/깨짐")
        meta = {}
    fm_name = meta.get("name", "")
    if not fm_name:
        errors.append("E002 name 없음")
    elif fm_name != name:
        errors.append("E002 name 불일치({0} != {1})".format(fm_name, name))
    desc = meta.get("description", "")
    if not desc:
        errors.append("E003 description 없음")
    elif not desc.startswith("Use "):
        errors.append("E004 description 이 트리거 문구('Use ...')로 시작하지 않음")
    for rel in REF_RE.findall(text):
        target = os.path.join(skill_dir, "references", rel)
        if os.path.isfile(target):
            continue
        if known_refs and os.path.basename(rel) in known_refs:
            continue
        errors.append("E005 references 링크 깨짐: {0}".format(rel))
    size_kb = round(os.path.getsize(path) / 1024, 1)
    if size_kb > MAX_KB:
        warnings.append("W001 SKILL.md {0}KB > {1}KB (references 분리 권고)".format(size_kb, MAX_KB))
    return {"name": name, "errors": errors, "warnings": warnings, "size_kb": size_kb}


def reference_index(root: str) -> set[str]:
    """스킬 전체의 references 파일명 집합 — 다른 스킬 참조(wsl-host-ops → references/x)를 허용한다."""
    names = set()
    for dirpath, _dirnames, filenames in os.walk(root):
        if os.path.basename(dirpath) == "references":
            names.update(filenames)
    return names


def lint_root(root: str, groups=GROUPS) -> dict:
    results = []
    known = reference_index(root)
    for group in groups:
        gdir = os.path.join(root, group)
        if not os.path.isdir(gdir):
            continue
        for name in sorted(os.listdir(gdir)):
            sdir = os.path.join(gdir, name)
            if os.path.isdir(sdir):
                item = lint_skill(sdir, known)
                item["group"] = group
                results.append(item)
    errors = [r for r in results if r["errors"]]
    warnings = [r for r in results if r["warnings"]]
    return {"checked": len(results), "errors": errors, "warnings": warnings,
            "results": results}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Hermes 스킬 린트")
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    report = lint_root(args.root)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("[skills] 검사 {0}개 · 오류 {1} · 경고 {2}".format(
            report["checked"], len(report["errors"]), len(report["warnings"])))
        for item in report["errors"]:
            for msg in item["errors"]:
                print("  ✗ {0}/{1}: {2}".format(item["group"], item["name"], msg))
        for item in report["warnings"]:
            for msg in item["warnings"]:
                print("  ⚠ {0}/{1}: {2}".format(item["group"], item["name"], msg))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

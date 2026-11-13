#!/usr/bin/env python3
"""하드코딩 경로를 PROJ_DIR 기반으로 바꾼다 (기본은 예행).

  python3 scripts/migrate_paths.py                 # 무엇이 바뀔지 출력만
  python3 scripts/migrate_paths.py --apply         # 파이썬만 수정
  python3 scripts/migrate_paths.py --apply --sh    # 쉘 스크립트까지

변환 규칙
  *.py : 따옴표 문자열 리터럴만
         ``"/home/jhshi/analyist_dd"``        → ``proj_dir()``
         ``"/home/jhshi/analyist_dd/<sub>"``  → ``proj("<sub>")``
         f-string/주석/이어붙이기는 건드리지 않는다. 상단에
         ``from _paths import proj, proj_dir`` 를 넣고 compile() 로 검증한 뒤에만 쓴다.
  *.sh : 주석 줄과 이미 ``${VAR:-<경로>}`` 로 덮어쓸 수 있는 줄은 건드리지 않는다.
         나머지 경로 리터럴은 ``${PROJ_DIR}`` 로 바꾸고, 상단에
         ``: "${PROJ_DIR:=/home/jhshi/analyist_dd}"`` 기본값을 넣는다.

제외: ``scripts/_*.py``(연구용 일회성), 이 도구 자신, services/, docs/
"""
from __future__ import annotations

import argparse
import os
import re
import sys

LEGACY = "/home/jhshi/analyist_dd"
IMPORT_LINE = "from _paths import proj, proj_dir\n"
SH_DEFAULT = ': "${PROJ_DIR:=%s}"\n' % LEGACY
PY_LITERAL = re.compile(r'(?<![A-Za-z0-9_])"(' + re.escape(LEGACY) + r')([^"]*)"')
SH_PROJ_ASSIGN = re.compile(r"^\s*(export\s+)?PROJ_DIR\s*=")
SKIP_PY = {"_paths.py", "migrate_paths.py", "check_hardcoded_paths.py"}


def py_candidates(root: str):
    base = os.path.join(root, "scripts")
    for name in sorted(os.listdir(base)):
        if name.endswith(".py") and not name.startswith("_") and name not in SKIP_PY:
            yield os.path.join(base, name)


def sh_candidates(root: str):
    for sub in ("scripts", os.path.join("config", "cron")):
        base = os.path.join(root, sub)
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            if name.endswith(".sh"):
                yield os.path.join(base, name)


def add_import(text: str) -> str:
    if IMPORT_LINE.strip() in text:
        return text
    lines = text.splitlines(keepends=True)
    last_import = -1
    for index, line in enumerate(lines[:80]):
        stripped = line.strip()
        if stripped.startswith("import ") or stripped.startswith("from "):
            last_import = index
    if last_import >= 0:
        lines.insert(last_import + 1, IMPORT_LINE)
        return "".join(lines)
    if text.lstrip().startswith(('"""', "'''")):
        quote = text.lstrip()[:3]
        start = text.find(quote)
        end = text.find(quote, start + 3)
        end = text.find("\n", end)
        if end != -1:
            return text[:end + 1] + IMPORT_LINE + text[end + 1:]
    return IMPORT_LINE + text


def convert_py(text: str):
    changes = []

    def repl(match):
        tail = match.group(2)
        if not tail:
            changes.append("proj_dir()")
            return "proj_dir()"
        sub = tail.lstrip("/")
        changes.append('proj("{0}")'.format(sub))
        return 'proj("{0}")'.format(sub)

    new = PY_LITERAL.sub(repl, text)
    if not changes:
        return text, []
    return add_import(new), changes


def convert_sh(text: str):
    """쉘: 주석·기존 오버라이드 줄은 보존, 나머지는 ${PROJ_DIR} 로."""
    lines = text.splitlines(keepends=True)
    out = []
    changed = 0
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("#") or LEGACY not in line:
            out.append(line)
            continue
        head = line[:line.find(LEGACY)]
        if ":-" in head or SH_PROJ_ASSIGN.match(line):
            out.append(line)
            continue
        out.append(line.replace(LEGACY, "${PROJ_DIR}"))
        changed += 1
    if not changed:
        return text, []
    if SH_DEFAULT not in text:
        if out and out[0].startswith("#!"):
            out.insert(1, SH_DEFAULT)
        else:
            out.insert(0, SH_DEFAULT)
    return "".join(out), [changed]


def verify_py(path: str, new_text: str) -> bool:
    try:
        compile(new_text, path, "exec")
        return True
    except SyntaxError as exc:
        print("  [skip] {0}: 변환 후 문법 오류 ({1})".format(os.path.basename(path), exc.msg))
        return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="migrate_paths.py", description=__doc__)
    parser.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--sh", action="store_true", help="쉘 스크립트도 변환")
    args = parser.parse_args(argv)

    touched = 0
    for path in py_candidates(args.root):
        text = open(path, encoding="utf-8").read()
        if LEGACY not in text:
            continue
        new_text, changes = convert_py(text)
        if not changes:
            continue
        print("{0}py  {1:45s} {2}곳".format("[apply] " if args.apply else "[check] ",
                                           os.path.relpath(path, args.root), len(changes)))
        if args.apply and verify_py(path, new_text):
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(new_text)
            touched += 1
    if args.sh:
        for path in sh_candidates(args.root):
            text = open(path, encoding="utf-8").read()
            new_text, changes = convert_sh(text)
            if not changes:
                continue
            print("{0}sh  {1:45s} {2}곳".format("[apply] " if args.apply else "[check] ",
                                               os.path.relpath(path, args.root), changes[0]))
            if args.apply:
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(new_text)
                touched += 1
    print("\n{0} 파일 {1}".format("변경" if args.apply else "대상", touched if args.apply else "위 목록"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

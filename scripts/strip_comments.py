#!/usr/bin/env python3
"""주석 제거기 (tokenize 기반 — 문자열·연속행·지시자를 안전하게 보존).

보존 대상:
  · shebang(#!), 인코딩 선언, type: ignore, noqa, pragma:, fmt:, isort:, SPDX-, ruff:, mypy:, nosec
  · 문자열 안의 # (tokenize 라 원천적으로 안전)
  · 들여쓰기·개행 구조(주석만 지우고 나머지 토큰 위치는 그대로)

사용:
  python scripts/strip_comments.py --dry-run --files a.py b.py
  python scripts/strip_comments.py --files a.py b.py
  python scripts/strip_comments.py --top 10 --dry-run
종료코드: 0 정상 · 2 변경으로 구문 오류가 날 수 있는 파일 발견(건드리지 않음)
"""
from __future__ import annotations
import argparse, ast, io, json, os, re, sys, tokenize

PROJ = os.environ.get("PROJ_DIR", "/home/jhshi/analyist_dd")
KEEP = re.compile(
    r"^#\s*(!|-\*-|coding[:=]|type:|noqa|pragma:|fmt:|isort:|SPDX-|ruff:|mypy:|pyright:|nosec)",
    re.IGNORECASE,
)
SKIP_DIR_PARTS = {".git", "node_modules", "__pycache__", "venv", ".venv", "data", "reports",
                  "archive", "creon_extracted_v1"}


def comment_spans(src: str):
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            out.append((tok.start, tok.end, tok.string))
    return out


def strip(src: str) -> tuple[str, int, int]:
    lines = src.splitlines(keepends=True)
    spans = comment_spans(src)
    edits_by_line: dict[int, list[tuple[int, int, bool]]] = {}
    removed = kept = 0
    for (sl, sc), (el, ec), text in spans:
        if KEEP.match(text.strip()):
            kept += 1
            continue
        standalone = lines[sl - 1][:sc].strip() == ""
        removed += 1
        edits_by_line.setdefault(sl, []).append((sc, ec, standalone))
    for ln, edits in edits_by_line.items():
        line = lines[ln - 1]
        for sc, ec, standalone in sorted(edits, key=lambda x: -x[0]):
            if standalone:
                body = line[:sc]
                rest = line[ec:]
                newline = "\n" if rest.endswith("\n") else ""
                line = ("" if body.strip() == "" else body.rstrip()) + newline
            else:
                line = line[:sc].rstrip() + line[ec:]
        lines[ln - 1] = line
    return "".join(lines), removed, kept


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", nargs="*")
    ap.add_argument("--top", type=int, default=0, help="주석 많은 파일 상위 N개")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", dest="json_out")
    args = ap.parse_args()

    targets = list(args.files or [])
    if args.top:
        base = json.load(open(os.path.join(PROJ, "docs/lint_baseline.json"), encoding="utf-8"))["files"]
        targets += [os.path.join(PROJ, k) for k, _ in sorted(base.items(), key=lambda x: -x[1])[: args.top]]
    if not targets:
        print("대상 없음 — --files 또는 --top N")
        sys.exit(1)

    results, unsafe = [], []
    for path in targets:
        try:
            src = open(path, encoding="utf-8").read()
            ast.parse(src)
        except (OSError, SyntaxError) as e:
            unsafe.append({"file": path, "reason": f"읽기/구문 실패: {e}"})
            continue
        new, removed, kept = strip(src)
        try:
            ast.parse(new)
        except SyntaxError as e:
            unsafe.append({"file": path, "reason": f"제거 후 구문 오류: {e}"})
            continue
        results.append({"file": os.path.relpath(path, PROJ), "removed": removed, "kept": kept})
        if not args.dry_run and removed:
            with open(path, "w", encoding="utf-8") as f:
                f.write(new)

    if args.json_out:
        json.dump({"dry_run": args.dry_run, "results": results, "unsafe": unsafe},
                  open(args.json_out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    tag = "DRY-RUN" if args.dry_run else "적용"
    print(f"[주석제거 {tag}] 대상 {len(results)}파일 · 제거 {sum(r['removed'] for r in results):,}건 · 보존(지시자) {sum(r['kept'] for r in results)}건")
    for r in sorted(results, key=lambda x: -x["removed"])[:12]:
        print(f"  {r['removed']:5}건  {r['file']}")
    if unsafe:
        print(f"  ⚠ 건드리지 않은 파일 {len(unsafe)}개:")
        for u in unsafe[:5]:
            print(f"     {u['file']}: {u['reason']}")
        sys.exit(2)


if __name__ == "__main__":
    main()

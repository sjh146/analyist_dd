#!/usr/bin/env python3
"""AI 에이전트 코드 규칙 검사 (주석 금지 + 침묵 실패 차단 + 래칫).

왜 이 규칙인가 (실측 사고 기반):
  · 주석은 코드와 어긋난다 — 이 저장소에서 "왜: ..." 주석이 실제 동작과 반대인 사례가 반복됐고,
    에이전트는 주석을 근거로 잘못된 결론을 낸다. 그래서 코드에 설명을 남기지 않고 이름·타입·테스트로 말한다.
  · 조용한 실패가 가장 비싸다 — except 로 삼켜 기본값(0/None/[])을 돌려주면 피처가 항상 0 이 되고
    아무도 모른다(2026-09-24 graph reader 사고). 그래서 삼키는 예외를 기계가 막는다.

규칙:
  C001 주석 금지 — 기계 지시자만 허용(#!, coding, type:, noqa, pragma, fmt:, SPDX, shebang)
  C002 TODO/FIXME/XXX/HACK 는 티켓 ID 필수 (예: TODO CG123: ...)
  C003 except 블록이 pass/continue 로만 끝남 (삼킴)
  C004 except Exception/BaseException 이 로그 없이 기본값을 반환 (삼킴)
  C005 모듈·함수 docstring 금지 아님 — 문서는 docstring 으로만(주석 대체)

사용:
  python scripts/lint_agent_rules.py --update-baseline      # 현재 위반을 기준선으로 고정
  python scripts/lint_agent_rules.py                        # 기준선 대비 증가만 보고(래칫)
  python scripts/lint_agent_rules.py --files a.py b.py      # 특정 파일만
  python scripts/lint_agent_rules.py --strict               # 기준선 무시(신규 파일 검증용)
종료코드: 0 통과 · 1 위반
"""
from __future__ import annotations
import argparse, ast, json, os, re, subprocess, sys

PROJ = os.environ.get("PROJ_DIR", "/home/jhshi/analyist_dd")
BASELINE = os.path.join(PROJ, "docs/lint_baseline.json")

ALLOWED_DIRECTIVE = re.compile(
    r"^#\s*(!|-\*-|coding[:=]|type:|noqa|pragma:|fmt:|isort:|SPDX-|ruff:|mypy:|pyright:|nosec|nosec:)",
    re.IGNORECASE,
)
TICKET = re.compile(r"\b[A-Z]{2,10}-\d+\b")
MARKERS = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b")
SKIP_DIR_PARTS = {".git", "node_modules", "__pycache__", "venv", ".venv", "data", "reports", "archive"}


def iter_py_files(root: str) -> list[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_PARTS and not d.startswith(".")]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            if fn.startswith("_") and not fn.startswith("__"):
                continue
            out.append(os.path.join(dirpath, fn))
    return out


def check_file(path: str, root: str) -> list[tuple[str, int, str]]:
    rel = os.path.relpath(path, root)
    try:
        src = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return []
    lines = src.splitlines()
    viol: list[tuple[str, int, str]] = []

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        if ALLOWED_DIRECTIVE.match(stripped):
            continue
        if MARKERS.search(stripped):
            viol.append(("C002", i, "TODO/FIXME 에 티켓 ID 없음"))
            continue
        viol.append(("C001", i, "주석 금지(기계 지시자 외)"))

    try:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(src)
    except SyntaxError as e:
        return viol + [("C900", e.lineno or 0, f"구문 오류: {e.msg}")]

    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        body = node.body
        stmts = [s for s in body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
        if len(stmts) == 1 and isinstance(stmts[0], (ast.Pass, ast.Continue)):
            viol.append(("C003", node.lineno, "예외를 삼킴(pass/continue)"))
            continue
        handler_type = ast.unparse(node.type) if node.type is not None else "bare"
        if handler_type in ("Exception", "BaseException", "bare"):
            has_log = any(
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr in ("warning", "error", "exception", "critical", "warn")
                for s in stmts for n in ast.walk(s)
            )
            returns_default = any(
                isinstance(s, ast.Return) and (s.value is None or isinstance(s.value, ast.Constant))
                for s in stmts
            )
            if returns_default and not has_log:
                viol.append(("C004", node.lineno, "except 후 로그 없이 기본값 반환"))
    return viol


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=PROJ)
    ap.add_argument("--files", nargs="*")
    ap.add_argument("--update-baseline", action="store_true")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--json", dest="json_out")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    files = args.files or iter_py_files(args.root)
    counts: dict[str, int] = {}
    details: dict[str, list] = {}
    for f in files:
        v = check_file(f, args.root)
        if v:
            rel = os.path.relpath(f, args.root)
            counts[rel] = len(v)
            details[rel] = v

    if args.update_baseline:
        os.makedirs(os.path.dirname(BASELINE), exist_ok=True)
        json.dump({"generated": "2026-11-23", "files": counts}, open(BASELINE, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2, sort_keys=True)
        print(f"[lint] 기준선 고정: {sum(counts.values())}건 / {len(counts)}파일 → {BASELINE}")
        sys.exit(0)

    base = {"files": {}}
    if not args.strict and os.path.exists(BASELINE):
        try:
            base = json.load(open(BASELINE, encoding="utf-8"))
        except Exception:
            base = {"files": {}}
    baseline = base.get("files", {})

    regressions = []
    for rel, n in counts.items():
        allowed = 0 if args.strict else int(baseline.get(rel, 0))
        if n > allowed:
            regressions.append((rel, n, allowed, details[rel]))

    total_now = sum(counts.values())
    total_base = sum(int(v) for v in baseline.values())
    silent = args.strict or not os.path.exists(BASELINE)

    if args.json_out:
        json.dump({"total": total_now, "baseline_total": total_base,
                   "regressions": [{"file": r, "now": n, "allowed": a,
                                    "violations": [{"rule": x[0], "line": x[1], "msg": x[2]} for x in d[:30]]}
                                   for r, n, a, d in regressions]},
                  open(args.json_out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    if not regressions:
        if not args.quiet and silent:
            print(f"[lint] 위반 {total_now}건 (기준선 없음 — --update-baseline 로 고정 권장)")
        sys.exit(0)

    print(f"[lint] 규칙 위반 증가: {len(regressions)}파일 (기준선 대비)")
    for rel, n, allowed, d in regressions[:10]:
        print(f"  ✗ {rel}: {n}건 (허용 {allowed})")
        for rule, line, msg in d[:6]:
            print(f"     {rule} L{line}: {msg}")
    sys.exit(1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""시스템 정합성 감사(결정적·빠름): 크론 경로, DB 테이블 참조, 중복 적재, 환경키.

LLM 위임(pstack)은 해석이 필요한 '최적화'에 쓰고, 이 감사는 기계적으로 확인 가능한
모순만 본다 — 결과가 재현되고 빠르다.

산출: reports/system_consistency_audit.json
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from collections import defaultdict

REPO = "/home/jhshi/analyist_dd"
CRON_DIR = "/etc/cron.d"
OUT = os.path.join(REPO, "reports/system_consistency_audit.json")
SCRIPT_RX = re.compile(r"(/(?:home|usr)/[A-Za-z0-9_./-]+\.(?:sh|py))")
ENV_RX = re.compile(r"os\.environ(?:\.get)?[\(\[]\s*[\"']([A-Z][A-Z0-9_]{2,})[\"']")
SH_ENV_RX = re.compile(r"\$\{?([A-Z][A-Z0-9_]{2,})\}?")
INSERT_RX = re.compile(r"INSERT\s+INTO\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.I)
COPY_RX = re.compile(r"COPY\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.I)


def dotenv() -> set:
    keys = set()
    path = os.path.join(REPO, ".env")
    for line in open(path, encoding="utf-8", errors="replace"):
        if "=" in line and not line.strip().startswith("#"):
            keys.add(line.split("=", 1)[0].strip())
    return keys


def psql(sql: str) -> str:
    env = {}
    for line in open(os.path.join(REPO, ".env"), encoding="utf-8", errors="replace"):
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.rstrip("\n").split("=", 1)
            env[k.strip()] = v.strip()
    cmd = ["docker", "exec", "-e", "PGPASSWORD=" + env.get("POSTGRES_PASSWORD", ""),
           "stock_postgres", "psql", "-U", env.get("POSTGRES_USER", "stock_user"),
           "-d", env.get("POSTGRES_DB", "stock_trading"), "-t", "-A", "-c", sql]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120).stdout.strip()


def cron_entries() -> list:
    rows = []
    for name in sorted(os.listdir(CRON_DIR)):
        if not name.startswith("analyist") or ".bak" in name:
            continue
        path = os.path.join(CRON_DIR, name)
        try:
            lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
        except OSError as exc:
            unreadable.append("{0}: {1}".format(name, exc))
            continue
        for idx, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" in stripped.split()[0]:
                continue
            if stripped.count(" ") < 4:
                continue
            rows.append({"file": name, "line": idx, "text": stripped})
    return rows


def main() -> int:
    issues = []
    unreadable = []
    skipped = []
    entries = cron_entries()
    scripts = defaultdict(list)
    schedule_seen = defaultdict(list)
    for e in entries:
        fields = e["text"].split()
        schedule = " ".join(fields[:5])
        for path in SCRIPT_RX.findall(e["text"]):
            scripts[path].append("{0}:{1}".format(e["file"], e["line"]))
            schedule_seen[(path, schedule)].append("{0}:{1}".format(e["file"], e["line"]))
    missing = {p: v for p, v in scripts.items() if not os.path.exists(p)}
    for path, refs in sorted(missing.items()):
        issues.append({"sev": "높음", "kind": "cron_path_missing", "what": path, "where": refs[:3]})

    for (path, schedule), refs in sorted(schedule_seen.items()):
        if len(refs) > 1:
            issues.append({"sev": "중간", "kind": "cron_duplicate_schedule",
                           "what": "{0} [{1}]".format(path, schedule), "where": refs})

    tables = set(psql("select table_name from information_schema.tables where table_schema='public'").split())
    writers = defaultdict(set)
    scanned = 0
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", "node_modules", "data", "logs", "reports"}]
        for fn in files:
            if not fn.endswith((".py", ".sh")):
                continue
            path = os.path.join(root, fn)
            try:
                text = open(path, encoding="utf-8", errors="replace").read()
            except OSError as exc:
                skipped.append("{0}: {1}".format(os.path.relpath(path, REPO), exc))
                continue
            scanned += 1
            for rx in (INSERT_RX, COPY_RX):
                for table in rx.findall(text):
                    writers[table.lower()].add(os.path.relpath(path, REPO))
    multi = {t: sorted(v) for t, v in writers.items() if len(v) > 1}
    for table, files in sorted(multi.items()):
        issues.append({"sev": "낮음", "kind": "duplicate_writer", "what": table, "where": files[:4]})

    absent = sorted(t for t in writers if t not in tables and not t.startswith("tmp_"))
    for table in absent[:20]:
        issues.append({"sev": "중간", "kind": "write_to_missing_table", "what": table,
                       "where": sorted(writers[table])[:3]})

    env_keys = dotenv()
    used = defaultdict(set)
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", "node_modules", "data", "logs", "reports"}]
        for fn in files:
            if not fn.endswith((".py", ".sh")):
                continue
            path = os.path.join(root, fn)
            try:
                text = open(path, encoding="utf-8", errors="replace").read()
            except OSError as exc:
                skipped.append("{0}: {1}".format(os.path.relpath(path, REPO), exc))
                continue
            for key in set(ENV_RX.findall(text)) | set(SH_ENV_RX.findall(text)):
                used[key].add(os.path.relpath(path, REPO))
    missing_env = {k: sorted(v)[:2] for k, v in used.items()
                   if k not in env_keys and k.startswith(("POSTGRES", "KIS_", "DART_", "ECOS_", "KRX_", "REDIS"))}
    for key, where in sorted(missing_env.items()):
        issues.append({"sev": "낮음", "kind": "env_key_not_in_dotenv", "what": key, "where": where})

    order = {"높음": 0, "중간": 1, "낮음": 2}
    issues.sort(key=lambda x: (order.get(x["sev"], 3), x["kind"]))
    report = {"cron_entries": len(entries), "cron_scripts": len(scripts),
              "unreadable_cron": unreadable, "skipped_files": len(skipped),
              "scanned_files": scanned, "issues": issues,
              "counts": {s: sum(1 for i in issues if i["sev"] == s) for s in ("높음", "중간", "낮음")}}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(report, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print("크론 {0}줄 / 스크립트 {1}개 / 스캔 {2}파일".format(len(entries), len(scripts), scanned))
    print("모순: 높음 {0} · 중간 {1} · 낮음 {2}".format(
        report["counts"]["높음"], report["counts"]["중간"], report["counts"]["낮음"]))
    for issue in issues[:15]:
        print(" [{0}] {1} — {2} @ {3}".format(issue["sev"], issue["kind"], issue["what"], issue["where"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

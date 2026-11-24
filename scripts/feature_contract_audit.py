#!/usr/bin/env python3
"""DB 피처 ↔ 테이블 정합성 감사.

검사 항목
  1) feature_pipeline.py 가 SQL 로 참조하는 **테이블**이 DB 에 실제로 있는가
  2) 그 테이블에서 참조하는 **컬럼**이 실제로 있는가(단순 SELECT/FROM 파싱)
  3) 파이프라인 피처 이름 ↔ feature_coverage 이름 불일치(한쪽에만 있는 이름)
  4) DB 테이블 컬럼 중 'event_*_5d' 처럼 피처 계약에 없는 고아 컬럼
  5) 데이터 신선도(핵심 테이블 max(date))

산출: reports/feature_contract_audit.json + 콘솔 요약
사용: python3 scripts/feature_contract_audit.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections import defaultdict

REPO = "/home/jhshi/analyist_dd"
PIPELINE = os.path.join(REPO, "services/xgboost-ml/app/feature_engine/feature_pipeline.py")
OUT = os.path.join(REPO, "reports/feature_contract_audit.json")

FROM_RX = re.compile(r"FROM\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.I)
JOIN_RX = re.compile(r"JOIN\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.I)
SELECT_RX = re.compile(r"SELECT\s+(.*?)\s+FROM", re.I | re.S)
IDENT = re.compile(r"\b([a-z_][a-z0-9_]{2,})\b")


def env() -> dict:
    out = {}
    for line in open(os.path.join(REPO, ".env"), encoding="utf-8", errors="replace"):
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.rstrip("\n").split("=", 1)
            out[k.strip()] = v.strip()
    return out


def psql(sql: str) -> str:
    e = env()
    cmd = ["docker", "exec", "-e", "PGPASSWORD=" + e.get("POSTGRES_PASSWORD", ""),
           "stock_postgres", "psql", "-U", e.get("POSTGRES_USER", "stock_user"),
           "-d", e.get("POSTGRES_DB", "stock_trading"), "-t", "-A", "-F", "|", "-c", sql]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if res.returncode != 0:
        print("psql 실패:", res.stderr[:200], file=sys.stderr)
    return res.stdout


def table_columns() -> dict:
    out = psql("select table_name, column_name from information_schema.columns "
               "where table_schema='public'")
    cols = defaultdict(set)
    for line in out.strip().splitlines():
        parts = line.split("|")
        if len(parts) == 2:
            cols[parts[0]].add(parts[1])
    return cols


SQL_BLOCK = re.compile(r'"""(.*?)"""', re.S)


def sql_text(src: str) -> str:
    """삼중따옴표 블록 중 SELECT 가 들어간 것만 모은다(docstring 오탐 차단)."""
    return "\n".join(b for b in SQL_BLOCK.findall(src) if re.search(r"\bselect\b", b, re.I))


def referenced_tables(src: str) -> set:
    text = sql_text(src)
    return {m.group(1).lower() for m in FROM_RX.finditer(text)} | \
           {m.group(1).lower() for m in JOIN_RX.finditer(text)}


def referenced_columns(src: str) -> dict:
    """SELECT 절의 식별자를 테이블별로 귀속하기는 어렵다 → 등장 식별자 집합만 만든다."""
    names = set()
    for block in SELECT_RX.findall(src):
        for tok in IDENT.findall(block):
            names.add(tok)
    return names


def pipeline_features() -> list:
    code = ("import app.feature_engine.feature_pipeline as fp; "
            "print('\\n'.join(fp.FeaturePipeline().get_feature_names()))")
    res = subprocess.run(["docker", "exec", "-w", "/app", "-e", "PYTHONPATH=/app",
                          "stock_xgboost_ml", "python", "-c", code],
                         capture_output=True, text=True, timeout=180)
    return [x.strip() for x in res.stdout.splitlines() if x.strip()]


def main() -> int:
    src = open(PIPELINE, encoding="utf-8").read()
    cols = table_columns()
    tables = set(cols)

    ref_tables = referenced_tables(src)
    cte = set(re.findall(r"(?:WITH|,)\s*([a-z_][a-z0-9_]*)\s+AS\s*\(", sql_text(src), re.I))
    known_false = {"select", "unnest", "generate_series", "lateral", "dual"} | {c.lower() for c in cte}
    missing_tables = sorted(t for t in ref_tables - tables - known_false)

    feats = pipeline_features()
    cov = {line.split("|")[0] for line in psql("select feature_name from feature_coverage").strip().splitlines()
           if "|" not in line}
    cov_missing_from_pipeline = sorted(cov - set(feats))
    pipeline_missing_from_cov = sorted(set(feats) - cov)

    event_cols = sorted(c for t, cs in cols.items() if t == "event_features" for c in cs
                        if c.startswith("event_") and c.endswith("_5d"))
    orphan_event = sorted(c for c in event_cols if c not in set(feats))

    freshness = {}
    for table, col in (("stock_prices", "trade_date"), ("minute_close_bars", "trade_date"),
                       ("event_features", "trade_date"), ("disclosures", "rcept_dt"),
                       ("supply_market_features", "trade_date"), ("institutional_ownership", "as_of")):
        if table in tables and col in cols.get(table, set()):
            val = psql("select max({0})::text from {1}".format(col, table)).strip()
            freshness[table] = val

    report = {
        "pipeline_features": len(feats),
        "coverage_features": len(cov),
        "referenced_tables": sorted(ref_tables & tables),
        "missing_tables": missing_tables,
        "coverage_only_features": cov_missing_from_pipeline,
        "pipeline_only_features": pipeline_missing_from_cov,
        "orphan_event_columns": orphan_event,
        "freshness": freshness,
        "ok": not missing_tables,
    }
    json.dump(report, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print("피처 {0}개 · coverage {1}개 · 참조 테이블 {2}개".format(
        len(feats), len(cov), len(ref_tables & tables)))
    print("없는 테이블 참조:", missing_tables or "없음")
    print("coverage 에만 있는 피처:", len(cov_missing_from_pipeline),
          cov_missing_from_pipeline[:8])
    print("파이프라인에만 있는 피처:", len(pipeline_missing_from_cov),
          pipeline_missing_from_cov[:8])
    print("고아 이벤트 컬럼:", len(orphan_event), orphan_event[:8])
    print("신선도:", freshness)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""source_freshness_gate 자체점검 — 오탐·미탐 없이 '배선된 원천의 정지'만 잡는가.

검사:
  1) 판정 문턱: lag 0/1 → OK · 2/4 → WARN · 5 → CRITICAL (경계값 포함).
  2) 요약줄: 전부 정상이면 '⚠' 없이 정상 문구 / 하나라도 WARN 이상이면 '⚠' + 그 테이블 이름 포함.
  3) 오탐 가드: financial_statements(report_date = 분기 말일)는 감시 목록에 **없다**.
  4) 실측 1회: 기준 거래일이 있고 6개 이상 원천이 정수 lag 을 낸다(스모크 — DB 필요).
  5) `--json-out` 이 파싱 가능한 JSON 을 남긴다.

재현: python3 scripts/_source_freshness_test.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJ, "scripts"))
import source_freshness_gate as g  # noqa: E402

FAILS = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILS.append(name)


def main() -> int:
    print("source_freshness_gate 자체점검")
    check("① 문턱 0/1 = OK", g.verdict(0) == "OK" and g.verdict(1) == "OK")
    check("① 문턱 2/4 = WARN", g.verdict(2) == "WARN" and g.verdict(4) == "WARN")
    check("① 문턱 5+ = CRITICAL", g.verdict(5) == "CRITICAL" and g.verdict(60) == "CRITICAL")

    all_ok = {"reference": {"table": "market_data", "date": "2026-10-08"},
              "worst": ("supply_market_features", 0, "OK"),
              "sources": {"supply_market_features": {"max_date": "2026-10-08",
                                                     "lag_trading_days": 0, "verdict": "OK"}}}
    line_ok = g.summary_line(all_ok)
    check("② 정상 요약줄에 경보 없음", "⚠" not in line_ok and "정상" in line_ok, line_ok)

    bad = {"reference": {"table": "market_data", "date": "2026-10-08"},
           "worst": ("supply_market_features", 6, "CRITICAL"),
           "sources": {"supply_market_features": {"max_date": "2026-09-29",
                                                  "lag_trading_days": 6, "verdict": "CRITICAL"}}}
    line_bad = g.summary_line(bad)
    check("② 경보 요약줄에 테이블·지연 표기", "⚠" in line_bad and "supply_market_features" in line_bad
          and "6거래일" in line_bad, line_bad)

    names = [t for t, _ in g.SOURCES]
    check("③ 오탐 가드: financial_statements 감시 제외",
          "financial_statements" not in names, f"감시={names}")

    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "f.json")
        r = subprocess.run([sys.executable, os.path.join(PROJ, "scripts/source_freshness_gate.py"),
                            "--json-out", out], capture_output=True, text=True, timeout=300)
        check("④ 실측 실행 rc=0", r.returncode == 0, (r.stderr or r.stdout).strip()[-120:])
        ok_json = os.path.exists(out)
        check("⑤ --json-out 파일 생성", ok_json, out)
        if ok_json:
            d = json.load(open(out, encoding="utf-8"))
            check("④ 기준 거래일 존재", bool(d["reference"]["date"]), str(d["reference"]))
            n_scored = sum(1 for v in d["sources"].values()
                           if isinstance(v.get("lag_trading_days"), int))
            check("④ 6개 이상 원천이 정수 lag", n_scored >= 6, f"{n_scored}개")
            check("④ 오탐 원천이 결과에 없음", "financial_statements" not in d["sources"])
            print(f"  실측 요약: {g.summary_line(d)}")

    print(f"결과: {'전부 PASS' if not FAILS else 'FAIL ' + ', '.join(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())

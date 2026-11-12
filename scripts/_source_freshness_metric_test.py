#!/usr/bin/env python3
"""구동기 metric `source_freshness` 배선 회귀 테스트 (2026-11-12).

WHY: CG165·XR26·CG179 등의 metric 이 `source_freshness` 인데 `summary_path`·`parse_by_metric`·
`judge_by_metric` 어디에도 분기가 없어, 매 틱 "알 수 없는 metric — 판정불가로 기록" 경고가
나고 원장에는 판정 없는 기록만 쌓였다(실측: 2026-11-12 20:00 틱). 감지기
(`scripts/source_freshness_gate.py`)는 이미 돌고 JSON 스키마도 고정이므로 배선만 하면
"신선도가 실제로 위반됐는가"를 원장에서 사후 확인할 수 있다.

검증하는 것:
  ① summary_path 가 기본 증거 경로(data/reports/source_freshness.json)와 --json-out 둘 다 해석
  ② 정상(전 원천 lag<2) → '충족'
  ③ WARN(2~4거래일) / CRITICAL(≥5거래일) 문구가 원천명·최신일을 담는다
  ④ **기준 원천 자체가 낡은 경우**(2026-11-09 실측 사고: 22거래일 공백이 '전 원천 정상'으로
     보고됨) → '기준 원천 정지 의심'
  ⑤ 오류 경로(빈 경로·파일 없음·mtime 미갱신)는 '판정불가'
  ⑥ judge 는 delta=None(모델 팔 실험이 아니므로 무개선 카운터·스코어보드에 영향 없음)

실행: python3 scripts/_source_freshness_metric_test.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_cycle():
    spec = importlib.util.spec_from_file_location(
        "model_engineer_cycle", os.path.join(PROJ, "scripts/model_engineer_cycle.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def write(payload: dict) -> str:
    fd, p = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    return p


def case_ok(m) -> list:
    return [
        {"reference": {"table": "market_data", "date": "2026-11-11", "lag_trading_days": 1},
         "sources": {"supply_market_features": {"max_date": "2026-11-11", "lag_trading_days": 0},
                     "macro_features": {"max_date": "2026-11-11", "lag_trading_days": 0}},
         "worst": ["supply_market_features", 0, "OK"], "errors": []},
        "충족",
    ]


def run() -> int:
    m = load_cycle()
    fails = []

    def check(name, cond, extra=""):
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  → {extra}"))
        if not cond:
            fails.append(name)

    # ① summary_path
    p_default = m.summary_path("source_freshness", "")
    check("① 기본 증거 경로", p_default.endswith("data/reports/source_freshness.json"), p_default)
    p_arg = m.summary_path("source_freshness",
                           "python3 scripts/source_freshness_gate.py --json-out /app/x.json")
    check("① --json-out 해석", p_arg.endswith("x.json"), p_arg)

    # ② 정상
    payload, want = case_ok(m)
    p = write(payload)
    r = m.parse_source_freshness(p, 0.0)
    os.remove(p)
    check("② 정상 → 충족", (r.get("verdict") or "").startswith(want), r.get("verdict"))

    # ③ WARN / CRITICAL
    for lag, tag in ((2, "WARN"), (6, "CRIT")):
        p = write({"reference": {"table": "market_data", "date": "2026-11-11",
                                 "lag_trading_days": 0},
                   "sources": {"event_features": {"max_date": "2026-11-02",
                                                  "lag_trading_days": lag}},
                   "worst": ["event_features", lag, tag], "errors": []})
        r = m.parse_source_freshness(p, 0.0)
        os.remove(p)
        v = r.get("verdict") or ""
        check(f"③ {tag} {lag}거래일", tag in v and "event_features" in v and "2026-11-02" in v, v)

    # ④ 기준 자체 정지(2026-11-09 사고 재현)
    p = write({"reference": {"table": "market_data", "date": "2026-10-08",
                             "lag_trading_days": 22},
               "sources": {"macro_features": {"max_date": "2026-10-08",
                                              "lag_trading_days": 0}},
               "worst": ["macro_features", 0, "OK"], "errors": []})
    r = m.parse_source_freshness(p, 0.0)
    os.remove(p)
    check("④ 기준 정지 의심", "기준 원천 정지 의심" in (r.get("verdict") or ""), r.get("verdict"))

    # ⑤ 오류 경로
    check("⑤ 빈 경로", "경로 없음" in (m.parse_source_freshness("", 0.0).get("error") or ""))
    check("⑤ 파일 없음", "파일 없음" in (
        m.parse_source_freshness("/tmp/_no_such_freshness_xyz.json", 0.0).get("error") or ""))
    p = write(case_ok(m)[0])
    stale = m.parse_source_freshness(p, os.path.getmtime(p) + 10)
    os.remove(p)
    check("⑤ mtime 미갱신", "미갱신" in (stale.get("error") or ""), stale.get("error"))

    # ⑥ judge delta=None
    item = {"id": "CG165", "metric": "source_freshness"}
    v, d, delta = m.judge_by_metric(item, {"verdict": "충족 — 전 원천 신선(지연 <2거래일)",
                                           "detail": "x"})
    check("⑥ judge delta None", delta is None and v.startswith("충족"), (v, delta))

    print(f"\n{'ALL PASS' if not fails else 'FAILED: ' + ', '.join(fails)}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(run())

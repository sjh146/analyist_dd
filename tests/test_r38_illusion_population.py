#!/usr/bin/env python3
"""R38(B안) 회귀 — 커버리지 착시 알림의 인구를 '모델 창(120일)'으로 분리한다.

WHY(실측 2026-11-21): feature_coverage 202행 중 모델 창(120일)은 131행, 착시>0.10 은
전 창 61 / 모델 창 26. 야간 빌더가 window_days 를 격자 스팬(42·44·521·522일)으로 다시 쓰면
지표가 61~63 으로 튀고 전량 재계산이 120 으로 덮으면 26 으로 돌아온다 — 데이터는 그대로인데
판정만 바뀐다. 그래서 알림은 모델 창 인구만 세고, 전체 인구값은 정보용으로 강등했다.
A안(빌더 로드창을 120거래일로 고정)은 R43(12개월 모멘텀)과 충돌해 채택하지 않았다.
"""
import os
import re
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import dq_snapshot as dq  # noqa: E402

QUERIES = os.path.join(ROOT, "config", "postgres-exporter", "queries.yaml")
ALERTS = os.path.join(ROOT, "config", "prometheus", "alert.dq.rules.yml")

MODEL = "dq_feature_coverage_illusion_count_model_window"
ALLROWS = "dq_feature_coverage_illusion_count"


def _metric(name):
    for row in dq.SPECS:
        if row[0] == name:
            return row
    raise AssertionError(f"{name} 가 SPECS 에 없다")


def test_model_window_metric_is_the_alarm():
    warn, breach = _metric(MODEL)[2], _metric(MODEL)[3]
    assert warn is not None and breach is not None, "모델 창 지표가 알림 대상이어야 한다"
    # 기준선 실측 26 위에 문턱이 있어야 한다(기준선 아래면 상시 발화).
    assert 26 < warn < breach


def test_all_rows_metric_is_informational():
    row = _metric(ALLROWS)
    assert row[2] is None and row[3] is None, "전 창 인구값은 정보용(문턱 없음)이어야 한다"


def test_alert_rule_uses_model_window_metric():
    doc = yaml.safe_load(open(ALERTS, encoding="utf-8"))
    exprs = []
    for group in doc.get("groups", []):
        for rule in group.get("rules", []):
            if rule.get("alert") == "CoverageIllusion":
                exprs.append(rule.get("expr", ""))
    assert exprs, "CoverageIllusion 규칙이 없다"
    assert any(MODEL in e for e in exprs)
    assert not any(re.search(rf"\b{ALLROWS}\b(?!_model_window)", e) for e in exprs), (
        "전 창 지표로 알림하면 창 라벨 드리프트로 26↔63 오탐이 돌아온다"
    )


def _dq_feature_block():
    lines = open(QUERIES, encoding="utf-8").read().splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("dq_feature:"))
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if re.match(r"^[A-Za-z_]", lines[i]):     # 다음 최상위 키
            end = i
            break
    return lines[start:end]


def test_select_columns_and_metrics_stay_aligned():
    """postgres_exporter 는 SELECT 컬럼과 metrics 를 순서로 짝짓는다 — 어긋나면 전부 밀린다."""
    block = _dq_feature_block()
    text = "\n".join(block)
    sql = text.split("  query: |", 1)[1].split("  metrics:", 1)[0]
    aliases = re.findall(r"\bAS\s+([a-z_][a-z0-9_]*)\b", sql, flags=re.I)
    names = re.findall(r"^\s{4}- ([a-z_][a-z0-9_]*):\s*$", text, flags=re.M)
    assert aliases == names, f"SELECT/metrics 순서 불일치\n aliases={aliases}\n metrics={names}"
    assert MODEL.replace("dq_feature_", "") in aliases

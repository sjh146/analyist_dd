"""run_with_claim.parse_claim 회귀 — 총계 줄이 있으면 이중 계산하지 않는다.

실측 배경(2026-09-28): 수급 러너가 항목별 `+N행` 과 총계 `연장 완료: +119행` 을 함께 찍어
claimed 가 238(=90+29+119)로 부풀었다. 같은 실행에 러너 자신의 신고(소스 120/저장 119)까지
기록되어 dq_runner_claim 이 두 행이 되고 gap·source_rows 가 왜곡됐다.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import run_with_claim as rwc  # noqa: E402

SUPPLY_OUT = """기준 거래일 2026-09-28 / 대상 2종목 / 목표 250영업일 / 완료 318 / 남음 2
  [1/2] 000080: +90행 (총 269행, 최소일 2025-08-19)
  [2/2] 000100: +29행 (총 29행, 최소일 2026-08-13)

연장 완료: +119행 / 호출 4회
  foreign_institutional: 345종목 88193행 (2025-08-14~2026-09-23) / 250영업일 이상 317종목
  자기신고: 소스 120 / 저장 119 / 신규 119 (일치)
"""


def test_summary_line_wins_over_item_sum():
    assert rwc.parse_claim(SUPPLY_OUT, rwc.DEFAULT_PATTERNS) == 119


def test_item_sum_when_no_summary_line():
    text = "  [1/3] 005930: +12행\n  [2/3] 000660: +30행\n"
    assert rwc.parse_claim(text, rwc.DEFAULT_PATTERNS) == 42


def test_no_numbers_is_none():
    assert rwc.parse_claim("대상 종목 없음\n", rwc.DEFAULT_PATTERNS) is None


def test_english_total_hint():
    text = "row A +5 rows\nTOTAL: +7 rows\n"
    assert rwc.parse_claim(text, rwc.DEFAULT_PATTERNS) == 7

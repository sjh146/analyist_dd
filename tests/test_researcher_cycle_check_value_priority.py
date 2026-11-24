"""구동기 eval_check — 판정 숫자는 '마지막 줄 단독 정수'를 우선한다 (R48 실측 회귀).

실측 2026-11-25 00:47: R48 프로브가 직전 거래일 봉 가용 마켓 0 을 내보냈는데, 마지막 출력 줄이
``... 문턱 마켓당 500행`` 이었다 → ``nums[-1]`` 이 **문턱값 500** 을 집어 '500 >= 2 → 충족' 으로
오판했다(실제 0). 러너가 판정 숫자를 마지막 줄 단독으로 찍으면 구동기는 그 값을 써야 한다.
마지막 줄이 숫자가 아니면 종전 휴리스틱(nums[-1])으로 폴백해 기존 항목 판정을 바꾸지 않는다.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import researcher_cycle as rc  # noqa: E402


def test_bare_number_last_line_wins_over_threshold_text(monkeypatch):
    out = "{\"min_rows\": 500, \"markets\": {\"KOSPI\": 0}}\\n가용 마켓수 0 (문턱 마켓당 500행)\\n0"
    item = {"id": "RX", "check": f"printf '%s\\n' '{out}'", "check_target": {"op": ">=", "value": 2}}
    val, detail, passed = rc.eval_check(item)
    assert val == 0.0
    assert passed is False
    assert "미달" in detail


def test_text_last_line_falls_back_to_last_number(monkeypatch):
    out = "start 7\\n가용 마켓수 0 (문턱 마켓당 500행)"
    item = {"id": "RX", "check": f"printf '%s\\n' '{out}'", "check_target": {"op": ">=", "value": 500}}
    val, detail, passed = rc.eval_check(item)
    assert val == 500.0 and passed is True

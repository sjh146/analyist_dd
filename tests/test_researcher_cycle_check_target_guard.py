"""구동기 eval_check — check_target 이 수치가 아니어도 **죽지 않는다** (R24 실측 회귀).

실측 2026-10-01 15:35: R24 항목의 ``check_target.value`` 가 서술 문자열
``"opnd_yn == 'Y'"`` 이어서 ``float()`` 이 ValueError 를 냈고, 그 예외가 실행 전체를 죽여
**명령은 돌았는데 원장 기록 없이 프로세스가 사라졌다**(state.json 만 잔존 → 다음 틱이
'기록 없이 죽었다'로 보고). 구동기는 어떤 항목 정의에도 죽지 않고 '판정불가'로 남아야 한다.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import researcher_cycle as rc  # noqa: E402


def test_non_numeric_check_target_returns_unjudgeable(monkeypatch):
    item = {"id": "RX", "check": "echo 5", "check_target": {"op": "==", "value": "opnd_yn == 'Y'"}}
    val, detail, passed = rc.eval_check(item)     # 예외가 나면 회귀
    assert passed is None
    assert "판정불가" in detail and "수치" in detail


def test_non_numeric_check_target_with_output_number(monkeypatch):
    item = {"id": "RX", "check": "echo 3", "check_target": {"op": "<=", "value": "숫자 아님"}}
    val, detail, passed = rc.eval_check(item)
    assert val == 3.0 and passed is None


def test_numeric_check_target_still_judges(monkeypatch):
    item = {"id": "RX", "check": "echo 3", "check_target": {"op": "<=", "value": 2}}
    val, detail, passed = rc.eval_check(item)
    assert (val, passed) == (3.0, False)
    item["check_target"] = {"op": "<=", "value": 3}
    assert rc.eval_check(item)[2] is True

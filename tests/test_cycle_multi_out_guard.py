"""다중 산출 루프 경고 회귀 테스트 (2026-11-26 CG502 실측).

WHY: 구동기 파서는 요약 JSON **하나**를 읽는다. 셸 `for` 루프가 오프셋마다 JSON 을 쓰는 항목에서
판정이 마지막 파일 하나로 이뤄지는데, 그 사실이 판정 문구에 없으면 '전 오프셋 확인'으로 오독된다.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import model_engineer_cycle as mec  # noqa: E402


LOOP_CMD = ("docker exec stock_xgboost_ml sh -c 'cd /app && for D in 2026-05-04 2026-05-06; do "
            "OMP_NUM_THREADS=2 python -u scripts/x.py --k 5 --json-out /app/reports/overnight/o_$D.json; done'")


def test_loop_with_variable_output_is_flagged():
    w = mec._multi_out_warning({"command": LOOP_CMD})
    assert w and "마지막 파일" in w and "$D.json" in w, w


def test_single_output_is_not_flagged():
    cmd = "docker exec c sh -c 'cd /app && python -u scripts/x.py --json-out /app/o.json'"
    assert mec._multi_out_warning({"command": cmd}) is None


def test_loop_without_variable_output_is_not_flagged():
    cmd = "for i in 1 2; do python x.py --json-out /app/o.json; done"
    assert mec._multi_out_warning({"command": cmd}) is None


def test_missing_command_is_safe():
    assert mec._multi_out_warning({}) is None
    assert mec._multi_out_warning(None) is None

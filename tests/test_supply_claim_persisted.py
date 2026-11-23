"""자기신고 persisted_rows 계약: '테이블 총계'가 아니라 '이번 실행이 쓴 행수'.

실측 2026-11-23 20:16: 빌더가 claimed 112,313 / persisted 1,220,890(테이블 총계)을 남겨
dq_claim_gap = -1,108,577 이 됐다. 적재는 정상인데 지표는 실패처럼 보였다.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import build_supply_market_features as bsf  # noqa: E402


def test_persisted_is_copied_rows_not_table_total():
    assert bsf.claim_persisted_rows(112_313, table_total=1_220_890) == 112_313


def test_persisted_ignores_missing_copied_rows():
    assert bsf.claim_persisted_rows(None) == 0


def test_gap_is_zero_when_every_generated_row_was_written():
    claimed = 112_313
    persisted = bsf.claim_persisted_rows(claimed, table_total=1_220_890)
    assert claimed - persisted == 0

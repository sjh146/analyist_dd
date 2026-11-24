"""이벤트 유형 매칭 확장 검증 — 접두어 제거·신규 유형·정기공시 제외."""
from __future__ import annotations

import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_event_features", ROOT / "scripts/build_event_features.py")
bef = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bef)

NAMES = [name for name, _ in bef.EVENT_PATTERNS]


def kinds(report_nm: str) -> set[str]:
    return {NAMES[i] for i in bef.classify(report_nm)}


def test_bracket_prefix_is_stripped():
    assert "shareholder_meeting" in kinds("[기재정정]주주총회소집결의")
    assert "option_grant" in kinds("[기재정정]주식매수선택권부여에관한신고")


def test_new_event_types_match():
    assert "ir" in kinds("기업설명회(IR)개최")
    assert "dividend" in kinds("현금ㆍ현물배당결정")
    assert "debt_guarantee" in kinds("타인에대한채무보증결정")
    assert "investment" in kinds("타법인주식및출자증권취득결정")
    assert "rumor" in kinds("풍문또는보도에대한해명(미확정)")
    assert "treasury" in kinds("주식소각결정")
    assert "stake_change" in kinds("최대주주등소유주식변동신고서")


def test_periodic_reports_are_not_events():
    assert kinds("사업보고서 (2025.12)") == set()
    assert kinds("분기보고서 (2026.09)") == set()


def test_prefix_only_name_is_safe():
    assert kinds("[기재정정]") == set()

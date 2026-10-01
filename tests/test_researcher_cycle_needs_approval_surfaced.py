"""researcher_cycle.approval_asks — 승인 문구가 어떤 필드명으로 적혀도 보고에 올라오는지 검증.

WHY (실측 2026-10-01 20:0x): 틱 보고의 ⚠ 줄은 `setup_needed` 만 읽었다. 그런데 R25
(status=needs_approval, 'DART 공시 정기 러너 크론 신설 승인')는 요구사항을 `needs`
(문자열)에 적어 등록돼 있어 **어떤 틱 보고에도 한 번도 안 올라왔다** — 백로그 관례가
`setup_needed`(list) 하나로 고정돼 있지 않은데 읽는 쪽이 그걸 가정한 탓이다.
'자동 추출이라 누락이 없다'는 전제가 필드명 하나로 깨지므로 경계를 테스트로 고정한다.

경계:
  · setup_needed(list) → 그대로
  · needs(str)        → 올라온다(종전 누락 경로)
  · 문자열을 문자 단위로 쪼개지 않는다(오염 금지)
  · 둘 다 있으면 중복은 한 번만
  · 빈 값(None/''/[]) → 아무 줄도 만들지 않는다(빈 승인 요청 금지)
"""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "researcher_cycle", ROOT / "scripts" / "researcher_cycle.py")
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)


def test_needs_string_is_surfaced():
    """`needs`(str) 만 있는 항목도 승인 요청으로 올라와야 한다 — R25 누락 경로."""
    asks = rc.approval_asks({"status": "needs_approval",
                             "needs": "크론 등록 승인 1건(스케줄 신설)"})
    assert asks == ["크론 등록 승인 1건(스케줄 신설)"]


def test_string_is_not_split_per_character():
    """문자열을 그대로 iterate 하면 보고가 문자 단위로 오염된다."""
    asks = rc.approval_asks({"setup_needed": "단일 문자열 승인"})
    assert asks == ["단일 문자열 승인"], asks


def test_setup_needed_list_unchanged():
    asks = rc.approval_asks({"setup_needed": ["A", "B"]})
    assert asks == ["A", "B"]


def test_dedupes_when_both_fields_agree():
    asks = rc.approval_asks({"setup_needed": ["동일 문구"], "needs": "동일 문구"})
    assert asks == ["동일 문구"]


def test_empty_values_produce_nothing():
    assert rc.approval_asks({}) == []
    assert rc.approval_asks({"setup_needed": None, "needs": ""}) == []
    assert rc.approval_asks({"setup_needed": [], "needs": "   "}) == []


def test_blank_entries_filtered():
    assert rc.approval_asks({"setup_needed": ["A", "", "  "]}) == ["A"]

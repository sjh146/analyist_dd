"""저작 단계 결과를 판정 문구에 합칠 때의 라벨·보존 회귀 테스트.

WHY(실측 2026-09-28 15:36 R11): `ensure_artifact` 가 '산출물 이미 존재 → 저작 생략'으로 돌려주면
info 에는 `skipped=True` 만 있고 `exists` 키가 없다. 예전 조립부는 `exists` 만 보고 else 로 떨어져
① 라벨이 `[저작실패]` 로 잘못 찍히고 ② check 판정 문구(`5 >= 3 → 충족`)를 통째로 덮어써
수치 근거가 사라졌다. 자율 루프에서 원장 detail 은 유일한 증거이므로 두 성질을 모두 고정한다.
"""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "researcher_cycle", ROOT / "scripts" / "researcher_cycle.py")
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)


def test_skipped_authoring_is_labelled_skipped_and_keeps_check_detail():
    out = rc._merge_authorship_detail(
        "산출물 이미 존재(scripts/x.py) — 저작 생략", {"target": "scripts/x.py", "skipped": True},
        "R11: 5 >= 3 → 충족")
    assert "[저작실패]" not in out
    assert out.startswith("[저작생략]")
    assert "5 >= 3 → 충족" in out          # 수치 근거가 살아 있어야 한다


def test_created_artifact_is_labelled_authoring():
    out = rc._merge_authorship_detail("저작 완료", {"target": "x.py", "exists": True},
                                      "R12: 4 >= 3 → 충족")
    assert out.startswith("[저작] ") and "충족" in out


def test_missing_artifact_is_labelled_failure():
    out = rc._merge_authorship_detail("저작 실패 — 산출물 없음(x.py)", {"target": "x.py"},
                                      "R12: 1 >= 3 → 미달")
    assert out.startswith("[저작실패]") and "미달" in out


def test_no_authoring_keeps_detail_untouched():
    assert rc._merge_authorship_detail("", {}, "R3: 700 >= 500 → 충족") == "R3: 700 >= 500 → 충족"

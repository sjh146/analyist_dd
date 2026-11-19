"""build_event_rolling 단위 테스트 — docs/spec_event_rolling.md §3 계약 5개.

WHY: event_*_5d 54개가 nnz<5% 로 죽어 레벨(20행 평균)·추세(5행/20행)·묶음으로 재정의한
피처의 창 계산이 틀리면 "재판정" 자체가 잘못된 수치 위에서 이루어진다(창 정의가 바뀌면
alive/dead 가 뒤집힌 전례 — docs/QUANT_FINDINGS.md). 창 경계(직전 20행·당일 제외)·추세
비율·any_60d 이진화·**창 부족 null(0 채움 금지)**·멱등을 합성 입력으로 잠근다.
DB·실패널 접근 없음 — 전부 순수 compute_rows/replace_range(scripts/build_event_rolling.py).
"""
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import build_event_rolling as br  # noqa: E402


def _rows(dates_totals):
    """(date_str, total) 목록 → compute_rows 소스 행 튜플(묶음·공시는 0)."""
    return [(d, float(t), 0.0, 0.0, 0.0, 0.0, 0.0) for d, t in dates_totals]


def _only(rows, code, D):
    """compute_rows 결과에서 (code, D) 행 하나만 꺼낸다."""
    got = [r for r in rows if r[0] == code and r[1] == D]
    assert len(got) == 1, "기대한 행 1개가 아님: %s %s -> %s" % (code, D, got)
    return got[0]


def test_01_level_20d_prior20_excludes_today():
    """① 20행 평균 = **직전 20행**(오래된 행 제외) 평균, **당일 행 제외**(trade_date < D)."""
    # 01-01..01-25 총합 1..25 → D=01-26 의 직전 20행 = 총합 6..25 → 평균 15.5
    src = {"000001": _rows([("2025-01-%02d" % i, i) for i in range(1, 26)])}
    rows = br.compute_rows(src, ["2025-01-26"])
    r = _only(rows, "000001", "2025-01-26")
    assert r[2] == pytest.approx(15.5)          # (6+25)/2 — 직전 20행
    # 같은 날짜에 소스 행이 있어도(총합 999) 창에 들어가지 않는다(당일 제외)
    src["000001"].append(("2025-01-26", 999.0, 0.0, 0.0, 0.0, 0.0, 0.0))
    rows2 = br.compute_rows(src, ["2025-01-26"])
    r2 = _only(rows2, "000001", "2025-01-26")
    assert r2[2] == pytest.approx(15.5)


def test_02_trend_5v20():
    """② 추세 = (직전 5행 평균) / (직전 20행 평균 + 1e-9) — 분모에만 1e-9."""
    # 20행: 앞 15행 총합 0, 뒤 5행 총합 10 → level=50/20=2.5, trend=10/(2.5+1e-9)≈4.0
    src = {"000002": _rows([("2025-01-%02d" % i, 0) for i in range(1, 16)]
                           + [("2025-01-%02d" % i, 10) for i in range(16, 21)])}
    rows = br.compute_rows(src, ["2025-01-22"])
    r = _only(rows, "000002", "2025-01-22")
    assert r[2] == pytest.approx(2.5)                                  # 20행 평균
    assert r[3] == pytest.approx(10.0 / (2.5 + 1e-9))                  # 5행/20행 (+1e-9)


def test_03_any_60d_binary():
    """③ any_60d = 직전 60행 중 총합>0 이면 1 — 이진화, 60행 밖의 이벤트는 0."""
    # 61행: 맨 앞 1행만 총합 5, 뒤 60행은 0 → D 에서 직전 60행엔 이벤트 없음 → 0
    src_a = {"000003": _rows([("2025-01-01", 5)] + [("2025-01-%02d" % i, 0)
                                                    for i in range(2, 62)])}
    r_a = _only(br.compute_rows(src_a, ["2025-02-01"]), "000003", "2025-02-01")
    assert r_a[4] == 0
    # 마지막(61번째) 행에 총합 7 → 직전 60행 안에 있음 → 1
    src_b = {"000004": _rows([("2025-01-%02d" % i, 0) for i in range(1, 61)]
                             + [("2025-01-61", 7)])}
    r_b = _only(br.compute_rows(src_b, ["2025-02-01"]), "000004", "2025-02-01")
    assert r_b[4] == 1
    assert r_a[4] in (0, 1) and r_b[4] in (0, 1)   # 이진값


def test_04_window_shortage_null():
    """④ 창 부족(직전 20행 미만) → **null**(0으로 메우지 않음). 직전 0행은 행 미방출."""
    src = {"000005": _rows([("2025-01-01", 1), ("2025-01-03", 2), ("2025-01-05", 3)])}
    rows = br.compute_rows(src, ["2024-12-31", "2025-01-04", "2025-01-06"])
    by_date = {r[1]: r for r in rows}
    assert "2024-12-31" not in by_date          # 직전 행 0개 → 행 자체를 내보내지 않음
    r4 = by_date["2025-01-04"]                  # 직전 1행(<20) → 레벨·추세·묶음·공시 null
    assert r4[2] is None and r4[3] is None
    assert r4[5] is None and r4[6] is None and r4[7] is None and r4[8] is None and r4[9] is None
    assert r4[4] == 1                           # any_60d 는 직전 행 ≥1 이면 계산
    r6 = by_date["2025-01-06"]                  # 직전 3행 — 여전히 <20
    assert r6[2] is None and r6[4] == 1


def test_05_upsert_idempotent():
    """⑤ 멱등 upsert: 같은 입력 2회 → 행수 불변, 범위 밖 행은 삭제되지 않음."""
    rows = [("000006", "2025-01-10", 1.0, None, 1, 0.5, 0.5, 0.5, 0.5, 0.5),
            ("000006", "2025-01-11", 2.0, 0.5, 0, 0.5, 0.5, 0.5, 0.5, 0.5)]
    store = {}
    n1 = br.replace_range(store, rows, "2025-01-01", "2025-01-31")
    n2 = br.replace_range(store, rows, "2025-01-01", "2025-01-31")   # 같은 입력 2회
    assert n1 == n2 == 2
    assert store[("000006", "2025-01-10")] == rows[0][2:]
    store[("000007", "2024-12-01")] = ("old",)                       # 범위 밖 기존 행
    n3 = br.replace_range(store, rows, "2025-01-01", "2025-01-31")
    assert n3 == 3
    assert store[("000007", "2024-12-01")] == ("old",)               # 범위 밖은 그대로

"""R41 — 시장패턴 원장 커버리지 검사. '그날 시장의 21%만 본 값'이 정상값처럼 기록되던 경로.

WHY (실측 2026-11-13): KRX 이름해석 실패 → KIS 폴백(3.5h) 중에 19:40 크론이 돌아
원장에 scored=819(전일 3828)·trade_value 12.25조(전일 22.53조)가 기록됐다. 재실행으로
scored=3805·29.32조로 정정됐다 — 즉 '실행 시각'이 값의 품질을 결정하는데 아무 표시가 없었다.

WHY 확장 (실측 2026-11-16): 종전 검사는 마지막 레코드 하나만 봐서 ① 원장에 아예 없는 세션
(10-12~11-06 20세션 + 11-12 = 21세션 누락) ② scored 는 맞는데 trade_value_sum 만 0 인 행(11-10)을
둘 다 통과시켰다. 아래 테스트는 그 두 침묵을 고정한다.
"""
import json
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import market_pattern_coverage_check as chk  # noqa: E402


def _write(tmp_path, records):
    p = tmp_path / "ledger.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return str(p)


def _db(*rows):
    """(session, 마감봉수, 거래대금 합) 튜플 목록으로 DB 를 대신한다."""
    return list(rows)


@pytest.fixture(autouse=True)
def _fake_db(monkeypatch):
    monkeypatch.setattr(chk, "db_sessions", lambda limit=30: _db(("2026-11-13", 3828, 37.18e12)))


def test_partial_record_is_defect(tmp_path, monkeypatch, capsys):
    p = _write(tmp_path, [{"session": "2026-11-13", "scored": 819, "trade_value_sum": 12.25e12}])
    monkeypatch.setattr(chk, "db_sessions", lambda limit=30: _db(("2026-11-13", 3722, 12.25e12)))
    rc = chk.main(["--ledger", p])
    out = capsys.readouterr().out
    assert rc == 1 and "공백 1건" in out
    assert "ratio=0.220" in out, "실측값(819/3722)을 그대로 고정한다"


def test_trade_value_zero_is_defect_even_when_scored_ok(tmp_path, capsys):
    """실측 11-10: scored 3726 은 정상인데 trade_value_sum 만 0 이었다(종전 검사 침묵)."""
    p = _write(tmp_path, [{"session": "2026-11-13", "scored": 3722, "trade_value_sum": 0.0}])
    rc = chk.main(["--ledger", p])
    out = capsys.readouterr().out
    assert rc == 1 and "거래대금" in out and "공백 1건" in out


def test_trade_value_null_does_not_crash(tmp_path, capsys):
    """원장 구버전 행은 trade_value_sum 키가 없다 — 검사가 traceback 으로 죽으면 판정이 사라진다."""
    p = _write(tmp_path, [{"session": "2026-11-13", "scored": 3828}])
    rc = chk.main(["--ledger", p])
    out = capsys.readouterr().out
    assert rc == 1 and "tv=null" in out and "공백 1건" in out


def test_missing_session_is_defect(tmp_path, monkeypatch, capsys):
    """실측 10-12~11-06: 원장에 아예 없는 세션은 scored 비교 대상조차 없었다(종전 침묵)."""
    p = _write(tmp_path, [{"session": "2026-11-13", "scored": 3722, "trade_value_sum": 37.18e12}])
    monkeypatch.setattr(chk, "db_sessions",
                        lambda limit=30: _db(("2026-11-12", 3723, 21.28e12),
                                             ("2026-11-13", 3722, 37.18e12)))
    rc = chk.main(["--ledger", p])
    out = capsys.readouterr().out
    assert rc == 1 and "누락" in out and "2026-11-12" in out and "공백 1건" in out


def test_complete_record_passes(tmp_path, monkeypatch, capsys):
    p = _write(tmp_path, [{"session": "2026-11-13", "scored": 3805, "trade_value_sum": 29.32e12}])
    monkeypatch.setattr(chk, "db_sessions", lambda limit=30: _db(("2026-11-13", 3828, 29.32e12)))
    rc = chk.main(["--ledger", p])
    assert rc == 0 and "충족" in capsys.readouterr().out


def test_tolerance_band_is_not_a_defect(tmp_path):
    # 90% 이상이면 정상(상장폐지·거래정지·신규상장 차이)
    p = _write(tmp_path, [{"session": "2026-11-13", "scored": 3450, "trade_value_sum": 37.18e12}])
    assert chk.main(["--ledger", p]) == 0


def test_just_below_tolerance_is_defect(tmp_path):
    p = _write(tmp_path, [{"session": "2026-11-13", "scored": 3400, "trade_value_sum": 37.18e12}])
    assert chk.main(["--ledger", p]) == 1


def test_stale_ledger_below_db_is_not_a_defect(tmp_path, monkeypatch, capsys):
    # 원장 < DB 는 DB 쪽 NULL 보충 등으로 DB 가 더 완전해진 경우다 — 손실이 아니다.
    p = _write(tmp_path, [{"session": "2026-11-13", "scored": 3828, "trade_value_sum": 28.99e12}])
    monkeypatch.setattr(chk, "db_sessions", lambda limit=30: _db(("2026-11-13", 3828, 35.31e12)))
    rc = chk.main(["--ledger", p])
    out = capsys.readouterr().out
    assert rc == 0 and "원장 낡음" in out


def test_db_lower_than_ledger_is_data_loss_defect(tmp_path, monkeypatch, capsys):
    p = _write(tmp_path, [{"session": "2026-11-13", "scored": 3828, "trade_value_sum": 37.18e12}])
    monkeypatch.setattr(chk, "db_sessions", lambda limit=30: _db(("2026-11-13", 3828, 30.0e12)))
    rc = chk.main(["--ledger", p])
    out = capsys.readouterr().out
    assert rc == 1 and "거래대금" in out and "손실" in out


def test_last_record_wins_when_duplicated(tmp_path):
    # 원장은 세션별 덮어쓰기 — 뒤에 나온 줄이 이긴다
    p = _write(tmp_path, [{"session": "2026-11-13", "scored": 819, "trade_value_sum": 0},
                          {"session": "2026-11-13", "scored": 3805, "trade_value_sum": 37.18e12}])
    assert chk.main(["--ledger", p]) == 0


def test_missing_ledger_is_unjudgeable(tmp_path):
    assert chk.main(["--ledger", str(tmp_path / "nope.jsonl")]) == 2


def test_empty_ledger_is_unjudgeable(tmp_path):
    p = tmp_path / "empty.jsonl"
    p.write_text("", encoding="utf-8")
    assert chk.main(["--ledger", str(p)]) == 2


def test_db_failure_is_unjudgeable_not_pass(tmp_path, monkeypatch):
    p = _write(tmp_path, [{"session": "2026-11-13", "scored": 3805, "trade_value_sum": 37.18e12}])

    def boom(limit=30):
        raise RuntimeError("db down")

    monkeypatch.setattr(chk, "db_sessions", boom)
    assert chk.main(["--ledger", p]) == 2, "판정 불가는 0(통과)이 아니다"


def test_empty_db_window_is_unjudgeable(tmp_path, monkeypatch):
    p = _write(tmp_path, [{"session": "2026-11-13", "scored": 3805, "trade_value_sum": 37.18e12}])
    monkeypatch.setattr(chk, "db_sessions", lambda limit=30: [])
    assert chk.main(["--ledger", p]) == 2


def test_last_number_in_stdout_is_defect_count(tmp_path, monkeypatch, capsys):
    """구동기 eval_check 는 stdout 의 **마지막 수치**를 check_value 로 읽는다."""
    import re
    p = _write(tmp_path, [{"session": "2026-11-12", "scored": 900, "trade_value_sum": 0}])
    monkeypatch.setattr(chk, "db_sessions",
                        lambda limit=30: _db(("2026-11-12", 3723, 21.28e12),
                                             ("2026-11-13", 3722, 37.18e12)))
    chk.main(["--ledger", p])
    nums = re.findall(r"[-+]?\d+(?:\.\d+)?", capsys.readouterr().out)
    # 결함 3 = 11-12 부분기록 + 11-12 거래대금 + 11-13 누락
    assert float(nums[-1]) == 3


def test_db_null_trading_value_is_defect(tmp_path, monkeypatch, capsys):
    """실측 2026-11-16: root crontab 소실로 KIS 일봉이 안 돌아 yfinance 스윕만 적재 →
    trading_value 3,724/3,725 NULL(합계 0.001조). 원장↔DB 를 비교하면 양쪽 다 ~0 이라
    비율 1.0 = 거짓 통과했다(자기참조). DB NULL 비율로 판정해야 잡힌다."""
    p = _write(tmp_path, [{"session": "2026-11-16", "scored": 3725, "trade_value_sum": 1.0e9}])
    monkeypatch.setattr(chk, "db_sessions",
                        lambda limit=30: [("2026-11-16", 3725, 1.0e9, 0.9997)])
    rc = chk.main(["--ledger", p])
    out = capsys.readouterr().out
    assert rc == 1 and "원천결손" in out and "공백 1건" in out
    assert "krx_daily.py --from 20261116 --to 20261116" in out, "수리 경로를 보고에 실어야 한다"
    assert "거래대금" not in out, "원천결손이면 거래대금 비교는 자기참조라 이중계상하지 않는다"


def test_baseline_null_ratio_is_not_defect(tmp_path, monkeypatch):
    """정상일의 trading_value NULL 은 22~30% 다 — 문턱 0.5 아래는 결함이 아니다."""
    p = _write(tmp_path, [{"session": "2026-11-17", "scored": 3720, "trade_value_sum": 35.3e12}])
    monkeypatch.setattr(chk, "db_sessions",
                        lambda limit=30: [("2026-11-17", 3720, 35.3e12, 0.30)])
    assert chk.main(["--ledger", p]) == 0


def test_three_tuple_sessions_still_supported():
    """db_sessions 3-튜플(구 형태)도 판정이 죽지 않아야 한다 — 계약 확장은 후방호환."""
    records = {"2026-11-17": {"session": "2026-11-17", "scored": 3720, "trade_value_sum": 35.3e12}}
    defects, _ = chk.judge(records, [("2026-11-17", 3720, 35.3e12)])
    assert defects == []


# --- 원천 담당 시각 유예 (실측 2026-11-20 18:47) --------------------------------
# 11-19 봉은 KIS 경로(전일 22:5x)만 적재돼 trading_value 3,722/3,722 NULL 이었지만
# KRX 일봉(평일 18:55)이 8분 뒤 11-19 를 담당할 예정이었다. 담당 시각 전의 NULL 을 결함으로
# 세면 정상 상태에서 매 틱 위반이 뜨는 '고쳐도 못 통과하는 check' 가 된다.

def test_source_pending_before_due_and_after_due():
    from datetime import datetime
    assert chk.source_pending("2026-11-19", datetime(2026, 11, 20, 18, 47)) is True
    assert chk.source_pending("2026-11-19", datetime(2026, 11, 20, 19, 31)) is False


def test_friday_session_grace_runs_to_monday():
    """금요일 세션은 주말을 건너 월요일 18:55 KRX 가 담당한다 — 토·일 틱이 위반을 만들면 안 된다."""
    from datetime import datetime
    assert chk.source_pending("2026-11-20", datetime(2026, 11, 21, 12, 0)) is True
    assert chk.source_due_at("2026-11-20").strftime("%Y-%m-%d %H:%M") == "2026-11-23 19:30"
    assert chk.source_pending("2026-11-20", datetime(2026, 11, 23, 19, 31)) is False


def test_pending_null_is_grace_line_not_defect():
    from datetime import datetime
    records = {"2026-11-19": {"session": "2026-11-19", "scored": 3722, "trade_value_sum": None}}
    defects, lines = chk.judge(records, [("2026-11-19", 3722, 0.0, 1.0)],
                               now=datetime(2026, 11, 20, 18, 47))
    assert defects == [], "원천 담당 전 NULL 은 결함이 아니다"
    assert any(l.startswith("  · 유예") for l in lines), "유예도 보고에 남긴다(은폐 금지)"


def test_pending_grace_expires_into_defect():
    """담당 시각이 지나도 NULL 이면 결함이다 — 유예가 실명을 가리면 안 된다."""
    from datetime import datetime
    records = {"2026-11-19": {"session": "2026-11-19", "scored": 3722, "trade_value_sum": None}}
    defects, _ = chk.judge(records, [("2026-11-19", 3722, 0.0, 1.0)],
                           now=datetime(2026, 11, 20, 20, 0))
    assert [d[1] for d in defects] == ["원천결손"]


# --- 원장 기록 담당 시각 유예 (실측 2026-11-21 04:5x) ---------------------------
# `market_pattern_ledger.py --today`(평일 19:40)는 그 시점 DB 의 **최신** 세션을 기록하므로
# 원장은 구조적으로 한 세션 뒤진다(11-20 세션은 11-23 19:40 에 기록). 종전 검사는 이 지연을
# '누락'으로 세어 주말·새벽 틱마다 상시 1건 미달을 기록했다(R41 11-20 18:47 '1 <= 0 → 미달').

def test_ledger_pending_window_ends_next_weekday_1950():
    from datetime import datetime
    assert chk.ledger_due_at("2026-11-20").strftime("%Y-%m-%d %H:%M") == "2026-11-23 19:50"
    assert chk.ledger_pending("2026-11-20", datetime(2026, 11, 21, 4, 47)) is True
    assert chk.ledger_pending("2026-11-20", datetime(2026, 11, 23, 19, 55)) is False


def test_missing_latest_session_in_window_is_grace_not_defect():
    from datetime import datetime
    defects, lines = chk.judge({}, [("2026-11-20", 3722, 0.0, 1.0)],
                               now=datetime(2026, 11, 21, 4, 47))
    assert defects == [], "원장 기록 창 전의 누락은 결번이 아니다"
    assert any(l.startswith("  · 유예(원장 기록 전)") for l in lines)


def test_missing_session_after_window_is_defect():
    """창이 지나도 원장에 없으면 결함이다 — 유예가 실명을 가리면 안 된다."""
    from datetime import datetime
    defects, _ = chk.judge({}, [("2026-11-20", 3722, 0.0, 1.0)],
                           now=datetime(2026, 11, 23, 20, 0))
    assert [d[1] for d in defects] == ["누락"]

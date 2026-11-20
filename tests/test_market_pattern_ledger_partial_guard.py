"""[R41] 부분 스냅샷이 완전한 세션 기록을 덮어쓰지 못하게 하는 가드 (2026-11-20 실측).

실측 사고: 리서처 틱 18:47 이 `--today` 를 돌렸고 일봉 적재는 18:55 에 끝난다.
`--today` 는 'DB 최신 날짜'를 쓰므로 18:47 에는 전일(11-19)이 yfinance 스윕분만 든 상태로 잡혀
11-19 기록이 scored 3832→3722 · trade_value_sum 17.99조→null · breadth 64.7→70.9 로 덮였다.
19:40 원장 크론이 재기록해 복구했지만 53분 동안 그 값이 '그날 시장'으로 읽혔다.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import market_pattern_ledger as mpl  # noqa: E402


FULL = {"session": "2026-11-19", "scored": 3832, "trade_value_sum": 1.7988e13,
        "breadth_pct": 64.7, "adr": 2.24, "chg_median_pct": 0.561}
PARTIAL = {"session": "2026-11-19", "scored": 3722, "trade_value_sum": None,
           "breadth_pct": 70.9, "adr": 2.72, "chg_median_pct": 0.843}


def test_no_prev_is_not_partial():
    assert mpl._looks_partial(FULL, None) == (False, "")


def test_fewer_bars_is_partial():
    partial, why = mpl._looks_partial(PARTIAL, FULL)
    assert partial is True
    assert "scored" in why


def test_missing_trade_value_is_partial():
    new = dict(FULL, scored=FULL["scored"], trade_value_sum=None)
    partial, why = mpl._looks_partial(new, FULL)
    assert partial is True
    assert "trade_value" in why


def test_growing_record_is_not_partial():
    # 부분 → 완전(정상 진행)은 통과해야 멱등 재실행 계약이 유지된다.
    assert mpl._looks_partial(FULL, PARTIAL)[0] is False


def test_identical_record_is_not_partial():
    assert mpl._looks_partial(dict(FULL), FULL)[0] is False


def _run_main(tmp_path, monkeypatch, new_metrics, ledger_rows, argv):
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in ledger_rows) + "\n",
                      encoding="utf-8")
    monkeypatch.setattr(mpl, "LEDGER", str(ledger))
    monkeypatch.setattr(mpl, "_connect", lambda: object())
    monkeypatch.setattr(mpl, "detect_columns", lambda conn: {"code": "stock_code", "date": "trade_date",
                                                             "close": "close", "volume": "volume",
                                                             "value": "trade_value"})
    monkeypatch.setattr(mpl, "session_metrics", lambda conn, cols, d: dict(new_metrics))
    # 날짜 목록 조회용 커서만 흉내낸다.
    class _Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, *a, **k):
            return None

        def fetchall(self):
            return [("2026-11-19",)]

    class _Conn:
        def cursor(self):
            return _Cur()

    monkeypatch.setattr(mpl, "_connect", lambda: _Conn())
    rc = mpl.main(argv)
    rows = {json.loads(l)["session"]: json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines() if l}
    return rc, rows


def test_main_keeps_full_record_when_partial_snapshot_arrives(tmp_path, monkeypatch, capsys):
    rc, rows = _run_main(tmp_path, monkeypatch, PARTIAL, [FULL], ["--today"])
    assert rc == 0
    assert rows["2026-11-19"]["scored"] == 3832
    assert rows["2026-11-19"]["trade_value_sum"] == pytest.approx(1.7988e13)
    assert rows["2026-11-19"]["breadth_pct"] == 64.7
    err = capsys.readouterr().err
    assert "부분 스냅샷" in err


def test_main_force_overwrites_partial(tmp_path, monkeypatch):
    rc, rows = _run_main(tmp_path, monkeypatch, PARTIAL, [FULL], ["--today", "--force"])
    assert rc == 0
    assert rows["2026-11-19"]["scored"] == 3722


def test_main_records_when_no_prev(tmp_path, monkeypatch):
    rc, rows = _run_main(tmp_path, monkeypatch, PARTIAL, [], ["--today"])
    assert rc == 0
    assert rows["2026-11-19"]["scored"] == 3722

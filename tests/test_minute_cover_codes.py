"""tests/test_minute_cover_codes.py — 분봉 백필 대상 코드 수집(3 tests).

배경: naver_minute_backfill.py 기본 대상은 capital_sim 신호 종목이라 신호 없는 날 0종목이 된다
(2026-11-18 실측 codes=0). 당일 실제 판단·체결 종목(피드 후보 + 저널)을 넘기기 위한 수집기.
"""
import json
import os
import sqlite3
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import minute_cover_codes as mcc  # noqa: E402


def test_feed_codes_dedupe_and_skip_bad(tmp_path):
    feed = tmp_path / "feed.json"
    feed.write_text(json.dumps({
        "candidates": {
            "swing": {"items": [{"stock_code": "003780"}, {"stock_code": "186230"}]},
            "daytrading": {"items": [{"stock_code": "003780"}, {"stock_code": ""}]},
        }
    }), encoding="utf-8")
    # 수집 단계는 중복을 그대로 두고(정렬·중복제거는 main), 빈 코드만 버린다.
    assert sorted(mcc._feed_codes(str(feed))) == ["003780", "003780", "186230"]


def test_journal_codes_reads_only_target_date(tmp_path):
    db = tmp_path / "j.sqlite3"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE trades (stock_code TEXT, ts TEXT, exit_ts TEXT)")
    conn.executemany("INSERT INTO trades VALUES (?,?,?)", [
        ("021320", "2026-11-18T12:35:16", "2026-11-18T13:05:30"),
        ("143240", "2026-11-18T13:36:11", "2026-11-18T14:10:49"),
        ("033100", "2026-11-17T09:00:00", "2026-11-17T09:20:00"),
    ])
    conn.commit(); conn.close()
    assert sorted(mcc._journal_codes(str(db), "2026-11-18")) == ["021320", "143240"]


def test_main_writes_sorted_unique_codes(tmp_path):
    feed = tmp_path / "f.json"
    feed.write_text(json.dumps({"candidates": {"a": {"items": [
        {"stock_code": "000002"}, {"stock_code": "000001"}, {"stock_code": "000002"}]}}}),
        encoding="utf-8")
    out = tmp_path / "codes.txt"
    rc = mcc.main(["--date", "2026-11-18", "--out", str(out), "--feed", str(feed),
                   "--daytrading", str(tmp_path / "none.json"),
                   "--journal", str(tmp_path / "none.sqlite3")])
    assert rc == 0
    assert out.read_text(encoding="utf-8").split() == ["000001", "000002"]

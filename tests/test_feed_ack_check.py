"""고객 피드 ack 검사 — 파일 픽스처로 ack 있음/없음/오늘 이전 ack 을 고정한다(DB 불필요)."""
import json
import os
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import feed_ack_check as chk  # noqa: E402

EXPECTED = "2026-11-24T20:30:02+09:00"
KST = timezone(timedelta(hours=9))


@pytest.fixture()
def env(tmp_path):
    ack_dir = tmp_path / "ack"
    ack_dir.mkdir()
    return {
        "ack": str(ack_dir),
        "feed": str(tmp_path / "feed.json"),
        "customers": str(tmp_path / "customers.json"),
        "report": str(tmp_path / "out" / "feed_ack_latest.json"),
        "notify": str(tmp_path / "notify.json"),
    }


def _args(env):
    return ["--ack-dir", env["ack"], "--feed-path", env["feed"], "--customers-config", env["customers"],
            "--report", env["report"], "--notify-config", env["notify"]]


def _write_feed(env, generated_at=EXPECTED):
    with open(env["feed"], "w", encoding="utf-8") as fh:
        json.dump({"generated_at": generated_at, "feed_schema_version": 1}, fh)


def _write_customers(env, ids):
    with open(env["customers"], "w", encoding="utf-8") as fh:
        json.dump({"customers": [{"id": cid} for cid in ids]}, fh)


def _append_ack(env, day, rows):
    path = os.path.join(env["ack"], day + ".jsonl")
    with open(path, "a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _row(cid, generated_at, received_at):
    return {"customer_id": cid, "feed_generated_at": generated_at, "version": "1.0.0",
            "received_at": received_at}


def _read_report(env):
    with open(env["report"], encoding="utf-8") as fh:
        return json.load(fh)


def test_all_acked_rc0(env):
    _write_feed(env)
    _write_customers(env, ["pilot1", "pilot2"])
    _append_ack(env, "20261124", [_row("pilot1", EXPECTED, "2026-11-24T20:57:49+09:00"),
                                  _row("pilot2", EXPECTED, "2026-11-24T20:58:49+09:00")])
    rc = chk.main(_args(env))
    report = _read_report(env)
    assert rc == 0
    assert report["generated_at"] == EXPECTED
    assert report["feed_expected_at"].endswith("T08:55:00+09:00")
    assert report["missing"] == []
    assert [c["id"] for c in report["customers"]] == ["pilot1", "pilot2"]
    assert all(c["acked"] and c["last_ack_at"] and c["age_min"] is not None for c in report["customers"])


def test_missing_customer_rc1(env):
    _write_feed(env)
    _write_customers(env, ["pilot1", "pilot2"])
    _append_ack(env, "20261124", [_row("pilot1", EXPECTED, "2026-11-24T20:57:49+09:00")])
    rc = chk.main(_args(env))
    report = _read_report(env)
    assert rc == 1
    assert report["missing"] == ["pilot2"]
    by_id = {c["id"]: c for c in report["customers"]}
    assert by_id["pilot1"]["acked"] is True
    assert by_id["pilot2"]["acked"] is False
    assert by_id["pilot2"]["last_ack_at"] is None and by_id["pilot2"]["age_min"] is None


def test_stale_ack_not_counted_rc1(env):
    _write_feed(env)
    _write_customers(env, ["pilot1"])
    _append_ack(env, "20261123", [_row("pilot1", "2026-11-23T20:30:02+09:00",
                                       "2026-11-23T20:57:49+09:00")])
    rc = chk.main(_args(env))
    report = _read_report(env)
    assert rc == 1
    assert report["missing"] == ["pilot1"]
    c = report["customers"][0]
    assert c["acked"] is False
    assert c["last_ack_at"] == "2026-11-23T20:57:49+09:00"
    assert c["age_min"] > 0


def test_stale_plus_today_ack_counts_today(env):
    _write_feed(env)
    _write_customers(env, ["pilot1"])
    _append_ack(env, "20261123", [_row("pilot1", "2026-11-23T20:30:02+09:00",
                                       "2026-11-23T20:57:49+09:00")])
    _append_ack(env, "20261124", [_row("pilot1", EXPECTED, "2026-11-24T20:57:49+09:00")])
    rc = chk.main(_args(env))
    report = _read_report(env)
    assert rc == 0
    c = report["customers"][0]
    assert c["acked"] is True and c["last_ack_at"] == "2026-11-24T20:57:49+09:00"


def test_age_min_computed_from_last_ack():
    now = datetime(2026, 11, 24, 8, 55, 0, tzinfo=KST)
    rows = [_row("pilot1", "2026-11-23T20:30:02+09:00", "2026-11-23T20:00:00+09:00")]
    out = chk.judge(rows, ["pilot1"], EXPECTED, now=now)
    c = out["customers"][0]
    assert out["missing"] == ["pilot1"]
    assert c["age_min"] == 775.0


def test_no_customers_config_rc0(env):
    _write_feed(env)
    rc = chk.main(_args(env))
    report = _read_report(env)
    assert rc == 0
    assert report["customers"] == [] and report["missing"] == []


def test_no_feed_rc2(env):
    _write_customers(env, ["pilot1"])
    rc = chk.main(_args(env))
    report = _read_report(env)
    assert rc == 2
    assert report["generated_at"] is None and report["customers"] == []


def test_notify_skipped_without_config(env, monkeypatch):
    _write_feed(env)
    _write_customers(env, ["pilot1"])

    def _fail(cmd, **kw):
        raise AssertionError("config 없이 notify 호출됨")

    monkeypatch.setattr(chk.subprocess, "run", _fail)
    rc = chk.main(_args(env))
    assert rc == 1


def test_notify_not_called_when_all_acked(env, monkeypatch):
    _write_feed(env)
    _write_customers(env, ["pilot1"])
    _append_ack(env, "20261124", [_row("pilot1", EXPECTED, "2026-11-24T20:57:49+09:00")])
    with open(env["notify"], "w", encoding="utf-8") as fh:
        json.dump({"mode": "webhook", "webhook_url": "https://example.invalid/hook"}, fh)

    def _fail(cmd, **kw):
        raise AssertionError("전원 확인인데 notify 호출됨")

    monkeypatch.setattr(chk.subprocess, "run", _fail)
    rc = chk.main(_args(env))
    assert rc == 0


def test_notify_called_on_missing(env, monkeypatch):
    _write_feed(env)
    _write_customers(env, ["pilot1", "pilot2"])
    _append_ack(env, "20261124", [_row("pilot1", EXPECTED, "2026-11-24T20:57:49+09:00")])
    with open(env["notify"], "w", encoding="utf-8") as fh:
        json.dump({"mode": "webhook", "webhook_url": "https://example.invalid/hook"}, fh)
    calls = []

    def _fake(cmd, **kw):
        calls.append(cmd)
        return types.SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(chk.subprocess, "run", _fake)
    rc = chk.main(_args(env))
    assert rc == 1
    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[1].endswith(os.path.join("services", "discord_notify", "notify.py"))
    assert cmd[4] == "test"
    assert "pilot2" in cmd[6]


def test_notify_failure_keeps_rc(env, monkeypatch):
    _write_feed(env)
    _write_customers(env, ["pilot1"])
    with open(env["notify"], "w", encoding="utf-8") as fh:
        json.dump({"mode": "webhook", "webhook_url": "https://example.invalid/hook"}, fh)
    monkeypatch.setattr(chk.subprocess, "run",
                        lambda cmd, **kw: types.SimpleNamespace(returncode=1, stderr="boom"))
    rc = chk.main(_args(env))
    assert rc == 1

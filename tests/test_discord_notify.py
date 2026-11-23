"""Discord 알림 회귀 — 메시지 내용·수신 상태 판정·실제 HTTP 전송(모의 웹훅).

모의 웹훅은 로컬 HTTP 서버라 전송 코드 경로(post)를 그대로 통과한다.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("notify", REPO / "services/discord_notify/notify.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class _Hook(BaseHTTPRequestHandler):
    received: list = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8")
        _Hook.received.append(json.loads(body))
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format, *args):
        return


@pytest.fixture()
def hook():
    _Hook.received = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Hook)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield "http://127.0.0.1:{0}/hook".format(httpd.server_address[1])
    httpd.shutdown()
    httpd.server_close()


def _feed_repo(tmp_path, published_today=True, state="risk_off"):
    mod = _load()
    repo = tmp_path / "feed"
    (repo / "deploy").mkdir(parents=True)
    stamp = mod.now_kst()
    if not published_today:
        stamp = stamp - mod.timedelta(days=1)
    (repo / "deploy" / "meta.json").write_text(json.dumps({
        "published_at": stamp.isoformat(timespec="seconds"),
        "generated_at": stamp.isoformat(timespec="seconds"),
        "candidates": {"close": 20, "swing": 20, "daytrading": 20},
    }), encoding="utf-8")
    (repo / "deploy" / "latest.json").write_text(json.dumps({
        "regime": {"state": state, "kosdaq_level": 632.54, "kospi_level": 4684.97,
                   "market_up": False},
    }), encoding="utf-8")
    return str(repo)


def test_morning_message_lists_steps_and_counts(tmp_path):
    mod = _load()
    feed = mod.feed_summary(_feed_repo(tmp_path))
    body = mod.morning_message(feed, {})
    assert "후보 60건" in body
    assert "크레온 PLUS 에 로그인" in body
    assert "08:45" in body
    assert "risk_off" in body
    assert "3단계" in body


def test_morning_message_warns_when_feed_missing(tmp_path):
    mod = _load()
    feed = mod.feed_summary(_feed_repo(tmp_path, published_today=False))
    body = mod.morning_message(feed, {})
    assert "피드 미발행 경고" in body
    assert "PC 를 켜지 마시고" in body


def test_ack_state_splits_done_and_missing(tmp_path):
    mod = _load()
    root = tmp_path / "relay"
    (root / "ack").mkdir(parents=True)
    (root / "telemetry" / "pilot1").mkdir(parents=True)
    day = mod.now_kst().strftime("%Y%m%d")
    (root / "ack" / (day + ".jsonl")).write_text(
        json.dumps({"customer_id": "pilot1"}) + "\n" + json.dumps({"customer_id": "pilot2"}) + "\n",
        encoding="utf-8")
    state = mod.ack_state(str(root), ["pilot1", "pilot2", "pilot3"])
    assert state["acked"] == ["pilot1", "pilot2"]
    assert state["missing"] == ["pilot3"]


def test_webhook_post_reaches_endpoint(hook, tmp_path):
    mod = _load()
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "mode": "webhook", "webhook_url": hook,
        "feed_repo": _feed_repo(tmp_path), "relay_root": str(tmp_path / "relay"),
        "customers": ["pilot1", "pilot2"],
    }), encoding="utf-8")
    assert mod.main(["--config", str(cfg_path), "morning"]) == 0
    assert _Hook.received and "장전 체크" in _Hook.received[0]["content"]
    assert _Hook.received[0]["allowed_mentions"] == {"parse": []}


def test_missing_config_returns_2(tmp_path):
    mod = _load()
    assert mod.main(["--config", str(tmp_path / "nope.json"), "test"]) == 2


def test_bot_mode_without_token_returns_1(tmp_path):
    mod = _load()
    cfg_path = tmp_path / "c.json"
    cfg_path.write_text(json.dumps({"mode": "bot", "bot_token": "", "channel_id": "1",
                                    "feed_repo": _feed_repo(tmp_path)}), encoding="utf-8")
    assert mod.main(["--config", str(cfg_path), "test"]) in (1, 2)

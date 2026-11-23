"""피드/업데이트 릴레이 회귀 — 서버·번들·클라이언트 왕복.

실제 HTTP 서버를 임시 포트로 띄워 feed_pull/updater 를 그대로 호출한다(모의 없이).
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pathlib
import threading
import urllib.error
import urllib.request

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
SRV = REPO / "services" / "feed_relay"


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def relay(tmp_path):
    server_mod = _load("relay_server", "services/feed_relay/server.py")
    root = tmp_path / "relay"
    (root / "feed").mkdir(parents=True)
    token = "pilot-token-123"
    (root / "customers.json").write_text(json.dumps({"customers": [
        {"id": "pilot1", "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
         "channel": "stable", "limits": {"max_daily_trades": 5}}
    ]}), encoding="utf-8")
    (root / "feed" / "latest.json").write_text(json.dumps({
        "generated_at": "2026-11-24T08:30:00+09:00", "feed_schema_version": 1,
        "regime": {"state": "risk_off", "kosdaq_level": 645.0},
        "candidates": {"daytrading": {"items": [{"stock_code": "005930"}]}},
    }), encoding="utf-8")
    httpd = server_mod.build_server(str(root), "127.0.0.1", 0)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield {"root": str(root), "base": "http://127.0.0.1:{0}".format(port), "token": token}
    httpd.shutdown()
    httpd.server_close()


def _get(url: str, token: str = ""):
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token} if token else {})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, resp.read()


def test_health(relay):
    status, body = _get(relay["base"] + "/health")
    assert status == 200 and json.loads(body)["ok"] is True


def test_feed_requires_token(relay):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(relay["base"] + "/v1/feed?customer_id=pilot1")
    assert exc.value.code == 401


def test_feed_injects_customer_limits(relay):
    status, body = _get(relay["base"] + "/v1/feed?customer_id=pilot1", relay["token"])
    payload = json.loads(body)
    assert status == 200
    assert payload["customer_id"] == "pilot1"
    assert payload["limits"]["max_daily_trades"] == 5
    assert payload["regime"]["state"] == "risk_off"


def test_wrong_customer_token_rejected(relay):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(relay["base"] + "/v1/feed?customer_id=other", relay["token"])
    assert exc.value.code == 401


def test_release_manifest_signed_and_served(relay, tmp_path):
    build = _load("relay_build", "services/feed_relay/build_release.py")
    app = tmp_path / "app"
    (app / "trader_core").mkdir(parents=True)
    (app / "trader_core" / "engine.py").write_text("VALUE = 1\n", encoding="utf-8")
    (app / "run_market_loop.py").write_text("print('ok')\n", encoding="utf-8")
    out = build.build(str(app), relay["root"], "1.0.0", "stable",
                      str(tmp_path / "release_key.txt"))
    assert out["files"] == 2
    key = (tmp_path / "release_key.txt").read_text().strip()

    status, body = _get(relay["base"] + "/v1/manifest?channel=stable")
    manifest = json.loads(body)
    assert status == 200 and manifest["version"] == "1.0.0"
    assert build.sign({k: v for k, v in manifest.items() if k != "signature"}, key) == manifest["signature"]

    status, pkg = _get(relay["base"] + "/v1/pkg/1.0.0")
    assert status == 200 and hashlib.sha256(pkg).hexdigest() == manifest["bundle_sha256"]


def test_client_pull_writes_feed_and_acks(relay, tmp_path):
    pull = _load("relay_pull", "services/feed_relay/client/feed_pull.py")
    cfg = {"base_url": relay["base"], "customer_id": "pilot1", "token": relay["token"],
           "feed_out": str(tmp_path / "data" / "feed" / "screener_latest.json"),
           "feed_schema_version": 1}
    result = pull.pull(cfg)
    assert result["ok"] is True
    saved = json.loads(pathlib.Path(cfg["feed_out"]).read_text(encoding="utf-8"))
    assert saved["customer_id"] == "pilot1"
    ack_files = list((pathlib.Path(relay["root"]) / "ack").glob("*.jsonl"))
    assert ack_files and "pilot1" in ack_files[0].read_text(encoding="utf-8")


def test_client_update_applies_and_is_idempotent(relay, tmp_path):
    build = _load("relay_build", "services/feed_relay/build_release.py")
    updater = _load("relay_updater", "services/feed_relay/client/updater.py")
    app_src = tmp_path / "src"
    (app_src / "trader_core").mkdir(parents=True)
    (app_src / "trader_core" / "engine.py").write_text("VALUE = 2\n", encoding="utf-8")
    build.build(str(app_src), relay["root"], "1.0.0", "stable", str(tmp_path / "key.txt"))
    key = (tmp_path / "key.txt").read_text().strip()

    app_dst = tmp_path / "customer" / "app"
    cfg = {"base_url": relay["base"], "channel": "stable", "release_key": key,
           "app_dir": str(app_dst), "customer_id": "pilot1", "token": relay["token"]}
    first = updater.update(cfg)
    assert first["changed"] is True and first["version"] == "1.0.0"
    assert (app_dst / "trader_core" / "engine.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    second = updater.update(cfg)
    assert second["changed"] is False


def test_update_rejects_bad_signature(relay, tmp_path):
    build = _load("relay_build", "services/feed_relay/build_release.py")
    updater = _load("relay_updater", "services/feed_relay/client/updater.py")
    app_src = tmp_path / "src"
    app_src.mkdir()
    (app_src / "run_market_loop.py").write_text("x\n", encoding="utf-8")
    build.build(str(app_src), relay["root"], "1.0.0", "stable", str(tmp_path / "key.txt"))
    cfg = {"base_url": relay["base"], "channel": "stable", "release_key": "wrong-key",
           "app_dir": str(tmp_path / "app"), "customer_id": "pilot1", "token": relay["token"]}
    with pytest.raises(SystemExit):
        updater.update(cfg)
    assert not (tmp_path / "app").exists()

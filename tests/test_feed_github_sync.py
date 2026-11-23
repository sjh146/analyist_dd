"""GitHub 배포 저장소 경로 회귀 — 게시 스크립트 + 고객 sync.

실제 git 저장소(bare 원격 + 작업 사본)를 임시로 만들어 subprocess 없이 함수를 그대로 호출한다.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _git(cwd, *args):
    out = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return out.stdout


@pytest.fixture()
def feed_repo(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(remote), str(work)], check=True)
    _git(work, "checkout", "-q", "-b", "main")
    _git(work, "config", "user.name", "t")
    _git(work, "config", "user.email", "t@t")
    (work / "deploy").mkdir()
    (work / "README.md").write_text("feed\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "init")
    _git(work, "push", "-q", "-u", "origin", "main")
    return {"remote": remote, "url": "file://" + str(remote), "work": work}


def test_publish_then_sync(tmp_path, feed_repo):
    publish = _load("pub_feed", "scripts/publish_feed_github.py")
    sync = _load("sync_feed", "services/feed_relay/client/sync_feed.py")
    src = tmp_path / "screener_latest.json"
    src.write_text(json.dumps({
        "generated_at": publish.now_kst().isoformat(timespec="seconds"),
        "candidates": {"daytrading": {"items": [{"stock_code": "005930"}]}},
        "regime": {"state": "risk_on"},
    }), encoding="utf-8")
    feed, meta = publish.prepare_payload(str(src), 18.0, False)
    result = publish.publish(str(feed_repo["work"]), feed, meta, "origin", "main", False, "feed: test")
    assert result["pushed"] is True

    cfg = {"repo_url": feed_repo["url"], "repo_dir": str(tmp_path / "customer" / "feed-repo"),
           "feed_out": str(tmp_path / "customer" / "data" / "feed" / "screener_latest.json"),
           "branch": "main", "feed_schema_version": 1, "max_age_hours": 18.0}
    cfg_path = tmp_path / "customer" / "config.json"
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")

    assert sync.main(["--config", str(cfg_path)]) == 0
    saved = json.loads(pathlib.Path(cfg["feed_out"]).read_text(encoding="utf-8"))
    assert saved["candidates"]["daytrading"]["items"][0]["stock_code"] == "005930"
    assert (pathlib.Path(cfg["feed_out"]).parent / "sync_state.json").is_file()

    assert sync.main(["--config", str(cfg_path)]) == 0


def test_sync_rejects_tampered_feed(tmp_path, feed_repo):
    publish = _load("pub_feed2", "scripts/publish_feed_github.py")
    sync = _load("sync_feed2", "services/feed_relay/client/sync_feed.py")
    src = tmp_path / "f.json"
    src.write_text(json.dumps({"generated_at": publish.now_kst().isoformat(timespec="seconds"),
                               "candidates": {"close": {"items": [{"stock_code": "000660"}]}}}),
                   encoding="utf-8")
    feed, meta = publish.prepare_payload(str(src), 18.0, False)
    publish.publish(str(feed_repo["work"]), feed, meta, "origin", "main", False, "feed: t2")

    latest = feed_repo["work"] / "deploy" / "latest.json"
    payload = json.loads(latest.read_text(encoding="utf-8"))
    payload["candidates"]["close"]["items"][0]["stock_code"] = "999999"
    latest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    _git(feed_repo["work"], "commit", "-qam", "tamper")
    _git(feed_repo["work"], "push", "-q", "origin", "main")

    cfg = {"repo_url": feed_repo["url"], "repo_dir": str(tmp_path / "c" / "r"),
           "feed_out": str(tmp_path / "c" / "feed.json"), "branch": "main",
           "feed_schema_version": 1}
    cfg_path = tmp_path / "c" / "config.json"
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    assert sync.main(["--config", str(cfg_path)]) == 1
    assert not pathlib.Path(cfg["feed_out"]).exists()


def test_sync_rejects_stale_feed(tmp_path, feed_repo):
    publish = _load("pub_feed3", "scripts/publish_feed_github.py")
    sync = _load("sync_feed3", "services/feed_relay/client/sync_feed.py")
    old = (publish.now_kst() - publish.timedelta(days=3)).isoformat(timespec="seconds")
    src = tmp_path / "old.json"
    src.write_text(json.dumps({"generated_at": old,
                               "candidates": {"close": {"items": [{"stock_code": "1"}]}}}),
                   encoding="utf-8")
    feed, meta = publish.prepare_payload(str(src), 18.0, True)
    publish.publish(str(feed_repo["work"]), feed, meta, "origin", "main", False, "feed: stale")

    cfg = {"repo_url": feed_repo["url"], "repo_dir": str(tmp_path / "s" / "r"),
           "feed_out": str(tmp_path / "s" / "feed.json"), "branch": "main",
           "feed_schema_version": 1, "max_age_hours": 18.0}
    cfg_path = tmp_path / "s" / "config.json"
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    assert sync.main(["--config", str(cfg_path)]) == 1
    assert not pathlib.Path(cfg["feed_out"]).exists()


def test_publish_refuses_empty_candidates(tmp_path, feed_repo):
    publish = _load("pub_feed4", "scripts/publish_feed_github.py")
    src = tmp_path / "empty.json"
    src.write_text(json.dumps({"generated_at": publish.now_kst().isoformat(timespec="seconds"),
                               "candidates": {"close": {"items": []}}}), encoding="utf-8")
    with pytest.raises(SystemExit):
        publish.prepare_payload(str(src), 18.0, False)

"""T10 전략 등록 테스트 — YAML 등록·strategy_config upsert·main.py 페이퍼 전용 라우팅."""

import pytest
from pathlib import Path
from unittest.mock import Mock

import yaml

from app.main import StrategyAgentService, _FACTOR_STRATEGY_NAMES
from app.storage.postgres_storage import PostgresStorage
from app.storage.redis_storage import RedisStorage

REPO_ROOT = Path(__file__).resolve().parents[3] if len(Path(__file__).resolve().parents) > 3 else Path("/app")
# 컨테이너 마운트(/app/tests)에서는 parents[3] 이 존재하지 않음 — config 디렉터리로 repo root 탐색
for _p in Path(__file__).resolve().parents:
    if (_p / "config" / "strategies" / "strategies.yaml").exists():
        REPO_ROOT = _p
        break
YAML_PATH = REPO_ROOT / "config" / "strategies" / "strategies.yaml"
FACTOR_NAMES = ["value_factor", "quality_factor", "momentum_factor", "lowvol_factor", "multifactor"]


def _load_yaml():
    with open(YAML_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _make_service(monkeypatch):
    monkeypatch.setattr("app.main.init_metrics", lambda *a, **k: None)
    pg = Mock()
    pg.get_strategy_config.return_value = None
    pg.upsert_strategy_config.return_value = True
    redis_mock = Mock()
    redis_mock.publish_paper_signal.return_value = True
    monkeypatch.setattr("app.main.PostgresStorage", lambda: pg)
    monkeypatch.setattr("app.main.RedisStorage", lambda: redis_mock)
    svc = StrategyAgentService()
    svc.redis = redis_mock
    return svc, pg, redis_mock


def test_yaml_contains_factor_strategies():
    strategies = _load_yaml()["strategies"]
    for name in FACTOR_NAMES:
        assert name in strategies, f"{name} missing from strategies.yaml"
        assert strategies[name]["is_active"] is True
        assert strategies[name]["parameters"]["paper_only"] is True


def test_yaml_factor_params_have_no_thresholds():
    strategies = _load_yaml()["strategies"]
    for name in FACTOR_NAMES:
        assert set(strategies[name]["parameters"].keys()) == {"paper_only"}


def test_registration_upserts_all_factor_strategies(monkeypatch):
    svc, pg, _ = _make_service(monkeypatch)
    assert pg.upsert_strategy_config.call_count == 5
    names = [c.kwargs["strategy_name"] for c in pg.upsert_strategy_config.call_args_list]
    assert set(names) == set(FACTOR_NAMES)
    for c in pg.upsert_strategy_config.call_args_list:
        assert c.kwargs["strategy_type"] == "factor"
        assert c.kwargs["parameters"] == {"paper_only": True}
        assert c.kwargs["is_active"] is True


def test_upsert_strategy_config_uses_on_conflict():
    storage = PostgresStorage.__new__(PostgresStorage)
    conn = Mock()
    storage._pool = Mock()
    storage._pool.getconn.return_value = conn
    storage._get_conn = lambda: conn
    storage._put_conn = lambda c: None
    assert storage.upsert_strategy_config("value_factor", "factor", {"paper_only": True}, True) is True
    sql = conn.cursor.return_value.execute.call_args[0][0]
    assert "INSERT INTO strategy_config" in sql
    assert "ON CONFLICT (strategy_name) DO UPDATE" in sql


def test_publish_paper_signal_uses_paper_stream():
    storage = RedisStorage.__new__(RedisStorage)
    storage._client = Mock()
    storage._streams = Mock()
    storage.PAPER_STREAM_NAME = "paper:factor_signals"
    ok = storage.publish_paper_signal({
        "action": "buy", "stock_code": "STK0000", "strategy_name": "value_factor", "confidence": 0.8,
    })
    assert ok is True
    assert storage._streams.xadd.call_args[0][0] == "paper:factor_signals"
    assert storage._streams.xadd.call_args[0][1]["action"] == "buy"


def _install_mocks(svc, legacy_signals=None, factor_signals=None):
    legacy_signals = legacy_signals or []
    factor_signals = factor_signals or {name: [] for name in FACTOR_NAMES}
    svc.theme_strategy = Mock(analyze=Mock(return_value=legacy_signals))
    svc.cycle_strategy = Mock(analyze=Mock(return_value=legacy_signals))
    svc.twin_strategy = Mock(analyze=Mock(return_value=legacy_signals))
    svc.stop_loss.evaluate_positions = Mock(return_value=[])
    attr_names = {
        "value_factor": "value_strategy",
        "quality_factor": "quality_strategy",
        "momentum_factor": "momentum_strategy",
        "lowvol_factor": "lowvol_strategy",
        "multifactor": "multifactor_strategy",
    }
    for name in FACTOR_NAMES:
        strat = Mock(analyze=Mock(return_value=factor_signals.get(name, [])))
        setattr(svc, attr_names[name], strat)
    return svc


def test_run_all_strategies_publishes_factor_signals_paper_only(monkeypatch):
    svc, _, redis_mock = _make_service(monkeypatch)
    factor_signals = {
        "value_factor": [{"action": "buy", "stock_code": "STK0000", "strategy_name": "value_factor", "confidence": 0.8}],
        "quality_factor": [{"action": "buy", "stock_code": "STK0001", "strategy_name": "quality_factor", "confidence": 0.7}],
        "momentum_factor": [{"action": "buy", "stock_code": "STK0002", "strategy_name": "momentum_factor", "confidence": 0.9}],
        "lowvol_factor": [{"action": "buy", "stock_code": "STK0003", "strategy_name": "lowvol_factor", "confidence": 0.6}],
        "multifactor": [{"action": "buy", "stock_code": "STK0004", "strategy_name": "multifactor", "confidence": 0.85}],
    }
    svc._process_and_publish = Mock()
    _install_mocks(svc, factor_signals=factor_signals)
    svc.run_all_strategies()
    assert redis_mock.publish_paper_signal.call_count == 5
    assert svc._process_and_publish.call_count == 0
    published = [c.args[0]["stock_code"] for c in redis_mock.publish_paper_signal.call_args_list]
    assert set(published) == {"STK0000", "STK0001", "STK0002", "STK0003", "STK0004"}


def test_legacy_strategy_error_does_not_stop_factor_strategies(monkeypatch):
    svc, _, redis_mock = _make_service(monkeypatch)
    svc.theme_strategy = Mock(analyze=Mock(side_effect=RuntimeError("theme broken")))
    svc.cycle_strategy = Mock(analyze=Mock(side_effect=RuntimeError("cycle broken")))
    svc.twin_strategy = Mock(analyze=Mock(side_effect=RuntimeError("twin broken")))
    svc.stop_loss.evaluate_positions = Mock(side_effect=RuntimeError("sl broken"))
    factor_signals = {
        "value_factor": [{"action": "buy", "stock_code": "STK0000", "strategy_name": "value_factor"}],
        "quality_factor": [{"action": "buy", "stock_code": "STK0001", "strategy_name": "quality_factor"}],
        "momentum_factor": [{"action": "buy", "stock_code": "STK0002", "strategy_name": "momentum_factor"}],
        "lowvol_factor": [{"action": "buy", "stock_code": "STK0003", "strategy_name": "lowvol_factor"}],
        "multifactor": [{"action": "buy", "stock_code": "STK0004", "strategy_name": "multifactor"}],
    }
    _install_mocks(svc, factor_signals=factor_signals)
    svc.run_all_strategies()
    assert redis_mock.publish_paper_signal.call_count == 5


def test_one_factor_strategy_error_does_not_stop_others(monkeypatch):
    svc, _, redis_mock = _make_service(monkeypatch)
    factor_signals = {name: [] for name in FACTOR_NAMES}
    factor_signals["value_factor"] = [{"action": "buy", "stock_code": "STK0000", "strategy_name": "value_factor"}]
    factor_signals["quality_factor"] = [{"action": "buy", "stock_code": "STK0001", "strategy_name": "quality_factor"}]
    _install_mocks(svc, factor_signals=factor_signals)
    svc.momentum_strategy = Mock(analyze=Mock(side_effect=RuntimeError("momentum broken")))
    svc.run_all_strategies()
    assert redis_mock.publish_paper_signal.call_count == 2

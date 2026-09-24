"""record_strategy_run 변환 헬퍼 단위 테스트 — DB·네트워크 무관 (호스트 pytest).

- to_native: numpy 스칼라 → 네이티브 int/float/bool/None 변환
- to_jsonable: meta(jsonb) 안의 numpy 값 재귀 변환
"""
import json
import os
import sys

import numpy as np
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import record_strategy_run as rsr


class TestToNative:
    def test_none(self):
        assert rsr.to_native(None) is None

    def test_plain_types_pass_through(self):
        assert rsr.to_native(42) == 42
        assert rsr.to_native(3.14) == 3.14
        assert rsr.to_native("ok") == "ok"
        assert rsr.to_native(True) is True

    def test_np_float64(self):
        v = rsr.to_native(np.float64(0.7129))
        assert type(v) is float
        assert v == pytest.approx(0.7129)

    def test_np_int64(self):
        v = rsr.to_native(np.int64(1765))
        assert type(v) is int
        assert v == 1765

    def test_np_bool(self):
        v = rsr.to_native(np.bool_(True))
        assert type(v) is bool
        assert v is True

    def test_np_nan_and_inf(self):
        # NaN/inf도 np.generic .item() 을 거쳐 네이티브 float이 되어야 함
        v = rsr.to_native(np.float64("nan"))
        assert isinstance(v, float)


class TestToJsonable:
    def test_nested_dict(self):
        d = {"auc": np.float64(0.71), "n": np.int64(5),
             "nested": {"win": np.float32(0.62)}, "list": [np.int64(1), 2.0]}
        out = rsr.to_jsonable(d)
        # json.dumps 가 numpy 없이도 성공해야 함
        s = json.dumps(out)
        parsed = json.loads(s)
        assert parsed["auc"] == pytest.approx(0.71)
        assert parsed["n"] == 5
        assert parsed["nested"]["win"] == pytest.approx(0.62, rel=1e-5)
        assert parsed["list"] == [1, 2.0]

    def test_none_and_scalars(self):
        assert rsr.to_jsonable(None) is None
        assert rsr.to_jsonable("x") == "x"
        assert rsr.to_jsonable(True) is True
        assert rsr.to_jsonable(3) == 3

    def test_tuple(self):
        assert rsr.to_jsonable((np.float64(1.5), np.int64(2))) == [1.5, 2]

    def test_ndarray(self):
        out = rsr.to_jsonable(np.array([1, 2, 3]))
        assert out == [1, 2, 3]
        assert json.dumps(out) == "[1, 2, 3]"

    def test_nested_ndarray_and_scalars(self):
        d = {"arr": np.array([[1.5, 2.5], [3.5, 4.5]]), "n": np.int64(9),
             "inner": [{"x": np.float64(0.1)}, np.array([1, 2])]}
        parsed = json.loads(json.dumps(rsr.to_jsonable(d)))
        assert parsed["arr"] == [[1.5, 2.5], [3.5, 4.5]]
        assert parsed["n"] == 9
        assert parsed["inner"][0]["x"] == pytest.approx(0.1)
        assert parsed["inner"][1] == [1, 2]

    def test_set_and_unserializable_fallback(self):
        from datetime import datetime

        class Foo:
            def __str__(self):
                return "Foo()"

        out = rsr.to_jsonable(
            {"s": {1, 2}, "obj": Foo(), "dt": datetime(2026, 9, 24)}
        )
        parsed = json.loads(json.dumps(out, default=str))
        assert sorted(parsed["s"]) == [1, 2]
        assert parsed["obj"] == "Foo()"
        assert parsed["dt"] == "2026-09-24 00:00:00"


class TestRecordRunParamsAreNative:
    """회귀 방지: record_run 이 넘기는 파라미터에 numpy 스칼라가 섞이지 않아야 함."""
    def test_execute_params_native(self, monkeypatch):
        captured = {}

        class FakeCur:
            def execute(self, sql, params):
                captured["params"] = params
            def close(self):
                pass
        class FakeConn:
            def cursor(self):
                return FakeCur()
            def commit(self):
                pass
            def close(self):
                pass

        monkeypatch.setattr(rsr, "_connect", lambda: FakeConn())
        ok = rsr.record_run(
            tool="backtest",
            stocks=np.int64(50),
            auc=np.float64(0.7129),
            metric_value=np.float64(0.7129),
            meta={"n_rows": np.int64(1200), "up_rate": np.float64(0.51)},
        )
        assert ok is True
        tool, run_at, status, stocks, errors, auc, acc, mv, meta_json = captured["params"]
        assert type(stocks) is int
        assert type(auc) is float
        assert type(mv) is float
        parsed_meta = json.loads(meta_json)
        assert type(parsed_meta["n_rows"]) is int

    def test_execute_params_ndarray_meta(self, monkeypatch):
        captured = {}

        class FakeCur:
            def execute(self, sql, params):
                captured["params"] = params
            def close(self):
                pass
        class FakeConn:
            def cursor(self):
                return FakeCur()
            def commit(self):
                pass
            def close(self):
                pass

        monkeypatch.setattr(rsr, "_connect", lambda: FakeConn())
        ok = rsr.record_run(tool="x", meta={"arr": np.array([1, 2, 3])})
        assert ok is True
        meta_json = captured["params"][8]
        assert json.loads(meta_json) == {"arr": [1, 2, 3]}

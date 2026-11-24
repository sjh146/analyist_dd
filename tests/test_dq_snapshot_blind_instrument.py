"""커버리지 착시 게이트가 '판정 인구 0'인데 ok 로 읽히던 것을 경고로 올리는지 고정한다.

WHY (실측 2026-11-24 20:1x): feature_coverage 209행이 창 30일(166)·525일(24)·42일(19) 뿐이고
120일 행이 0개 → `dq_feature_coverage_illusion_count_model_window`=0 이 "착시 없음"으로 읽혔다.
"""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("dq_snapshot", ROOT / "scripts" / "dq_snapshot.py")
dq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dq)


def _m(**kw):
    return {k: {"value": v} for k, v in kw.items()}


def test_all_rows_outside_model_window_is_reported():
    m = _m(dq_feature_feature_count=209, dq_feature_non_model_window_rows=209,
           dq_feature_coverage_illusion_count=40,
           dq_feature_coverage_illusion_count_model_window=0)
    out = dq.blind_instrument_warns(m)
    assert len(out) == 1
    assert "판정 인구 없음" in out[0]
    assert "209" in out[0]


def test_partial_mix_is_not_reported_as_blind():
    m = _m(dq_feature_feature_count=209, dq_feature_non_model_window_rows=71,
           dq_feature_coverage_illusion_count=63,
           dq_feature_coverage_illusion_count_model_window=26)
    assert dq.blind_instrument_warns(m) == []


def test_missing_metrics_do_not_crash_or_alert():
    assert dq.blind_instrument_warns({}) == []
    assert dq.blind_instrument_warns(_m(dq_feature_feature_count=0,
                                        dq_feature_non_model_window_rows=0)) == []

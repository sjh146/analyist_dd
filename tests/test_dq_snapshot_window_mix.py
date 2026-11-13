"""dq_snapshot 의 '창 혼재' 정보 메트릭 회귀 테스트.

WHY(실측 2026-11-13): dq_feature_coverage_illusion_count 는 feature_coverage 202행 전체를 세는데,
그 표의 window_days 는 **덮어쓰기 순서**에 따라 통째로 바뀐다.
  · 20:01 스냅샷 = 62 (≥ breach 50) — 야간 빌더가 수급·재무·거시 행을 격자 스팬(513~514일)으로 기록
  · 21:11:40 전량 재계산(feature_coverage_report, 모델 창 120일) → 202행 전부 120
  · 22:00 스냅샷 = 26 (ok)
데이터가 좋아진 것이 아니라 **모집단 창이 바뀐 것**이다. 값만 보면 '수리됐다'로 오독되므로
창 혼재 자체를 시계열로 노출하고, 0(일관) 일 때는 잡음을 만들지 않도록 침묵시킨다.
"""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("dq_snapshot", ROOT / "scripts" / "dq_snapshot.py")
dq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dq)

SPECS = {s[0]: s for s in dq.SPECS}


def test_window_mix_metrics_are_info_only():
    """신설 3종은 임계값이 없어야 한다 — 판정(알림) 인구를 바꾸지 않는 것이 이 배선의 계약이다."""
    for name in ("dq_feature_window_variants_count",
                 "dq_feature_non_model_window_rows",
                 "dq_feature_illusion_at_120d_count"):
        assert name in SPECS, name
        warn, breach = SPECS[name][2], SPECS[name][3]
        assert warn is None and breach is None, f"{name} 는 정보용이어야 한다(warn={warn}, breach={breach})"


def test_non_model_window_rows_is_watched():
    """창 혼재는 '조용히 진행되는' 신호라 상시 보고 목록에 있어야 한다(아니면 값이 바뀌어도 침묵)."""
    assert "dq_feature_non_model_window_rows" in dq.ALWAYS_REPORT
    assert "dq_feature_non_model_window_rows" in dq.SILENT_AT_ZERO


def test_silent_when_zero():
    """0 = 창 정의 일관(정상) → 매 틱 '=0' 을 찍지 않는다(잡음 금지)."""
    lines = dq.always_report_lines({
        "dq_feature_non_model_window_rows": {"value": 0, "label": "모델 창 아닌 행"},
        "dq_feature_oldest_age_days": {"value": 0.04, "label": "나이"},
    })
    assert not any("non_model_window_rows" in ln for ln in lines)
    assert any("dq_feature_oldest_age_days" in ln for ln in lines)


def test_reports_when_span_writers_return():
    """야간 빌더가 스팬 창으로 되돌리면(>0) 반드시 한 줄 올라와야 한다 — 이게 R38 재발 신호다."""
    lines = [ln for ln in dq.always_report_lines({
        "dq_feature_non_model_window_rows": {"value": 35, "label": "모델 창 아닌 행"},
    }) if "non_model_window_rows" in ln]
    assert len(lines) == 1
    assert "35.00" in lines[0]
    assert "62" in lines[0]


def test_nodata_is_not_silenced():
    """값이 없음(None)은 0 이 아니다 — 모니터링 실명을 침묵으로 숨기지 않는다."""
    lines = [ln for ln in dq.always_report_lines({
        "dq_feature_non_model_window_rows": {"value": None, "label": "모델 창 아닌 행"},
    }) if "non_model_window_rows" in ln]
    assert len(lines) == 1
    assert "없음" in lines[0]

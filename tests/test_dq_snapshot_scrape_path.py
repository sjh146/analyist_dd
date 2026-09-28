"""dq_snapshot 의 '수집 경로(스크랩) 건강' 판정 회귀 테스트.

WHY: 이 판정이 없어서 실제로 눈먼 채 지나갔다 — 실측 2026-09-28 18:11~18:21 KST.
  · postgres-exporter 스크랩이 scrape_timeout 45초에 걸려 **11회 연속 실패**
    (up=0, scrape_duration_seconds=45.009, 모든 컬렉터가 내부 60초 데드라인 초과).
  · 그 사이 dq_* 86개 시리즈가 Prometheus 5분 lookback 을 넘겨 stale(실명)이 됐는데,
    스냅샷 틱이 18:09:30 → 18:23:27 로 그 창을 비켜 가 **어떤 틱도 보고하지 않았다**.
  · 3시간 뒤 수동 조사로 발견했다. 그때 틱이 볼 수 있었던 신호가 up/scrape_duration 이다.

두 방향을 모두 고정한다.
  · 과소 — up=0 을 '정상'으로 넘기면 실명이 조용해진다.
  · 과대 — 재기동 직후 ~26초의 단발 실패(실측)를 breach 로 세면 매 재기동이 위반이 된다.
여기에 더해 문턱이 **실측 기준선 위**에 있는지도 고정한다(기준선 아래면 매 틱 경고 = 잡음).
"""
import importlib.util
import pathlib
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("dq_snapshot", ROOT / "scripts" / "dq_snapshot.py")
dq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dq)

J = dq.classify_scrape_path


def test_normal_is_ok_and_silent():
    """정상(실측 2026-09-28 21:16 up=1 / 18.9초 / 창 6성공)은 문구를 만들지 않는다 — 잡음이 없어야 신호가 산다."""
    st, msg = J(1, 6, 6, 18.9)
    assert st == "ok" and msg is None


def test_measured_outage_is_breach():
    """**실측 사고값 그대로**(2026-09-28 18:16 KST Prometheus 조회): up=0 · 6분 창 6회 중 1회만 성공
    · 지연 45.0007초 → breach. 이 세 수치는 손으로 만든 값이 아니라 Prometheus 에서 읽은 값이다."""
    st, msg = J(0, 6, 1, 45.0007)
    assert st == "breach"
    assert "실패 5회" in msg and "45" in msg and "dq_*" in msg


def test_outage_second_minute_is_already_breach():
    """사고 2분째(성공 4/6)에도 위반 — '아직 평균이 0 이 아니다'로 넘기지 않는다.

    WHY: 평균 기준(avg_over_time==0)이었다면 이 시점 avg=0.667 이라 '단발 실패(과도기)'로 분류됐다.
    실명이 6분 넘게 이어지는데 '재기동 과도기'라고 부르는 오독을 막는 것이 이 테스트의 목적이다.
    """
    st, _ = J(0, 6, 4, 45.0)
    assert st == "breach"


def test_restart_grace_single_failure_is_warn_not_breach():
    """exporter 재기동 직후 ~26초의 단발 down(6분 창 5성공/1실패)은 warn — 위반으로 깨지지 않는다."""
    st, msg = J(0, 6, 5, 3.0)
    assert st == "warn" and "과도기" in msg


def test_zero_success_at_low_duration_is_breach():
    """지연이 낮아도 창 전체가 실패면 위반 — 지속 실패는 그 자체로 소실 신호다."""
    st, _ = J(0, 6, 0, None)
    assert st == "breach"


def test_unknown_hold_fails_closed():
    """up=0 인데 실패 횟수를 못 세면 위반(fail closed) — 모르면 조용해지지 않는다."""
    st, msg = J(0, None, None, 45.0)
    assert st == "breach" and "확인 불가" in msg


def test_missing_up_signal_is_nodata_not_ok():
    """up 자체를 못 읽으면 '정상'이 아니라 판정 불가(nodata) — 없는 정보를 정상으로 세지 않는다."""
    st, msg = J(None, None, None, None)
    assert st == "nodata" and "판정 불가" in msg


def test_slow_scrape_warns_before_timeout():
    """지연이 기준선을 넘으면 위반 전에 경고 — 소실 5분 전에 알린다(리드타임)."""
    st, msg = J(1, 6, 6, 31.2)
    assert st == "warn" and "여유" in msg


def test_duration_at_timeout_threshold_is_breach():
    """지연이 타임아웃 임계에 닿으면 위반(경계값 포함)."""
    st, msg = J(1, 6, 6, dq.SCRAPE_BREACH_S)
    assert st == "breach" and "직전" in msg


def test_duration_just_below_thresholds_stays_ok():
    """문턱 바로 아래는 ok — 문턱은 '이상(>=)'에서만 발화한다."""
    assert J(1, 6, 6, dq.SCRAPE_WARN_S - 0.1)[0] == "ok"
    assert J(1, 6, 6, dq.SCRAPE_BREACH_S - 0.1)[0] == "warn"


def test_thresholds_sit_above_measured_baseline():
    """문턱은 실측 기준선(정상 최대 28.2초) **위**에 있어야 한다.

    기준선 아래에 두면 매 틱 경고가 떠서 신호가 죽는다(dq_feature_stock_constant_ratio 0.35/0.38 실측 사고와
    같은 함정). 동시에 breach 는 scrape_timeout(45초)을 넘지 않아야 의미가 있다.
    """
    assert dq.SCRAPE_WARN_S > 28.2
    assert dq.SCRAPE_WARN_S < dq.SCRAPE_BREACH_S <= 45.0
    assert dq.SCRAPE_FAILS_BREACH >= 2  # 1 == 1 로 두면 단발 과도기가 위반이 된다

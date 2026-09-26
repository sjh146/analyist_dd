"""dq_snapshot 의 모니터링 사각지대 판정 회귀 테스트.

WHY: 이 판정은 두 방향으로 틀릴 수 있고 둘 다 실측으로 겪었다.
  · 과소 — nodata 를 위반으로 안 세면 '모니터링 실명'이 rc=0 '정상'으로 보고된다(2026-09-25).
  · 과대 — 재부팅 직후 전 메트릭 nodata 를 위반으로 세면 데이터 장애가 아닌 기동 지연이
    위반으로 보고된다(2026-09-26 18:44: WSL 재부팅 18:44 / 스냅샷 18:44:39 / DB커넥션 18:44:44).
따라서 '기동 과도기만 경고로 낮추고 나머지는 위반' 이라는 경계를 테스트로 고정한다.
"""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("dq_snapshot", ROOT / "scripts" / "dq_snapshot.py")
dq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dq)

N_TOTAL = len(dq.SPECS)
CORE = sorted(dq.CORE_ALWAYS)
ALL = [s[0] for s in dq.SPECS]  # 20개 전부 — 전면 소실 시나리오


def _run(nodata, uptime):
    core_missing = [n for n in nodata if n in dq.CORE_ALWAYS]
    return dq.classify_blackout(nodata, N_TOTAL, core_missing, uptime)


def test_total_blackout_after_boot_is_warn():
    """전면 소실 + 업타임 60초 = 기동 과도기 → warn(사람이 매 틱 위반으로 깨지지 않게)."""
    sev, msg = _run(ALL, 60.0)
    assert sev == "warn"
    assert "기동 과도기" in msg


def test_total_blackout_with_uptime_above_grace_is_breach():
    """같은 전면 소실이라도 업타임이 지나면 위반 — 실명을 감추지 않는다."""
    sev, msg = _run(ALL, 301.0)
    assert sev == "breach"
    assert "기동 과도기" not in msg


def test_partial_blackout_shortly_after_boot_is_still_breach():
    """부분 소실(17/20 = 0.85 < 0.9)은 기동 과도기로 낮추지 않는다 — 타겟 하나만 죽은 건 장애다."""
    partial = ALL[:17]
    sev, _ = _run(partial, 60.0)
    assert sev == "breach"


def test_unknown_uptime_fails_closed():
    """업타임을 못 읽으면 위반으로 둔다(모르면 조용해지지 않는다)."""
    sev, _ = _run(ALL, None)
    assert sev == "breach"


def test_no_nodata_reports_zero_blackout():
    """소실 0 이면 판정 문구가 nodata 0/20 임을 명시한다(호출부는 3.3절 조건문으로 게이트한다)."""
    sev, msg = _run([], 60.0)
    assert sev == "breach"  # 호출부가 호출하지 않으므로 여기 도달하면 메시지는 사실대로 남는다
    assert f"nodata 0/{N_TOTAL}" in msg


def test_api_failure_returns_empty_instead_of_raising():
    """Prometheus 도달 불가 → 예외 대신 빈 dict(조회 실패를 '값 없음'으로 흘려보낸다)."""
    old = dq.PROM
    dq.PROM = "http://127.0.0.1:9"  # 닫힌 포트
    try:
        assert dq._api("/api/v1/query", {"query": "up"}) == {}
        assert dq.instant("up") == (None, [])
        assert dq.range_series("up", 1) == []
    finally:
        dq.PROM = old

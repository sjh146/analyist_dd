"""dq_snapshot `_api` 사각지대 단락(short-circuit) 회귀 테스트.

WHY(실측 2026-11-21 18:47 KST): 스택 컨테이너 14개가 내려가 Prometheus 가 없어진 상태에서
researcher_cycle 의 dq 스냅샷이 **600초 상한에 걸려 rc=99** 로 끝났다. Prometheus 가 연결을
즉시 거부하면(빠른 실패) 사각지대 breach 로 정상 판정되지만(같은 날 18:57), SYN drop 처럼
**느리게 실패**하면 요청당 20초 × 200여 회 = 틱 상한 초과 → 판정 줄이 아예 사라진다.
사각지대를 판정하려고 만든 코드가 사각지대에서 판정을 못 하면 안 된다 → 연속 실패 5회에서
남은 조회를 건너뛴다. 이 파일은 그 계약(단락 · 리셋 · 오탐 금지)을 고정한다.
"""
import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("dq_snapshot", ROOT / "scripts" / "dq_snapshot.py")
dq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dq)


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        import json
        return json.dumps(self._payload)


class _Opener:
    """호출 횟수를 세고, 지정한 실패 횟수까지 OSError 를 던진다."""

    def __init__(self, fail_times=10 ** 6, payload=None):
        self.fail_times = fail_times
        self.calls = 0
        self.payload = payload or {"data": {"result": [{"metric": {}, "value": [0, "1"]}]}}

    def __call__(self, url):
        self.calls += 1

        class _O:
            def open(_self, u, timeout=None):
                if self.calls <= self.fail_times:
                    raise OSError("connection timed out (simulated SYN drop)")
                return _Resp(self.payload)

        return _O()


@pytest.fixture(autouse=True)
def _reset():
    dq.reset_api_state()
    yield
    dq.reset_api_state()


def test_short_circuits_after_streak(monkeypatch):
    """연속 실패가 BLIND_STREAK 에 닿으면 이후 조회는 opener 를 부르지 않는다(시간 폭발 방지)."""
    fake = _Opener()
    monkeypatch.setattr(dq, "opener", fake)

    for _ in range(dq.BLIND_STREAK):
        assert dq.instant("up")[0] is None
    assert fake.calls == dq.BLIND_STREAK
    assert dq._API_STATE["blind"] is True

    for _ in range(50):
        assert dq.instant("up")[0] is None
    assert fake.calls == dq.BLIND_STREAK, "blind 이후에도 조회하면 20초×N 으로 틱 상한을 넘긴다"


def test_success_resets_streak(monkeypatch):
    """중간에 한 번이라도 성공하면 연속 카운터가 0 으로 돌아간다(단발 실패는 사각지대가 아니다)."""
    fake = _Opener(fail_times=3)
    monkeypatch.setattr(dq, "opener", fake)

    for _ in range(3):
        dq.instant("up")
    assert dq._API_STATE["fail_streak"] == 3

    vals, _ = dq.instant("up")
    assert vals == [1.0]
    assert dq._API_STATE["fail_streak"] == 0
    assert dq._API_STATE["blind"] is False


def test_streak_below_threshold_keeps_querying(monkeypatch):
    """문턱 미만이면 단락하지 않는다 — 일시적 지연으로 모니터링을 스스로 끄면 안 된다."""
    fake = _Opener(fail_times=4)
    monkeypatch.setattr(dq, "opener", fake)

    for _ in range(4):
        dq.instant("up")
    assert dq._API_STATE["blind"] is False

    vals, _ = dq.instant("up")
    assert vals == [1.0]
    assert fake.calls == 5


def test_blind_returns_empty_dict_not_exception(monkeypatch):
    """단락 상태에서도 예외 없이 빈 dict(=nodata) — 호출부가 '값 없음'으로 판정해야 한다."""
    monkeypatch.setattr(dq, "opener", _Opener())
    for _ in range(dq.BLIND_STREAK):
        dq.instant("up")
    assert dq.instant("up") == (None, [])

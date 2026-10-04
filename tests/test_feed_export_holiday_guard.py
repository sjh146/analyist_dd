"""feed_export KRX 휴장일 가드 — 휴장일에 신선한 피드가 나가 실주문 경로가 열리는 사고 방지.

왜 필요한가 (실측 2026-10-05 08:30 KST, 개천절 대체공휴일)
- 소비자(trader-agent)의 ``market_is_open`` 은 평일 09:00-15:30 시계만 본다
  (``trader_core/profiles.py``: "Business days only ... exchange holidays are not").
- 그날 08:30 에 루프 감독기(supervise_loop.ps1)가 휴장일인데도 **라이브 루프를 기동**했고
  (pid 14064), 08:30 swing 파이프라인이 산출물 date 를 10-04 로 갱신해 발행측 신선도
  가드(>3일이면 비움)까지 통과했다. 발행되면 상위10 평균확률 0.5978 > R1(swing 0.58)
  로 **휴장일에 실매수 시도**가 가능했다.
- 수리: 발행측이 ``data/krx_holidays.json`` 을 보고 휴장일엔 모든 전략을 빈 리스트로
  발행한다(``--ignore-holiday`` 로 수동 우회). 캘린더를 못 읽으면 False — 발행을 막지 않는다.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import feed_export as fe  # noqa: E402


def test_krx_holiday_known_dates():
    # 2026-10-05 = 개천절(10-03 토) 대체공휴일, 2026-10-09 = 한글날, 2026-10-06 = 평일 거래일.
    assert fe.is_krx_holiday(fe.datetime(2026, 10, 5).date()) is True
    assert fe.is_krx_holiday(fe.datetime(2026, 10, 9).date()) is True
    assert fe.is_krx_holiday(fe.datetime(2026, 10, 6).date()) is False


def test_krx_holiday_missing_calendar_is_false(tmp_path, monkeypatch):
    """캘린더 파일이 없으면 발행을 막지 않는다(보수적 — 파일 부재가 거래일을 막으면 더 큰 손해)."""
    monkeypatch.setattr(fe, "HOLIDAY_PATH", str(tmp_path / "nope.json"))
    assert fe.is_krx_holiday(fe.datetime(2026, 10, 5).date()) is False


def test_krx_holiday_accepts_dict_shapes(tmp_path, monkeypatch):
    p = tmp_path / "h.json"
    p.write_text(json.dumps({"holidays": [{"date": "2026-10-05"}]}), encoding="utf-8")
    monkeypatch.setattr(fe, "HOLIDAY_PATH", str(p))
    assert fe.is_krx_holiday(fe.datetime(2026, 10, 5).date()) is True
    assert fe.is_krx_holiday(fe.datetime(2026, 10, 6).date()) is False


def test_holiday_publish_is_empty(capsys, monkeypatch):
    """휴장일엔 전략을 비워 발행한다 — 후보가 하나도 나가면 안 된다."""
    monkeypatch.setattr(fe, "is_krx_holiday", lambda day: True)
    rc = fe.main(["--dry-run", "--no-stats"])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out[out.index("{"):])  # 로그 뒤 JSON 블록
    assert payload["candidates"]["close"]["items"] == []
    assert payload["candidates"]["swing"]["items"] == []


def test_ignore_holiday_still_publishes(capsys, monkeypatch):
    """--ignore-holiday 는 가드를 끄고 정상 경로로 발행한다(운영 수동 우회)."""
    monkeypatch.setattr(fe, "is_krx_holiday", lambda day: True)
    rc = fe.main(["--dry-run", "--no-stats", "--ignore-holiday"])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out[out.index("{"):])
    # 산출물이 신선하면 후보가 나온다. 오래됐으면 각 전략이 비워질 뿐(가드와 무관).
    assert "close" in payload["candidates"]

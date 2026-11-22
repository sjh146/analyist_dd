"""피드 원천 산출물 '나이' 계산 회귀 — date(00:00) 대신 generated_at 우선.

왜(실측 2026-11-23 08:20 발행 로그):
  [close] 산출물이 3.3일 지났다(> 3.0일) — 이 전략은 빈 리스트로 발행
  [swing] 산출물이 4.3일 지났다(> 3.0일) — 이 전략은 빈 리스트로 발행
close 산출물의 generated_at 은 2026-11-20T14:40 KST(=2.74일 전)였는데, 계산이
payload["date"]("2026-11-20" → 00:00 KST)를 먼저 봐서 3.34일로 부풀려졌다.
주말 경계(금→월)마다 3.0일 문턱을 넘겨 close/swing 이 통째로 사라지는 구조였다.
"""
import os
import sys
from datetime import datetime, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import feed_export  # noqa: E402

KST = feed_export.KST


def test_generated_at_wins_over_date():
    now = datetime(2026, 11, 23, 8, 20, tzinfo=KST)
    payload = {"date": "2026-11-20", "generated_at": "2026-11-20T14:40:09+09:00"}
    age = feed_export.source_age_days(payload, now=now)
    assert abs(age - 2.74) < 0.02, age
    assert age < 3.0  # 금요일 산출물은 월요일 개장 전에 유효해야 한다


def test_utc_generated_at_is_converted():
    now = datetime(2026, 11, 23, 8, 20, tzinfo=KST)
    payload = {"generated_at": "2026-11-20T05:40:09Z"}
    assert abs(feed_export.source_age_days(payload, now=now) - 2.74) < 0.02


def test_date_only_falls_back_to_midnight():
    now = datetime(2026, 11, 23, 8, 20, tzinfo=KST)
    age = feed_export.source_age_days({"date": "2026-11-20"}, now=now)
    assert abs(age - 3.34) < 0.02  # 폴백은 예전 그대로(보수적)


def test_stale_artifact_still_counts_as_old():
    now = datetime(2026, 11, 23, 8, 20, tzinfo=KST)
    old = (now - timedelta(days=6)).isoformat()
    assert feed_export.source_age_days({"generated_at": old}, now=now) > 3.0


def test_missing_stamp_is_none():
    assert feed_export.source_age_days({}, now=datetime(2026, 11, 23, 8, 20, tzinfo=KST)) is None

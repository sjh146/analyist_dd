"""T17 프로브 회귀 — 트레이더 환류(스크리너 귀속) 소비 게이트.

고정하는 실측 함정:
 · 무거래(n=0)를 미달로 세면 신규 실계좌에서 매 틱 오탐(스킬: '판정 대상 없는 날은 통과').
 · 확률구간(ml_prob) 부재를 미달로 세우면 리서처가 못 고치는 check 가 된다 → 정보 줄로만.
 · 리포트 낡음을 통과로 두면 환류 경로가 조용히 죽는다(2026-09-28 '조용한 0행' 유형).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import t17_screener_attribution as t17  # noqa: E402

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 7, 21, 10, tzinfo=KST)


def _stats(**over):
    base = {
        "generated_at": "2026-10-07T16:40:03+09:00",
        "source": "trader-agent/data/fills",
        "files": ["fills_2026-10-07.json"],
        "rows": 3,
        "all": {"n": 3, "net_pnl": -1446.0, "expectancy_krw": -482.0},
        "by_screener": {"close": {"n": 3, "net_pnl": -1446.0, "win_rate": 33.3}},
        "scored_n": 0,
    }
    base.update(over)
    return base


def test_healthy_report_passes():
    v, _ = t17.decide(_stats(), NOW)
    assert v == 1


def test_missing_report_is_fail():
    v, reason = t17.decide(None, NOW)
    assert v == 0 and "리포트 없음" in reason


def test_unparsable_generated_at_is_fail():
    v, _ = t17.decide(_stats(generated_at="not-a-date"), NOW)
    assert v == 0


def test_stale_report_is_fail():
    v, reason = t17.decide(_stats(generated_at="2026-09-30T16:40:03+09:00"), NOW)  # 7일
    assert v == 0 and "낡음" in reason


def test_weekend_gap_under_threshold_passes():
    # 금 16:40 → 월 휴장 → 화 16:00 (≈4.0일) 은 정상
    v, _ = t17.decide(_stats(generated_at="2026-10-03T16:40:03+09:00"),
                      datetime(2026, 10, 7, 16, 0, tzinfo=KST))
    assert v == 1


def test_rows_but_empty_breakdown_is_fail():
    v, reason = t17.decide(_stats(rows=3, by_screener={}), NOW)
    assert v == 0 and "귀속" in reason


def test_blank_screener_label_is_fail():
    v, reason = t17.decide(_stats(by_screener={"": {"n": 2}, "close": {"n": 1}}), NOW)
    assert v == 0 and "라벨" in reason


def test_zero_closed_trades_passes():
    # 신규 계좌 무거래 — 결함이 아니다
    v, reason = t17.decide(_stats(rows=0, by_screener={}, all={"n": 0}), NOW)
    assert v == 1 and "0건" in reason


def test_naive_timestamp_treated_as_kst():
    v, _ = t17.decide(_stats(generated_at="2026-10-07T16:40:03"), NOW)
    assert v == 1


def test_attribution_lines_show_signal_only_expectancy():
    """사람 수동청산(owner_exit)을 신호 기대값 분모에서 분리해 찍는다.

    실측 2026-11-10: owner_exit 1건(−14,620원)이 전체 기대값을 −742 → −2,477원/거래로
    3.3배 왜곡했다. 분해 줄이 없으면 '스크리너가 돈을 잃는다'는 잘못된 결론이 나간다.
    """
    stats = _stats(by_exit_kind={
        "signal": {"n": 7, "net_pnl": -5197.0, "expectancy_krw": -742.4},
        "owner_manual": {"n": 1, "net_pnl": -14620.0, "expectancy_krw": -14620.0},
    })
    lines = t17.attribution_lines(stats)
    joined = "\n".join(lines)
    assert "신호 청산만 n=7" in joined
    assert "-742.4원" in joined
    assert "사람 수동청산 n=1건" in joined
    # 판정값은 바뀌지 않는다(측정 분해일 뿐 게이트가 아니다)
    assert t17.decide(stats, NOW)[0] == 1


def test_attribution_lines_tolerate_old_report_without_exit_kind():
    lines = t17.attribution_lines(_stats())
    assert not any("신호 청산만" in line for line in lines)

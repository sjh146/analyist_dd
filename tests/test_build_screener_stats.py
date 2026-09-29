"""build_screener_stats 단위 테스트 (DB 없이 fake cursor).

왜: 이 스크립트의 출력은 트레이더의 **켈리 사전확률**(사이징)로 직결되므로
(1) 어느 스크리너에 귀속되는지, (2) 축소(shrink)와 f*<=0 가드, (3) 어떤 행을 쓸 수 있는지가
틀리면 조용히 잘못된 사이징이 된다. 2026-09-29 수리: 예전에는 모든 backtest_pnl 행을
"close" 로 귀속해 **swing 모델 성과가 close 통계로 발행**됐다.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import build_screener_stats as bss  # noqa: E402


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *args, **kwargs):  # noqa: D401 - the SQL is irrelevant here
        return None

    def fetchall(self):
        return self._rows


def _row(run_at, win_rate=0.53, avg_win=6.8, avg_loss=2.5, n=47, **extra):
    meta = {"win_rate": win_rate, "avg_win_pct": avg_win, "avg_loss_pct": avg_loss,
            "n_trades": n, "walkforward": True}
    meta.update(extra)
    return (meta, 50, run_at)


BASE = datetime(2026, 9, 25, 4, 6, 35, tzinfo=timezone.utc)


def test_backtest_rows_are_attributed_to_swing_by_default():
    """meta 에 이름이 없으면 close 가 아니라 swing 이다(09-29 수리)."""
    per = bss.collect_from_runs(FakeCursor([_row(BASE)]))

    assert set(per) == {"swing"}
    assert per["swing"]["n"] == 47


def test_explicit_screener_name_in_meta_wins():
    per = bss.collect_from_runs(
        FakeCursor([_row(BASE, screener="close"), _row(BASE, screener="swing")])
    )

    assert set(per) == {"close", "swing"}


def test_rows_without_avg_pct_are_unusable():
    """b(=평균이익/평균손실)를 알 수 없는 옛 기록은 켈리 계산에 쓸 수 없다."""
    rows = [
        ({"win_rate": 0.6, "n_trades": 10}, 50, BASE),   # avg_* 없음 → 제외
        _row(BASE),
    ]
    per = bss.collect_from_runs(FakeCursor(rows))

    assert per["swing"]["n"] == 47
    assert per["swing"]["windows"] == 1


def test_only_the_latest_batch_is_used():
    """챔피언이 바뀌면 과거 배치(다른 모델)의 승률을 섞으면 안 된다."""
    old = BASE - timedelta(hours=6)
    per = bss.collect_from_runs(FakeCursor([_row(BASE), _row(old, n=99)]))

    assert per["swing"]["n"] == 47  # 오래된 배치 제외


def test_shrink_keeps_a_positive_edge_and_reports_f_star():
    p, b, f = bss.shrink(0.532, 6.861 / 2.5)  # 왕복 창1(가장 좋았던 구간)

    assert p == pytest.approx(0.516)
    assert b == pytest.approx(1.0 + (2.7444 - 1.0) * 0.5, abs=1e-3)
    assert f > 0


def test_negative_measured_edge_is_not_a_usable_prior():
    """실측 p=0.431/b=1.172 (n=123, 2026-06~09 워크포워드) → f* <= 0 → 기록하지 않는다."""
    p, b, f = bss.shrink(0.431, 1.172)

    assert p is None and b is None
    assert f <= 0


def test_shrink_clamps_out_of_range_inputs():
    p, b, f = bss.shrink(0.95, 50.0)

    assert p <= 0.62          # 과신 방지 상한
    assert b <= 2.0
    assert f == pytest.approx(p - (1 - p) / b)

"""R21 수급 지연 프로브 판정 회귀 테스트.

WHY: 종전 판정은 기준일을 '시장 최신일'로 잡았다. 그 기준일은 수집 주기와 무관하게(저녁 파이프라인이
일봉을 적재하면) 하루 앞으로 밀리므로 **같은 DB 상태가 다른 판정**을 받았다 — 실측 2026-09-30:
20:12 틱(시장 최신 09-29) = 최대 지연 2 → 충족 / 21:13 틱(수급 142,996행·최신 09-29 **그대로**,
시장만 09-30) = 3 → 미달. 수집은 16:20 에 돌고 그 시각 KIS 가 준 최신일까지만 담으므로 이 증가는
수집 결함이 아니라 시계 차이다. 같은 함정이 '달력일 고정 신선도 문턱'·'누적 재시작 카운트'와 동형이다.

판정 = max(① 회전 유니버스의 상대 지연(수급이 도달한 최신일 기준),
           ② 시장 최신일 − 수급 최신일(T+1 이면 1) + 1).
정상 상태에서 두 항이 모두 2 = 문턱(≤2)과 만나고, ①(회전 둔화)이나 ②(수집 정지) 중 하나라도
나빠지면 3 을 넘겨 미달이 된다 — '고쳐도 통과 못 하는 check'도, '아무것도 못 잡는 check'도 아니다.
"""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("r21_probe", ROOT / "scripts" / "r21_supply_lag_probe.py")
r21 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r21)

THRESHOLD = 2  # 백로그 R21 check_target


def test_normal_state_reaches_threshold_exactly():
    """T+1 수집 + 250종목/일 회전 = 상대 2 · 정지신호 2 → 문턱과 정확히 만난다(충족)."""
    assert r21.judgment(2, 1) == 2
    assert r21.judgment(2, 1) <= THRESHOLD


def test_market_basis_advance_does_not_change_verdict():
    """실측 2026-09-30 사고 재현: DB 수급이 그대로여도(상대 2) 시장 최신일이 밀려도 판정은 2."""
    assert r21.judgment(2, 1) == 2  # 20:12 틱
    assert r21.judgment(2, 1) == 2  # 21:13 틱 — 종전 규칙은 여기서 3(미달)을 냈다


def test_same_day_collection_is_not_penalized():
    """KIS 가 당일 수급을 준 경우(실측 09-29 16:20 as-of=09-29)에는 정지 신호가 0 → 더 좋다."""
    assert r21.judgment(2, 0) == 2


def test_tail_starvation_fires():
    """회전이 느려져 꼬리가 3거래일 뒤처지면 미달 — 설계(2일 주기) 위반."""
    assert r21.judgment(3, 1) > THRESHOLD


def test_absolute_collection_stop_fires_even_if_relative_looks_healthy():
    """①의 기준도 '수집이 도달한 날'이라 수집이 멈추면 ①만으로는 안 보인다 → ②가 덮는다."""
    assert r21.judgment(0, 2) > THRESHOLD   # 하루 더 밀림
    assert r21.judgment(2, 3) > THRESHOLD   # 이틀 더 밀림


def test_no_supply_at_all_fails_closed():
    """수급 최신일 자체가 없으면 '판정 불가'를 정상으로 보고하면 안 된다(실명 감추기 금지)."""
    assert r21.judgment(None, 0) == r21.FAIL_CLOSED > THRESHOLD


def test_sql_anchor_bounds_the_lag_window():
    """회귀 방지: 상대 지연 창이 anchor 로 상한을 갖지 않으면 기준일이 다시 '시장'이 된다."""
    assert "m.trade_date <= %s" in r21.LAG_SQL
    assert "m.trade_date <= %s" not in r21.TABLE_LAG_SQL  # 테이블 전체는 정보용(상한 없음)

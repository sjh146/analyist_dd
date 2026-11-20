"""docs/spec_regime_filter.md '테스트' 절 계약 4개 — DB·네트워크 없이 순수 함수로 검증.

① close<ma20 → risk_off  ② ret5 ≤ −3% → risk_off  ③ 둘 다 정상 → risk_on
④ 지수 없음 → unknown + 사유

market_regime.compute_regime(오름차순 종가 리스트)만 검증한다 — 실제 DB 조회 없음.
픽스처는 실측 근거를 단순화한 합성 값이다. 실측(2026-11-20, 조회 명령):
  -- SELECT trade_date, close FROM index_daily
  --   WHERE index_code='KOSDAQ' AND trade_date <= '2026-11-20'
  --   ORDER BY trade_date DESC LIMIT 21;
  -- → close=645.68, ma20=695.659, ret5=−7.715% → risk_off (둘 다 해당)
결측을 0/추정으로 메우는 경로가 없음을 ④와 함께 확인한다(스펙 §5 금지).
"""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import market_regime as mr  # noqa: E402


# ① close<ma20 → risk_off (ret5 는 미해당 — close 조건만으로 차단됨을 확인)
#    [100×14, 98, 99, 98, 99, 98, 97]: ma20=99.45, close=97 → 97<99.45
#    ret5 = (97/98−1)×100 = −1.02% (> −3%) → reason 은 "close<ma20" 단독이어야 한다
def test_risk_off_close_below_ma20():
    closes = [100.0] * 14 + [98.0, 99.0, 98.0, 99.0, 98.0, 97.0]
    r = mr.compute_regime(closes, ma=20)
    assert r["state"] == "risk_off"
    assert r["reason"] == "close<ma20"
    assert abs(r["ma20"] - 99.45) < 1e-9
    assert r["close"] == 97.0
    assert r["ret5"] == round((97.0 / 98.0 - 1.0) * 100.0, 3)


# ② ret5 ≤ −3% → risk_off (close 는 ma20 위 — ret5 조건만으로 차단됨을 확인)
#    [80×14, 95, 96, 97, 98, 99, 92]: ma20=84.85, close=92 → 92≥84.85 (close 조건 미해당)
#    ret5 = (92/95−1)×100 = −3.158% (≤ −3%) → reason 은 "ret5<=-3%" 단독
def test_risk_off_ret5_drop():
    closes = [80.0] * 14 + [95.0, 96.0, 97.0, 98.0, 99.0, 92.0]
    r = mr.compute_regime(closes, ma=20)
    assert r["state"] == "risk_off"
    assert r["reason"] == "ret5<=-3%"
    assert r["close"] >= r["ma20"]           # close 조건은 미해당
    assert r["ret5"] == round((92.0 / 95.0 - 1.0) * 100.0, 3)


# ③ 둘 다 정상 → risk_on
def test_risk_on_when_normal():
    r = mr.compute_regime([100.0] * 20, ma=20)
    assert r["state"] == "risk_on"
    assert r["reason"] == "ok"
    assert r["ma20"] == 100.0
    assert r["close"] == 100.0
    assert r["ret5"] == 0.0


# ④ 지수 없음 → unknown + 사유 (0 채움·추정 금지 — 값들은 None 그대로)
def test_unknown_when_no_data():
    r = mr.compute_regime([], ma=20)
    assert r["state"] == "unknown"
    assert "없음" in r["reason"]
    assert r["close"] is None and r["ma20"] is None and r["ret5"] is None
    # ma20 계산에 행이 부족한 경우도 추정하지 않고 unknown(급락 신호도 없을 때)
    short = mr.compute_regime([100.0, 101.0, 102.0], ma=20)
    assert short["state"] == "unknown"
    assert "부족" in short["reason"] and short["ma20"] is None

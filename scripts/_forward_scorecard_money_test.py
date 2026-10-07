#!/usr/bin/env python3
"""CG137 자체점검 — forward_scorecard 의 체결성·수수료·널 기준선 money 블록 (pytest 불필요).

검사 대상(모두 순수 함수 + 파서 배선):
  1. fillable_ok: 상한가/급등 제외 · 거래대금 하한 · 자료 없음(None)은 통과 금지 · 첫 행(i=0) 판정불가
  2. money_stats: kept/rows/no_filter_data 집계 · Δ = top-k − 풀평균(널 기준선) · t · 제로분산 t=None · 분할표본
  3. 수수료는 Δ 에서 상쇄되고 절대 수준에서만 걸린다(문서화된 성질을 테스트로 고정)
  4. k >= 후보 수 포화(Δ=0) 케이스
  5. 파서 배선: parse_forward_scorecard 가 창별 `money`/`money_filter` 를 통과시키는가
  6. 상수 단일 진실원: fillable_expectancy 를 import 할 수 있으면 FEE_*·LIMIT_UP_PCT 가 같은가

실행: python3 scripts/_forward_scorecard_money_test.py            (호스트)
      docker exec -e PYTHONPATH=/app stock_xgboost_ml python /app/scripts/_forward_scorecard_money_test.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import types

FAIL = []
PASS = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


# psycopg2 는 호스트에 없다 → 모듈 임포트 전에 스텁 주입(순수 함수만 검사한다).
try:  # noqa: SIM105
    import psycopg2  # noqa: F401
except Exception:  # noqa: BLE001
    sys.modules["psycopg2"] = types.ModuleType("psycopg2")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import forward_scorecard as fw  # noqa: E402

FEE = 0.00015 + 0.00015 + 0.0018  # 0.21%p 왕복

# ---------------------------------------------------------------- 1. fillable_ok
closes = {"A": {"2026-01-01": 100.0, "2026-01-02": 100.0, "2026-01-03": 130.0, "2026-01-04": 126.0}}
tvals = {"A": {"2026-01-01": 5e9, "2026-01-02": 5e9, "2026-01-03": 5e9, "2026-01-04": 5e8}}
days = ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"]

# i=0 → 전일종가 없음 = 판정불가
check("fillable_ok: 전일종가 없음(i=0) → None", fw.fillable_ok("A", days, closes, tvals, 0) is None)
# i=1: 등락 0%, 거래대금 50억 → 체결가능
check("fillable_ok: 등락0%·거래대금 50억 → True", fw.fillable_ok("A", days, closes, tvals, 1) is True)
# i=2: 등락 +30% → 상한가, 체결 불가
check("fillable_ok: +30% 상한가 → False", fw.fillable_ok("A", days, closes, tvals, 2) is False)
# i=3: 등락 −3.08%, 거래대금 5억(<10억) → 체결 불가
check("fillable_ok: 거래대금 5억 → False", fw.fillable_ok("A", days, closes, tvals, 3) is False)
# 거래대금 결측 → None(보수적: 통과 금지)
tr = {k: dict(v) for k, v in tvals.items()}
tr["A"]["2026-01-02"] = None
check("fillable_ok: 거래대금 결측 → None", fw.fillable_ok("A", days, closes, tr, 1) is None)
# max_day_chg 25% 로 급등 제외(경계: 25.0 은 제외)
closes2 = {"B": {"d0": 100.0, "d1": 125.0}}
tvals2 = {"B": {"d0": 9e9, "d1": 9e9}}
check("fillable_ok: +25.0% 는 제외(경계 포함)",
      fw.fillable_ok("B", ["d0", "d1"], closes2, tvals2, 1) is False)

# ---------------------------------------------------------------- 2/3. money_stats
# 날짜 4개. 각 날: 필터 통과 3행 + 상한가 1행(제외) + 자료없음 1행.
# conf 순서대로 top-k 가 정렬되는지, Δ = top-k − 풀평균 인지 손계산으로 확인한다.
by_date = {
    "2026-01-02": [(0.9, 0.10, True), (0.8, 0.02, True), (0.7, -0.04, True), (0.99, 0.50, False), (0.5, 0.0, None)],
    "2026-01-03": [(0.6, 0.01, True), (0.5, -0.01, True), (0.4, -0.02, True), (0.95, 0.30, False), (0.3, 0.0, None)],
    "2026-01-04": [(0.85, 0.05, True), (0.45, 0.01, True), (0.35, -0.01, True), (0.98, 0.40, False), (0.2, 0.0, None)],
    "2026-01-05": [(0.75, -0.01, True), (0.55, -0.02, True), (0.25, -0.03, True), (0.97, 0.20, False), (0.1, 0.0, None)],
}
m = fw.money_stats(by_date, k=1, fee_rt=FEE)
check("money_stats: n_dates = 필터 통과 날짜 수", m["n_dates"] == 4, str(m["n_dates"]))
check("money_stats: rows = 20", m["rows"] == 20, str(m["rows"]))
check("money_stats: kept = 12(상한가 4 제외)", m["kept"] == 12, str(m["kept"]))
check("money_stats: no_filter_data = 4", m["no_filter_data"] == 4, str(m["no_filter_data"]))
check("money_stats: kept_ratio = 0.6", m["kept_ratio"] == 0.6, str(m["kept_ratio"]))

# 수계산(k=1): 각 날 top = 최고 conf 통과행, pool = 통과 3행 평균
#  01-02: top .10, pool (.10+.02-.04)/3 = .026667 → Δ = .073333
#  01-03: top .01, pool (.01-.01-.02)/3 = -.006667 → Δ = .016667
#  01-04: top .05, pool (.05+.01-.01)/3 = .016667 → Δ = .033333
#  01-05: top -.01, pool (-.01-.02-.03)/3 = -.02 → Δ = .01
hand = [7.3333, 1.6667, 3.3333, 1.0]
check("money_stats: Δ(%p) 손계산 일치", all(abs(a - b) < 0.01 for a, b in zip(m["date_deltas_pct"], hand)),
      str(m["date_deltas_pct"]))
mean = sum(hand) / 4
check("money_stats: Δ평균 = 3.3333%p", abs(m["delta_mean_pct"] - round(mean, 4)) < 0.01, str(m["delta_mean_pct"]))
check("money_stats: topk_pos_dates = 4", m["topk_pos_dates"] == 4, str(m["topk_pos_dates"]))
check("money_stats: split_half = 앞 4.5 / 뒤 2.1667",
      m["split_half_pct"] is not None and abs(m["split_half_pct"][0] - 4.5) < 0.02
      and abs(m["split_half_pct"][1] - 2.1667) < 0.02, str(m["split_half_pct"]))

# 풀평균 절대 수준은 수수료만큼 낮아진다(0.21%p)
pool_raw = (0.10 + 0.02 - 0.04 + 0.01 - 0.01 - 0.02 + 0.05 + 0.01 - 0.01 - 0.01 - 0.02 - 0.03) / 12
check("money_stats: pool_net = 풀평균 − 수수료",
      abs(m["pool_net_mean_pct"] - round((pool_raw - FEE) * 100, 4)) < 0.01,
      f"{m['pool_net_mean_pct']} vs {round((pool_raw-FEE)*100,4)}")

# 3. 수수료 상쇄: fee 를 0 으로 바꿔도 Δ 는 같고 topk_net 만 0.21 만큼 커진다
m0 = fw.money_stats(by_date, k=1, fee_rt=0.0)
check("수수료: Δ 는 fee 무관(top/pool 상쇄)", abs(m0["delta_mean_pct"] - m["delta_mean_pct"]) < 1e-9,
      f"{m0['delta_mean_pct']} vs {m['delta_mean_pct']}")
check("수수료: topk_net 은 0.21%p 만큼 커진다",
      abs((m0["topk_net_mean_pct"] - m["topk_net_mean_pct"]) - 0.21) < 0.01,
      f"{m0['topk_net_mean_pct']} vs {m['topk_net_mean_pct']}")

# 4. 포화: k >= 후보 수 → top = pool → Δ = 0
sat = fw.money_stats(by_date, k=3, fee_rt=FEE)
check("money_stats: k=후보수 포화 → Δ = 0", abs(sat["delta_mean_pct"]) < 1e-9, str(sat["delta_mean_pct"]))

# 제로분산 → t=None (fp 잔차로 t 가 1e16 로 튀지 않게)
flat = {"2026-01-02": [(0.9, 0.01, True), (0.8, 0.0, True)],
        "2026-01-03": [(0.7, 0.01, True), (0.6, 0.0, True)],
        "2026-01-04": [(0.5, 0.01, True), (0.4, 0.0, True)]}
mf = fw.money_stats(flat, k=1, fee_rt=FEE)
check("money_stats: 제로분산 → t=None", mf["delta_t"] is None, str(mf["delta_t"]))
check("money_stats: 제로분산 Δ=0.5%p 유지", abs(mf["delta_mean_pct"] - 0.5) < 1e-9, str(mf["delta_mean_pct"]))
single = fw.money_stats({"2026-01-02": [(0.9, 0.01, True)]}, k=1, fee_rt=FEE)
check("money_stats: 1세션 → sd/t None", single["delta_sd_pct"] is None and single["delta_t"] is None)

# 상한가만 있는 날(통과 0) → 그 날은 표본에서 제외
only_blocked = {"2026-01-02": [(0.9, 0.5, False)], "2026-01-03": [(0.9, 0.4, None)]}
mb = fw.money_stats(only_blocked, k=1, fee_rt=FEE)
check("money_stats: 통과 0 날짜는 제외(n_dates=0)", mb["n_dates"] == 0 and mb["delta_mean_pct"] is None)

# ---------------------------------------------------------------- 5. 파서 배선
try:
    import model_engineer_cycle as me
    payload = {"generated_at": "2026-10-08T00:00:00", "predictions_rows": 3, "model_versions": {"v1.0": 3},
               "result": {"h5": {"n_pairs": 3, "n_dates": 2, "skipped": 0, "pooled_auc": 0.53,
                                 "daily_auc_mean": 0.52, "daily_auc_list": [0.52], "base_rate_up": 0.5,
                                 "top10_ret_mean": 0.01, "all_ret_mean": 0.008,
                                 "money": m, "money_filter": {"min_value_krw": 1e9}}}}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(payload, f)
        p = f.name
    parsed = me.parse_forward_scorecard(p, 0.0)
    w = parsed.get("windows", {}).get("h5", {})
    check("parser: money 블록 통과", isinstance(w.get("money"), dict) and w["money"].get("kept") == 12,
          str(type(w.get("money"))))
    check("parser: money_filter 통과", isinstance(w.get("money_filter"), dict))
    check("parser: 기존 키 회귀 없음(all_ret_mean)", w.get("all_ret_mean") == 0.008)
    os.unlink(p)
except Exception as e:  # noqa: BLE001
    check(f"parser 배선 검사 예외({e})", False)

# ---------------------------------------------------------------- 6. 상수 단일 진실원
try:
    from fillable_expectancy import FEE_BUY as _fb, FEE_SELL as _fs, TAX_SELL as _ts, LIMIT_UP_PCT as _lu
    check("상수: fillable_expectancy 와 동일",
          (_fb, _fs, _ts, _lu) == (fw.FEE_BUY, fw.FEE_SELL, fw.TAX_SELL, fw.LIMIT_UP_PCT))
except Exception as e:  # noqa: BLE001
    check("상수: fillable_expectancy import(폴백값 사용)", fw.FEE_BUY == 0.00015 and fw.TAX_SELL == 0.0018,
          f"{e}")

print(f"\n=== PASS {len(PASS)} / FAIL {len(FAIL)} ===")
if FAIL:
    for f in FAIL:
        print("  FAIL:", f)
sys.exit(1 if FAIL else 0)

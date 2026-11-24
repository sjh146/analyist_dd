#!/usr/bin/env python3
"""cg472_score_dynamics.py — 챔피언 '점수 레벨' vs '점수 변화율' 의 돈 정보 비교 (읽기 전용).

WHY
  전방 성적표(forward_scorecard)는 **점수 레벨**의 순기대를 잰다: 2026-11-25 실측
  h1 top10 −0.3077%p vs 풀 −0.3131 (Δ +0.0054, t 0.01) · h5 top10 −5.3544 vs 풀 −3.3567 →
  레벨 랭킹은 돈 정보가 없다(quintile top−bottom h1 −0.3032%p, monotone 0.0).
  그렇다면 모델이 담은 정보는 **레벨이 아니라 변화(모멘텀)** 에 있을 수 있다 —
  score_t − score_{t−lag} 로 순위를 매기면 같은 행·같은 체결필터·같은 수수료에서
  레벨 랭킹과 짝 비교가 된다. 이 축은 백로그에 없던 신규 축이다.

WHAT
  ml_predictions(단일 모델버전·(종목,날짜) 최신 1행)와 market_data 일봉을 조인해
  signal-day i 의 순위키를 ①confidence(레벨) ②confidence_i − confidence_{i−lag}(변화)로 두고
  각각 top-k 순기대(%p)·풀평균(널)·Δ·t·분할표본 부호를 낸다.
  체결성 필터·수수료·r 정의는 scripts/forward_scorecard.py 를 그대로 import 해 재사용한다.

판정(사전등록)
  Δ(변화 − 풀) ≥ +0.1%p AND |t| ≥ 2 AND 분할표본 both_positive AND n_dates ≥ 10 → '신호'
  그 외 → '노이즈'(표본 n_dates < 10 이면 '표본부족 — 판정불가', 노이즈로 닫지 않는다)

사용
  cd /home/jhshi/analyist_dd && set -a && . ./.env && set +a
  export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434
  python3 scripts/cg472_score_dynamics.py --horizon 5 --lag 5 --topk 10 \
      --json-out data/reports/overnight/cg472_score_dynamics.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forward_scorecard import fillable_ok, money_stats, _dedupe_latest, _pg_connect  # noqa: E402
from fillable_expectancy import FEE_BUY, FEE_SELL, TAX_SELL, LIMIT_UP_PCT  # noqa: E402


def load(model_version):
    conn = _pg_connect()
    cur = conn.cursor()
    q = ("SELECT stock_code, prediction_date, model_version, confidence, created_at "
         "FROM ml_predictions WHERE confidence IS NOT NULL ")
    if model_version:
        q += "AND model_version = %s "
    q += "ORDER BY 1,2,5"
    cur.execute(q, ((model_version,) if model_version else ()))
    preds = _dedupe_latest(cur.fetchall())
    cur.execute("SELECT stock_code, trade_date, close_price, trading_value FROM market_data "
                "WHERE close_price IS NOT NULL ORDER BY stock_code, trade_date")
    closes, tvals, seq = defaultdict(dict), defaultdict(dict), defaultdict(list)
    for code, dt, px, tv in cur.fetchall():
        d = str(dt)[:10]
        closes[str(code)][d] = float(px)
        tvals[str(code)][d] = float(tv) if tv is not None else None
        seq[str(code)].append(d)
    conn.close()
    conf = {}
    for code, pdate, _ver, c in preds:
        conf[(code, str(pdate)[:10])] = c
    return conf, closes, tvals, seq


def build(conf, closes, tvals, seq, h, lag, max_day_chg, limit_up_pct, min_value):
    """→ per-date 리스트: (lev_key, dkey, r, fillable)."""
    out = defaultdict(list)
    for (code, pdate), c in conf.items():
        days = seq.get(code)
        if not days:
            continue
        if pdate not in days:
            continue
        i = days.index(pdate)
        if i + h >= len(days) or i - lag < 0:
            continue
        c0, c1 = closes[code].get(days[i]), closes[code].get(days[i + h])
        prev = conf.get((code, days[i - lag]))
        if not c0 or c1 is None or prev is None:
            continue
        r = c1 / c0 - 1.0
        ok = fillable_ok(code, days, closes, tvals, i, max_day_chg=max_day_chg,
                         limit_up_pct=limit_up_pct, min_value=min_value)
        out[pdate].append((float(c), float(c) - float(prev), r, ok))
    return out


def rank_stats(by_date, idx, k, fee_rt):
    by_date_top = {d: [(row[idx], row[2], row[3]) for row in rows] for d, rows in by_date.items()}
    by_date_top = {d: v for d, v in by_date_top.items() if v}
    return money_stats(by_date_top, k, fee_rt)


def judge(level, dyn, n_dates):
    d = dyn.get("delta_mean_pct")
    t = dyn.get("delta_t")
    sh = dyn.get("split_half_pct") or [None, None]
    both = all(x is not None and x > 0 for x in sh)
    if n_dates < 10:
        return "표본부족 — 판정불가", {"n_dates": n_dates, "reason": "n_dates<10"}
    if d is not None and d >= 0.1 and t is not None and t >= 2 and both:
        return "신호", {"delta": d, "t": t, "split": sh}
    return "노이즈", {"delta": d, "t": t, "split": sh, "both_positive": both}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=5, help="청산 호라이즌(거래일)")
    ap.add_argument("--lag", type=int, default=5, help="점수 변화 계산 간격(거래일)")
    ap.add_argument("--topk", type=int, default=10)
    ap.add_argument("--model-version", default=None)
    ap.add_argument("--max-day-chg", type=float, default=25.0)
    ap.add_argument("--limit-up-pct", type=float, default=LIMIT_UP_PCT)
    ap.add_argument("--min-value", type=float, default=1e9)
    ap.add_argument("--json-out", default="data/reports/overnight/cg472_score_dynamics.json")
    a = ap.parse_args(argv)
    fee_rt = FEE_BUY + FEE_SELL + TAX_SELL

    conf, closes, tvals, seq = load(a.model_version)
    by_date = build(conf, closes, tvals, seq, a.horizon, a.lag,
                    a.max_day_chg, a.limit_up_pct, a.min_value)
    n_dates = len(by_date)
    level = rank_stats(by_date, 0, a.topk, fee_rt)
    dyn = rank_stats(by_date, 1, a.topk, fee_rt)
    verdict, why = judge(level, dyn, n_dates)

    res = {
        "config": {k: getattr(a, k) for k in
                   ("horizon", "lag", "topk", "model_version", "max_day_chg",
                    "limit_up_pct", "min_value")} | {"fee_rt_pct": round(fee_rt * 100, 4)},
        "n_dates": n_dates,
        "rows": sum(len(v) for v in by_date.values()),
        "level": level,
        "dyn": dyn,
        "dyn_minus_level_pct": (None if (dyn.get("delta_mean_pct") is None or
                                         level.get("delta_mean_pct") is None)
                                else round(dyn["delta_mean_pct"] - level["delta_mean_pct"], 4)),
        "verdict": verdict,
        "why": why,
        "note": ("r = 종가_{i+h}/종가_i − 1 · 체결성 필터(당일등락<25%·거래대금≥10억·자료없음 제외)"
                 " · 수수료 왕복 0.21%p 차감 · 널=같은 세션 풀평균 · 순위키: level=confidence, "
                 "dyn=confidence_i − confidence_{i−lag}. 단일 런·짝 비교."),
    }
    os.makedirs(os.path.dirname(a.json_out) or ".", exist_ok=True)
    with open(a.json_out, "w") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

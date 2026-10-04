#!/usr/bin/env python3
"""_baseline_ranking_money_test.py — baseline_ranking_money 계약 자체점검(DB 불필요, 컨테이너용).

왜: 이 계측기는 fillable_topk_expectancy 의 계산 경로를 **재사용**한다(그래야 모델과 기준 랭킹을
같은 단위로 비교할 수 있다). 경로가 어긋나면 'Δ0 = 효과 없음' 같은 거짓 결론이 나므로, 계약을
합성 데이터로 못박는다. DB·모델 없이 돌아가야 한다(순수 파이썬 + numpy/pandas).

검사
  [1] _ret/_lowvol 은 시점정합(≤ i)만 쓴다 — 미래 종가를 바꿔도 값이 변하지 않는다
  [2] evaluate() 의 k-바스켓 순기대가 F.fillable_topk_expectancy 의 baskets 와 **비트 동일**
  [3] rand 랭킹은 시드 고정 시 결정적, 시드가 다르면 달라진다
  [4] exp 블록 풀링: 같은 (fold,date) 의 서로 다른 exp 가 **한 세션**으로 합쳐진다
  [5] _lowvol 부호: 변동성이 큰 종목의 lowvol 점수가 더 작다(음의 표준편차)

실행: docker exec -e PYTHONPATH=/app stock_xgboost_ml python /app/scripts/_baseline_ranking_money_test.py
"""
from __future__ import annotations

import datetime as dt
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fillable_topk_expectancy as F  # noqa: E402
import baseline_ranking_money as B  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        FAIL.append(name)


def mk_series(codes, days=80, base=10000.0, step=50.0, noise=None):
    """{code: {d,o,c,tv}} — 날짜는 평일 80개."""
    d0 = dt.date(2025, 1, 1)
    dates = []
    d = d0
    while len(dates) < days:
        if d.weekday() < 5:
            dates.append(d)
        d += dt.timedelta(days=1)
    out = {}
    for k, code in enumerate(codes):
        amp = (noise or {}).get(code, 1.0)
        cl = [base + k * 100 + step * i * amp for i in range(days)]
        out[code] = {"d": dates, "o": [c - 1 for c in cl], "c": cl,
                     "tv": [1.2e9] * days}
    return out, dates


def ns(**kw):
    """apply_filters 가 요구하는 Namespace(최소 필드)."""
    base = dict(exclude_limit_up=True, max_day_chg=25.0, min_value=1e9,
                min_price=None, max_price=None, exit="close_h", horizon=5)
    base.update(kw)
    return type("A", (), base)


def main():
    codes = ["000001", "000002", "000003", "000004"]
    series, dates = mk_series(codes)
    ks = [1, 2, 3]
    H, EXIT, RT = 5, "close_h", (F.FEE_BUY + F.FEE_SELL + F.TAX_SELL) * 100.0

    # ---- [1] 시점정합 -------------------------------------------------------
    s = series["000001"]
    i = 40
    r20 = B._ret(s, i, 20)
    lv = B._lowvol(s, i, 20)
    s2 = dict(s)
    s2["c"] = list(s["c"])
    for j in range(i + 1, len(s2["c"])):
        s2["c"][j] = s2["c"][j] * 3.0        # 미래만 바꾼다
    check("[1] _ret 시점정합", B._ret(s2, i, 20) == r20, f"{r20} vs {B._ret(s2, i, 20)}")
    check("[1] _lowvol 시점정합", B._lowvol(s2, i, 20) == lv, f"{lv} vs {B._lowvol(s2, i, 20)}")
    check("[1] lookback 부족이면 None", B._ret(s, 10, 20) is None and B._lowvol(s, 10, 20) is None)

    # ---- [2] 경로 재사용 비트 동일 -----------------------------------------
    rows = []
    for fi, (code, sc) in enumerate(zip(codes, [0.9, 0.1, 0.5, 0.7])):
        rows.append({"exp": "E1", "fold": 1, "date": str(dates[20]), "code": code,
                     "score": sc, "fwd_ret": 0.01 * fi})
    r, ba = B.evaluate("model", rows, series, ns(), ks, RT, {}, {})
    e, _ = F.enrich([dict(x) for x in rows], series, H, EXIT)
    fe, _ = F.apply_filters(e, ns(), True)
    ba_ref = F.baskets(fe, ks, RT)
    same = all(ba[k] == ba_ref[k] for k in ks)
    check("[2] evaluate == fillable_topk_expectancy 경로", same,
          None if same else f"{ba.get(2)} vs {ba_ref.get(2)}")
    check("[2] arm 통계 산출", r["k"]["2"]["arm"].get("n") == 1)

    # ---- [3] rand 결정성 ---------------------------------------------------
    a = B.build_scores(rows, series, ["rand"], seed=0)["rand"]
    b = B.build_scores(rows, series, ["rand"], seed=0)["rand"]
    c = B.build_scores(rows, series, ["rand"], seed=7)["rand"]
    check("[3] rand 결정적", [x["score"] for x in a] == [x["score"] for x in b])
    check("[3] rand 시드별 상이", [x["score"] for x in a] != [x["score"] for x in c])

    # ---- [4] exp 풀링 ------------------------------------------------------
    rows2 = ([{"exp": "E1", "fold": 1, "date": str(dates[20]), "code": cd, "score": 0.5,
               "fwd_ret": 0.0} for cd in codes]
             + [{"exp": "E2", "fold": 1, "date": str(dates[20]), "code": cd, "score": 0.5,
                 "fwd_ret": 0.0} for cd in codes])
    _, ba2 = B.evaluate("model", rows2, series, ns(), ks, RT, {}, {})
    check("[4] 풀링: 2 exp → 1 세션", len(ba2[1]) == 1, f"sessions={len(ba2[1])}")
    _, ba3 = B.evaluate("model", rows2, series, ns(), ks, RT, {},
                        {"E1": 0, "E2": 1000})
    check("[4] --split-exps: 2 세션", len(ba3[1]) == 2, f"sessions={len(ba3[1])}")

    # ---- [5] lowvol 부호 ---------------------------------------------------
    sv, _ = mk_series(["111111", "222222"], noise={"111111": 1.0, "222222": 4.0})
    lo = B._lowvol(sv["111111"], 40, 20)
    hi = B._lowvol(sv["222222"], 40, 20)
    check("[5] 변동성 큰 쪽 lowvol 점수가 작다", lo is not None and hi is not None and lo > hi,
          f"{lo:.6f} vs {hi:.6f}")

    print(f"\n{'ALL PASS' if not FAIL else 'FAILED: ' + ', '.join(FAIL)}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())

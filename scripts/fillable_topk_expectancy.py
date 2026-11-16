#!/usr/bin/env python3
"""fillable_topk_expectancy.py — 라벨/모델 arm 의 **돈 지표**(체결성·수수료 반영 순기대) 측정기.

WHY (실측 배경)
  CG92 는 생산 경로에서 q0.05 라벨 arm 이 q0.30 대조군을 5창 짝 Δ+0.0459(t 3.83)로 이겼고,
  CG94 는 공통 후보집합 top-k 정밀도에서 k=3 Δprec +0.1208(p 0.0054) · k=5 +0.1100(p 9.0e-05) 를 냈다.
  그런데 그 두 측정은 **AUC/정밀도**이고, 이 역할의 최우선 규칙은 "실험은 '돈' 지표로 측정한다"이다.
  게다가 CG93/CG94 의 후보집합은 `--restrict-q` 로 **실현 선행수익의 꼬리**를 골라 만든 것이라
  (예측 시점에 알 수 없는 선택) 그 자체로는 매매 가능한 바스켓이 아니다.
  그래서 여기서는 **전 유니버스 채점 결과**(champion_robust_eval --dump-all)를 받아
    매 세션(폴드·날짜) 마다 필터를 통과한 종목 중 모델 점수 상위 k 만 동일비중 매수 →
    청산가 매도, 수수료·거래세 왕복 차감
  이라는 **실제 소비 정책**을 그대로 시뮬레이션하고, 두 arm 을 **짝(같은 세션)** 으로 비교한다.

체결성 필터(1급 옵션, 기본 ON)
  --exclude-limit-up        당일등락 >= 29.9% (상한가 부근 = 종가에 살 수 없음) 제외
  --max-day-chg FLOAT       당일등락 상한(%): 이 이상 오른 종목 제외
  --min-value FLOAT         거래대금 하한(원): 예 1e9 = 10억
  --min-price FLOAT         종가 하한(원)
  기본값은 `--unfiltered` 로 끄고 나란히 볼 수 있다(필터가 부호를 뒤집는지 확인용).

수수료 모델(scripts/fillable_expectancy.py 와 동일 상수에서 import)
  net(%) = gross(%) − (매수 0.015% + 매도 0.015% + 거래세 0.18%) = 0.21%p

입력 스키마 = champion_robust_eval --dump-preds jsonl
  {"exp","fold","date","code","y_true","y_pred","fwd_ret"}  ← --dump-all 이면 y_true=None 행 포함

사용(컨테이너 안에서 — DB·psycopg2 가 컨테이너에 있다)
  docker exec stock_xgboost_ml sh -c 'cd /app && python scripts/fillable_topk_expectancy.py \
      --arm-jsonl /app/reports/overnight/cg95_q05_all.jsonl --arm-tag cg92_q05 \
      --control-jsonl /app/reports/overnight/cg95_q30_all.jsonl --control-tag cg92_q30 \
      --k 3,5,10 --exit close_h --horizon 5 --json-out /app/reports/overnight/cg95_money.json'
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fillable_expectancy import FEE_BUY, FEE_SELL, TAX_SELL   # noqa: E402  (수수료 단일 진실원)

LIMIT_UP_PCT = 29.9
EXITS = ("next_open", "next_close", "close_h", "open_h1")


def db_connect():
    import psycopg2
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT", 5434)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def load_rows(path, tag):
    """dump jsonl 을 읽어 tag(exp) 로 거른다. y_true 없음(None)도 허용."""
    rows, bad = [], 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                continue
            if tag and r.get("exp") != tag:
                continue
            if r.get("y_pred") is None or r.get("date") is None or r.get("code") is None:
                bad += 1
                continue
            rows.append({"fold": int(r.get("fold") or 0), "date": str(r["date"]),
                         "code": str(r["code"]).zfill(6), "score": float(r["y_pred"]),
                         "fwd_ret": (float(r["fwd_ret"]) if r.get("fwd_ret") is not None else None)})
    return rows, bad


def price_series(conn, codes, dmin, dmax):
    q = ("SELECT stock_code, trade_date, open_price, close_price, trading_value "
         "FROM market_data WHERE stock_code = ANY(%s::text[]) "
         "AND trade_date BETWEEN %s AND %s")
    px = pd.read_sql(q, conn, params=[list(codes), dmin, dmax])
    px["stock_code"] = px["stock_code"].astype(str).str.zfill(6)
    px = px.sort_values(["stock_code", "trade_date"])
    out = {}
    for code, g in px.groupby("stock_code", sort=False):
        out[code] = {
            "d": list(g["trade_date"]), "o": list(g["open_price"]),
            "c": list(g["close_price"]), "tv": list(g["trading_value"]),
        }
    return out


def _f(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def enrich(rows, series, horizon, exit_mode):
    """행마다 종가·전일종가·거래대금·청산가를 붙인다. 계산 불가 행은 버린다."""
    kept, drop = [], 0
    for r in rows:
        s = series.get(r["code"])
        if not s:
            drop += 1
            continue
        try:
            i = s["d"].index(pd.Timestamp(r["date"]).date())
        except ValueError:
            drop += 1
            continue
        close = _f(s["c"][i])
        if not close:
            drop += 1
            continue
        if exit_mode == "next_open":
            j, field = i + 1, "o"
        elif exit_mode == "next_close":
            j, field = i + 1, "c"
        elif exit_mode == "close_h":
            j, field = i + horizon, "c"
        else:                      # open_h1: h일 뒤 다음 거래일 시가
            j, field = i + horizon + 1, "o"
        if j >= len(s["d"]):
            drop += 1
            continue
        exit_px = _f(s[field][j])
        if not exit_px:
            drop += 1
            continue
        prev_close = _f(s["c"][i - 1]) if i > 0 else None
        r = dict(r)
        r["close"] = close
        r["exit_px"] = exit_px
        r["gross_pct"] = (exit_px / close - 1.0) * 100.0
        r["day_chg_pct"] = ((close / prev_close - 1.0) * 100.0
                            if prev_close else None)
        r["trading_value"] = _f(s["tv"][i])
        kept.append(r)
    return kept, drop


def apply_filters(rows, a, on=True):
    if not on:
        return rows, 0
    kept = []
    for r in rows:
        dc = r.get("day_chg_pct")
        if a.exclude_limit_up and dc is not None and dc >= LIMIT_UP_PCT:
            continue
        if a.max_day_chg is not None and dc is not None and dc >= a.max_day_chg:
            continue
        if a.min_value is not None:
            tv = r.get("trading_value")
            if tv is None or tv < a.min_value:
                continue
        if a.min_price is not None and r["close"] < a.min_price:
            continue
        if a.max_price is not None and r["close"] > a.max_price:
            continue
        kept.append(r)
    return kept, len(rows) - len(kept)


def baskets(rows, ks, rt_pct, reverse=False):
    """(fold,date) 세션마다 score 상위 k 동일비중 net(%) → {k: {session_key: net}}.

    reverse=True 면 **하위 k**(모델이 가장 낮게 점수한 k종목) — 점수가 돈 정보를 갖는지
    양끝에서 보는 검사용(2026-10-04 CG98). 기본 False 는 기존 동작(상위 k) 비트 동일.
    """
    by_sess = defaultdict(list)
    for r in rows:
        by_sess[(r["fold"], r["date"])].append(r)
    out = {k: {} for k in ks}
    for key, rs in by_sess.items():
        rs = sorted(rs, key=(lambda x: x["score"]) if reverse else (lambda x: -x["score"]))
        for k in ks:
            pick = rs[:k]
            if not pick:
                continue
            out[k][key] = sum(p["gross_pct"] for p in pick) / len(pick) - rt_pct
    return out


def stride_keys(rows, stride):
    """비중첩 세션 키 집합 — 폴드별로 날짜 정렬 후 stride 칸 간격만 남긴다.

    WHY(CG290 실측 2026-11-15): h5 라벨의 5일 보유창이 세션마다 겹쳐 연속 225세션의
    t 통계가 sqrt(5)≈2.2배 부풀려진다(q0.05 돈 Δ+3.235 t 4.48 → 독립표본 환산 t≈2.0).
    stride=h 로 채점하면 표본은 1/h 로 줄지만 세션 간 보유창이 겹치지 않아 t 가 정직해진다.
    기본 stride=1 은 현행과 비트 동일(행 필터 없음).
    """
    if stride <= 1:
        return None
    per_fold = defaultdict(set)
    for r in rows:
        per_fold[r["fold"]].add(r["date"])
    keep = set()
    for fold, dates in per_fold.items():
        for d in sorted(dates)[::stride]:
            keep.add((fold, d))
    return keep


def pool_series(rows, rt_pct):
    """세션별 **풀 평균**(필터 통과 종목 동일비중) − 수수료 = 무작위 k 바스켓의 기대값.

    WHY(실측 2026-10-04 CG95): 모델 top-k 의 순기대가 양(+)이어도 그게 **시장 베타**인지
    모델 엣지인지 가르는 널 기준선이 없었다 — CG95 는 arm(q0.05) vs control(q0.30) 만 봤고
    둘 다 +2%p/세션 대로 양(+)이었다. 무작위 k 바스켓의 기대값은 정확히 풀 평균이므로,
    `top-k − 풀평균` = 모델이 캡처한 **베타 제거 초과**다(양수여야 '돈이 된다'고 말할 수 있다).
    """
    by_sess = defaultdict(list)
    for r in rows:
        by_sess[(r["fold"], r["date"])].append(r)
    return {key: (sum(x["gross_pct"] for x in rs) / len(rs)) - rt_pct
            for key, rs in by_sess.items() if rs}


def trailing_pool_gate(rows, series, days, min_pct=0.0):
    """진입 시점에 **알 수 있는** 트레일링 풀 모멘텀 게이트 (CG304).

    WHY(2026-11-16 CG302 실측): top-k 초과수익은 국면 무관(상승 Δ+1.795 / 하락 Δ+2.353)인데
    절대 기대는 하락국면 −3.157%p 로 무너진다 → '풀수익 부호'를 사후에 가른 것은 배포 불가다.
    같은 부호를 t 시점 종가까지의 트레일링 풀수익으로 **예측**할 수 있는지만 본다.
    lookahead 없음: t 종가까지의 종가만 쓰고, 진입도 t 종가(enrich 와 동일)다.
    """
    sess = defaultdict(lambda: defaultdict(list))
    for r in rows:
        sess[r["fold"]][r["date"]].append(r["code"])
    close_map = {c: {d: v for d, v in zip(s["d"], s["c"])} for c, s in series.items()}
    out = {}
    for fold, by_date in sess.items():
        ds = sorted(by_date)
        for i in range(days, len(ds)):
            d, d0 = ds[i], ds[i - days]
            dd, dd0 = pd.Timestamp(d).date(), pd.Timestamp(d0).date()
            vals = []
            for c in by_date[d]:
                cm = close_map.get(c)
                if not cm:
                    continue
                p0, p1 = cm.get(dd0), cm.get(dd)
                if p0 and p1 and p0 > 0:
                    vals.append(p1 / p0 - 1.0)
            if len(vals) >= 5:
                out[(fold, d)] = (sum(vals) / len(vals)) * 100.0
    return out


def stat(vals):
    vals = [v for v in vals if v is not None and v == v]
    n = len(vals)
    if not n:
        return {"n": 0}
    mean = sum(vals) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / (n - 1)) if n > 1 else 0.0
    sv = sorted(vals)
    return {"n": n, "mean": round(mean, 4), "median": round(sv[n // 2], 4),
            "sd": round(sd, 4),
            "t": round(mean / (sd / math.sqrt(n)), 2) if sd > 0 else 0.0,
            "pos_pct": round(100 * sum(1 for v in vals if v > 0) / n, 1),
            "worst": round(min(vals), 4), "best": round(max(vals), 4)}


def halves(series_by_key):
    keys = sorted(series_by_key)
    n = len(keys)
    if n < 4:
        return None
    h = n // 2
    front = stat([series_by_key[k] for k in keys[:h]])
    back = stat([series_by_key[k] for k in keys[h:]])
    fp, bp = front.get("mean", 0) > 0, back.get("mean", 0) > 0
    return {"front": front, "back": back,
            "stable": "both_positive" if (fp and bp) else ("neither" if not (fp or bp) else "unstable"),
            "split_session": str(keys[h])}


def paired_stats(arm_by_key, ctl_by_key, rt_pct=None):
    keys = sorted(set(arm_by_key) & set(ctl_by_key))
    diffs, pos, neg, ties = [], 0, 0, 0
    per_fold = defaultdict(lambda: [0, 0])
    for k in keys:
        d = arm_by_key[k] - ctl_by_key[k]
        diffs.append(d)
        if d > 0:
            pos += 1
        elif d < 0:
            neg += 1
        else:
            ties += 1
        fold = k[0] if isinstance(k, tuple) else 0
        per_fold[fold][0] += 1 if d > 0 else 0
        per_fold[fold][1] += 1
    s = stat(diffs)
    if not s.get("n"):
        return {"n_dates": 0}
    s["n_dates"] = s.pop("n")
    s["delta_mean"] = s.pop("mean")
    s["pos"], s["neg"], s["ties"] = pos, neg, ties
    s["folds"] = {str(f): f"{a}/{b}" for f, (a, b) in sorted(per_fold.items())}
    return s


def session_sets(rows):
    """폴드 → 날짜 집합 (덤프 구조 서명)."""
    m = defaultdict(set)
    for r in rows:
        m[r["fold"]].add(r["date"])
    return {k: set(v) for k, v in m.items()}


def pair_plan(arm_rows, ctl_rows):
    """짝 비교 키 결정 — 폴드 번호가 같아도 '같은 평가창'이라는 보장은 없다.

    WHY(2026-11-17 실측, CG318): cg314_preds(8폴드) vs cg297_norank_preds(10폴드) 의
    (fold,date) 교집합은 **8개**뿐이었고 그 8개는 서로 다른 평가창의 우연한 날짜 일치였다
    (arm 160세션·ctl 80세션·날짜 교집합 37). 그런데 그 8세션 짝 Δ 가 CG311 '신호있음'
    (+6.069 t 3.83)과 CG314 '노이즈'(−6.082 t −3.06)의 근거로 보고됐다 → 표본의 5%로
    축을 열고 닫은 셈. 폴드→날짜집합이 **완전히 같을 때만** (fold,date) 로 짝짓고,
    다르면 date 로 짝짓는다(같은 날짜 = 같은 세션, 폴드 번호는 무의미).
    """
    same = session_sets(arm_rows) == session_sets(ctl_rows)
    return ("fold_date" if same else "date"), same


def by_date(series_by_key):
    """{(fold,date): v} → {date: 평균}. 폴드 구조가 다른 두 덤프의 짝 비교용."""
    agg = defaultdict(list)
    for k, v in series_by_key.items():
        d = k[1] if isinstance(k, tuple) else k
        agg[d].append(v)
    return {d: sum(vs) / len(vs) for d, vs in agg.items()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="체결성·수수료 반영 top-k 순기대 (돈 지표)")
    ap.add_argument("--arm-jsonl", required=True)
    ap.add_argument("--arm-tag", default=None)
    ap.add_argument("--control-jsonl", required=True)
    ap.add_argument("--control-tag", default=None)
    ap.add_argument("--k", default="3,5,10")
    ap.add_argument("--sides", default="top",
                    help="top(기본) · bottom 추가 시 반대쪽 k 바스켓도 계산해 `bottom_k` 로 싣는다")
    ap.add_argument("--regime-split", choices=("none", "pool_sign"), default="none",
                    help="pool_sign: 세션별 풀평균 부호로 상승/하락 국면을 갈라 같은 paired 델타를 각각 계산(CG302)")
    ap.add_argument("--gate-rule", choices=("none", "trailing_pool"), default="none",
                    help="trailing_pool: 진입 시점에 알 수 있는 과거 N세션 풀수익(종목 동일비중)이 "
                         "양(+)인 세션에서만 채점 — 사후(pool_sign) 분할을 예측 가능한 규칙으로 바꾼다(CG304). "
                         "기본 none = 기존 경로 비트 동일")
    ap.add_argument("--gate-days", type=int, default=5,
                    help="trailing_pool 게이트의 룩백 세션 수(기본 5 = h5 보유기간과 동일)")
    ap.add_argument("--gate-min-pct", type=float, default=0.0,
                    help="trailing_pool 게이트 문턱(%%). 이 값 초과일 때만 ON (기본 0.0 = 부호)")
    ap.add_argument("--primary-side", choices=("top", "bottom"), default="top",
                    help="k 바스켓을 상위(기본)로 볼지 하위로 볼지 — 하위는 '점수 반전' 가설 검정(CG99)")
    ap.add_argument("--exit", choices=EXITS, default="close_h",
                    help="close_h: 라벨 호라이즌 h 의 종가 매도(기본) · next_open: 익일 시가")
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--exclude-limit-up", action="store_true", default=True)
    ap.add_argument("--no-exclude-limit-up", dest="exclude_limit_up", action="store_false")
    ap.add_argument("--max-day-chg", type=float, default=25.0)
    ap.add_argument("--min-value", type=float, default=1e9)
    ap.add_argument("--min-price", type=float, default=None)
    ap.add_argument("--max-price", type=float, default=None)
    ap.add_argument("--stride", type=int, default=1,
                    help="세션 채점 간격 — h5 처럼 보유창이 겹치는 라벨은 --stride 5 로 "
                         "비중첩 표본을 만든다(기본 1 = 현행 비트 동일)")
    ap.add_argument("--fee-buy", type=float, default=FEE_BUY)
    ap.add_argument("--fee-sell", type=float, default=FEE_SELL)
    ap.add_argument("--tax-sell", type=float, default=TAX_SELL)
    ap.add_argument("--json-out", default=None)
    a = ap.parse_args(argv)
    ks = [int(x) for x in str(a.k).split(",") if x.strip()]
    sides = [s.strip() for s in str(a.sides).split(",") if s.strip()]
    flip = (a.primary_side == "bottom")
    rt_pct = (a.fee_buy + a.fee_sell + a.tax_sell) * 100.0

    arm, bad_a = load_rows(a.arm_jsonl, a.arm_tag)
    ctl, bad_c = load_rows(a.control_jsonl, a.control_tag)
    if not arm or not ctl:
        print(f"[FAIL] 행 없음 arm={len(arm)} control={len(ctl)}", file=sys.stderr)
        return 2
    codes = sorted({r["code"] for r in arm} | {r["code"] for r in ctl})
    dmin = min(r["date"] for r in arm + ctl)
    dmax = max(r["date"] for r in arm + ctl)
    d0 = (pd.Timestamp(dmin) - pd.Timedelta(days=6)).date()
    d1 = (pd.Timestamp(dmax) + pd.Timedelta(days=6 * (a.horizon + 2))).date()
    conn = db_connect()
    series = price_series(conn, codes, d0, d1)

    arm_e, drop_a = enrich(arm, series, a.horizon, a.exit)
    ctl_e, drop_c = enrich(ctl, series, a.horizon, a.exit)
    pair_mode, same_folds = pair_plan(arm_e, ctl_e)

    out = {
        "metric_name": "fillable_topk_expectancy",
        "exit": a.exit, "horizon": a.horizon, "ks": ks,
        "arm_tag": a.arm_tag, "control_tag": a.control_tag,
        "fee_roundtrip_pct": round(rt_pct, 4),
        "stride": a.stride,
        "rows": {"arm": len(arm_e), "control": len(ctl_e),
                 "arm_dropped_price": drop_a, "control_dropped_price": drop_c,
                 "bad_lines": bad_a + bad_c},
        "pair": {
            "mode": pair_mode,
            "same_fold_structure": bool(same_folds),
            "arm_folds": len(session_sets(arm_e)),
            "control_folds": len(session_sets(ctl_e)),
            "date_overlap": len(set(r["date"] for r in arm_e)
                                & set(r["date"] for r in ctl_e)),
            "note": ("폴드→날짜집합 동일 → (fold,date) 짝" if same_folds else
                     "폴드 구조 불일치(다른 평가창) → date 짝. (fold,date) 교집합으로 "
                     "계산한 Δ 는 무효 — CG318 실측 2026-11-17"),
        },
        "conditions": {},
    }
    for cond, on in (("fillable", True), ("unfiltered", False)):
        fa, sk_a = apply_filters(arm_e, a, on)
        fc, sk_c = apply_filters(ctl_e, a, on)
        keep = stride_keys(fa, a.stride)
        if keep is not None:
            fa = [r for r in fa if (r["fold"], r["date"]) in keep]
            fc = [r for r in fc if (r["fold"], r["date"]) in keep]
        ba = baskets(fa, ks, rt_pct, reverse=flip)
        bc = baskets(fc, ks, rt_pct, reverse=flip)
        block = {"filtered_out": {"arm": sk_a, "control": sk_c}, "k": {}}
        # 널 기준선(무작위 k 기대 = 풀 평균). arm 행 기준 — 두 arm 의 필터 통과 집합은
        # 같으므로(같은 dump·같은 종목·날짜) 어느 쪽에서 계산해도 동일하다.
        bpool = pool_series(fa, rt_pct)
        block["baseline_pool"] = {
            "desc": "세션별 풀 평균(필터 통과 종목 동일비중) − 수수료 = 무작위 k 바스켓 기대값",
            "stat": stat(list(bpool.values())), "halves": halves(bpool),
            "paired_by_k": {str(k): paired_stats(ba[k], bpool) for k in ks},
        }
        if ("bottom" in sides) and not flip:
            # 반대쪽(하위) k — 점수의 돈 정보를 양끝에서 본다(CG98).
            bb = baskets(fa, ks, rt_pct, reverse=True)
            block["bottom_k"] = {
                str(k): {"arm": stat(list(bb[k].values())),
                         "paired_vs_pool": paired_stats(bb[k], bpool)} for k in ks}
        if (a.regime_split == "pool_sign") and bpool:
            # 세션별 풀평균 부호로 국면을 가른다 — 모델 엣지가 상승/하락 국면에 의존하는지(CG302).
            reg = {}
            for name, sel in (("up", {kk for kk, v in bpool.items() if v > 0}),
                              ("down", {kk for kk, v in bpool.items() if v <= 0})):
                gp = {kk: v for kk, v in bpool.items() if kk in sel}
                entry = {"n_sessions": len(gp), "pool": stat(list(gp.values())), "k": {}}
                for k in ks:
                    sa = {s: v for s, v in ba[k].items() if s in sel}
                    entry["k"][str(k)] = {"arm": stat(list(sa.values())),
                                          "paired_vs_pool": paired_stats(sa, gp)}
                reg[name] = entry
            block["regime"] = reg
        if (a.gate_rule == "trailing_pool") and bpool:
            # 진입 시점에 알 수 있는 게이트로 세션을 ON/OFF 로 갈라 절대 기대를 본다(CG304).
            tg = trailing_pool_gate(fa, series, a.gate_days, a.gate_min_pct)
            on_keys = {kk for kk, v in tg.items() if v > a.gate_min_pct}
            scored_keys = set(tg) & set(bpool)
            bands = {}
            for name, sel in (("on", on_keys & scored_keys),
                              ("off", scored_keys - on_keys)):
                gp = {kk: v for kk, v in bpool.items() if kk in sel}
                entry = {"n_sessions": len(gp), "pool": stat(list(gp.values())), "k": {}}
                for k in ks:
                    sa = {s: v for s, v in ba[k].items() if s in sel}
                    entry["k"][str(k)] = {"arm": stat(list(sa.values())),
                                          "arm_halves": halves(sa),
                                          "paired_vs_pool": paired_stats(sa, gp)}
                bands[name] = entry
            block["gate"] = {"rule": a.gate_rule, "days": a.gate_days,
                             "min_pct": a.gate_min_pct, "n_scored": len(scored_keys),
                             "n_on": len(on_keys & scored_keys),
                             "n_off_sessions": len(scored_keys - on_keys),
                             "n_unscored": len(set(bpool) - scored_keys),
                             "trailing_pool": stat(list(tg.values())),
                             "bands": bands}
        for k in ks:
            sa = ba[k]
            sc = bc[k]
            if pair_mode == "date":
                # 폴드 구조가 다르면 (fold,date) 교집합은 '우연히 같은 번호+같은 날짜'일 뿐이다.
                pa = paired_stats(by_date(sa), by_date(sc))
                legacy = paired_stats(sa, sc)
            else:
                pa = paired_stats(sa, sc)
                legacy = None
            ref = min(len(by_date(sa)), len(by_date(sc)))
            pa["expected_pairs"] = ref
            if ref and pa.get("n_dates", 0) < 0.5 * ref:
                pa["invalid"] = (f"짝 교집합 {pa.get('n_dates', 0)}/{ref} 세션 — "
                                 "다른 평가창 의심, 판정 불가")
            block["k"][str(k)] = {
                "arm": stat(list(sa.values())),
                "control": stat(list(sc.values())),
                "arm_halves": halves(sa),
                "control_halves": halves(sc),
                "paired": pa,
                **({"paired_fold_date_legacy": legacy} if legacy is not None else {}),
            }
        out["conditions"][cond] = block

    # 세션당 후보 풀 크기(필터 ON) 중앙값 — k 포화 여부 판단용
    fa, _ = apply_filters(arm_e, a, True)
    pools = defaultdict(int)
    for r in fa:
        pools[(r["fold"], r["date"])] += 1
    out["pool_median_fillable"] = int(np.median(list(pools.values()))) if pools else 0
    out["n_sessions_fillable"] = len(pools)

    print(f"exit={a.exit} h={a.horizon} 필터 ON: 세션 {out['n_sessions_fillable']} · "
          f"풀 중앙값 {out['pool_median_fillable']} · 수수료 왕복 {rt_pct:.3f}%p")
    hdr = f"{'k':>3} {'arm net%':>9} {'ctl net%':>9} {'Δ':>8} {'t':>6} {'pos/neg/ty':>11} {'arm 양세션%':>10}"
    for cond in ("fillable", "unfiltered"):
        print(f"--- {cond} ---")
        print(hdr)
        for k in ks:
            b = out["conditions"][cond]["k"][str(k)]
            pa = b["paired"]
            print(f"{k:>3} {b['arm'].get('mean', float('nan')):>9.3f} "
                  f"{b['control'].get('mean', float('nan')):>9.3f} "
                  f"{pa.get('delta_mean', float('nan')):>8.3f} {pa.get('t', float('nan')):>6.2f} "
                  f"{str(pa.get('pos'))+'/'+str(pa.get('neg'))+'/'+str(pa.get('ties')):>11} "
                  f"{b['arm'].get('pos_pct', float('nan')):>10.1f}")
        bp = out["conditions"][cond]["baseline_pool"]
        bs = bp["stat"]
        print(f"  [널] 풀 평균(무작위 k 기대) {bs.get('mean', float('nan')):+.3f}%p/세션 "
              f"(n={bs.get('n')} · t {bs.get('t')} · 양세션 {bs.get('pos_pct')}%)")
        for k in ks:
            pk = bp["paired_by_k"][str(k)]
            print(f"    k={k:>2} Δ({a.primary_side}-k − 풀평균) {pk.get('delta_mean', float('nan')):+.3f} "
                  f"(t {pk.get('t')} · pos/neg/ty {pk.get('pos')}/{pk.get('neg')}/{pk.get('ties')})")
        cb = out["conditions"][cond]
        if "bottom_k" in cb:
            print("  [반대쪽 k] 풀 평균 대비")
            for k in ks:
                bk = cb["bottom_k"][str(k)]
                pv = bk["paired_vs_pool"]
                print(f"    k={k:>2} arm {bk['arm'].get('mean', float('nan')):+.3f}%p/세션 "
                      f"· Δ(pool) {pv.get('delta_mean', float('nan')):+.3f} (t {pv.get('t')})")
        if "regime" in cb:
            print("  [국면분해] 세션별 풀평균 부호")
            for name, lbl in (("up", "상승"), ("down", "하락")):
                rg = cb["regime"][name]
                rs = rg["pool"]
                print(f"    {lbl}: 세션 {rg['n_sessions']} · 풀 {rs.get('mean', float('nan')):+.3f}%p (t {rs.get('t')})")
                for k in ks:
                    rk = rg["k"][str(k)]
                    rp = rk["paired_vs_pool"]
                    print(f"      k={k:>2} top {rk['arm'].get('mean', float('nan')):+.3f}%p "
                          f"· Δ(pool) {rp.get('delta_mean', float('nan')):+.3f} (t {rp.get('t')})")
        if "gate" in cb:
            gt = cb["gate"]
            print(f"  [예측 게이트] {gt['rule']} days={gt['days']} 문턱{gt['min_pct']:+.2f}% "
                  f"→ ON {gt['n_on']}세션 / OFF {gt['n_off_sessions']}세션 (채점 {gt['n_scored']})")
            for name, lbl in (("on", "게이트 ON"), ("off", "게이트 OFF")):
                gb = gt["bands"][name]
                gs = gb["pool"]
                print(f"    {lbl}: 세션 {gb['n_sessions']} · 풀 {gs.get('mean', float('nan')):+.3f}%p (t {gs.get('t')})")
                for k in ks:
                    gk = gb["k"][str(k)]
                    ar = gk["arm"]
                    hh = gk["arm_halves"] or {}
                    print(f"      k={k:>2} top {ar.get('mean', float('nan')):+.3f}%p (t {ar.get('t')}) "
                          f"· 분할 {hh.get('stable')} [{ (hh.get('front') or {}).get('mean') }/{(hh.get('back') or {}).get('mean')} ]")
    if a.json_out:
        os.makedirs(os.path.dirname(a.json_out) or ".", exist_ok=True)
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"[ok] {a.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

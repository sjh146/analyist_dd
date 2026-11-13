#!/usr/bin/env python3
"""_cg240_label_excess_test — CG240 '초과수익 라벨' 축의 자기검정(2026-11-13 엔지니어 자율).

무엇을 검증하나
  ① 기존 kind 는 **비트 동일**(회귀 무변경): quantile/smooth/relative/voladj/abs_thresh 를
     변경 전 구현(이 파일의 ref_make_labels, 패치 전 본문 복사)과 실패널에서 비교한다.
  ② 'demean + 분위' 는 **no-op** 임을 수치로 확인: 날짜별 상수 이동은 날짜 내 순서를 보존하므로
     excess_smooth 라벨 == smooth 라벨(비트 동일). → 이 조합을 실험 arm 으로 쓰면 무의미하다.
  ③ excess_smooth_thresh(=당일 풀평균 초과 이진)는 실제로 달라진다: 양성률·행 도메인·라벨 일치율.
  ④ 누수 없음(구조): 초과 라벨은 **같은 날 행들의 선행수익 평균**만 쓴다 — 그날 이후 정보를
     라벨에 넣지 않는다(demean 상수는 같은 날 값으로만 계산). 합성 데이터에서 한 종목의 미래가격을
     바꾸면 **그 종목의 라벨만** 바뀌고 다른 종목 라벨은 그대로인지 확인(단, 같은 날 평균이
     바뀌므로 다른 종목 라벨도 바뀔 수 있다 → 이는 '라벨 정의상 정상'이며 피처 누수가 아니다.
     여기서는 '라벨이 그날 이후 가격만 본다'는 것만 확인한다: 마지막 날 라벨은 전부 NaN).

실행(컨테이너):
  docker exec stock_xgboost_ml sh -c 'cd /app && python -u scripts/_cg240_label_excess_test.py \
      --panel /app/app/models/wf/panel_200liq_fresh.npz'
"""
import argparse
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "/app/scripts")
import wf_wave as W  # noqa: E402


def ref_make_labels(df, kind, horizon, q, thresh=None):
    """패치 **전** make_labels 본문(회귀 비교 기준). 절대 수정하지 말 것."""
    price = df.groupby("stock_code", sort=False)["price"]
    if kind in ("smooth", "voladj_smooth"):
        acc = None
        for k in range(1, horizon + 1):
            r = price.transform(lambda s, _k=k: s.shift(-_k) / s - 1.0)
            acc = r if acc is None else acc + r
        ret = acc / float(horizon)
    else:
        ret = price.transform(lambda s: s.shift(-horizon) / s - 1.0)
    if kind in ("voladj", "voladj_smooth"):
        vol = price.transform(lambda s: s.pct_change().rolling(20, min_periods=10).std())
        ret = ret / vol.replace(0.0, np.nan)
    day = df["date"]
    if kind == "abs_thresh":
        r = np.asarray(ret, dtype=float)
        return np.where(np.isnan(r), np.nan, np.where(r >= float(thresh), 1.0, 0.0))
    if kind == "relative":
        med = ret.groupby(day).transform("median")
        y = (ret > med).astype(float)
        y[ret.isna()] = np.nan
        return y.values
    hi = ret.groupby(day).transform(lambda s: s.quantile(1 - q))
    lo = ret.groupby(day).transform(lambda s: s.quantile(q))
    y = pd.Series(np.nan, index=df.index, dtype=float)
    y[ret > hi] = 1.0
    y[ret < lo] = 0.0
    return y.values


def same(a, b):
    """NaN 위치까지 같은지(비트 동일) — 둘 다 float 배열."""
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    if a.shape != b.shape:
        return False, f"shape {a.shape} vs {b.shape}"
    na, nb = np.isnan(a), np.isnan(b)
    if not np.array_equal(na, nb):
        return False, f"NaN 위치 불일치 {int((na != nb).sum())}행"
    m = ~na
    if not np.array_equal(a[m], b[m]):
        bad = int((a[m] != b[m]).sum())
        return False, f"값 불일치 {bad}행"
    return True, f"동일({int(m.sum())}행 유효)"


def synthetic(n_codes=6, n_days=40, seed=7):
    """합성 패널 — 종목별로 다른 일간수익 + 특정 날 '전 종목 동일수익' 날을 넣는다."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2026-01-01", periods=n_days, freq="B").strftime("%Y-%m-%d")
    rows = []
    for c in range(n_codes):
        px = 1000.0 * (1 + c)
        for i, d in enumerate(dates):
            if i > 0:
                # 30번째 날은 전 종목 같은 수익률(+3%) → 횡단면 신호 0
                if i == 30:
                    r = 0.03
                else:
                    r = float(rng.normal(0.0, 0.02))
                px = px * (1 + r)
            rows.append({"stock_code": f"C{c:02d}", "date": d, "price": px})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default="/app/app/models/wf/panel_200liq_fresh.npz")
    ap.add_argument("--folds", type=int, default=5)
    args = ap.parse_args()
    fails = 0

    def check(name, ok, detail=""):
        nonlocal fails
        print(f"[{'PASS' if ok else 'FAIL'}] {name} — {detail}", flush=True)
        if not ok:
            fails += 1

    # ── ①② 합성 패널: 비트 동일 회귀 + no-op 확인 ────────────────────────────────
    df = synthetic()
    for kind, kw in (("quantile", {}), ("smooth", {}), ("relative", {}),
                     ("voladj", {}), ("abs_thresh", {"thresh": 0.02})):
        new = W.make_labels(df, kind, 5, 0.30, **kw)
        old = ref_make_labels(df, kind, 5, 0.30, **kw)
        check(f"회귀 무변경({kind})", *same(new, old))

    y_sm = W.make_labels(df, "smooth", 5, 0.30)
    y_ex = W.make_labels(df, "excess_smooth", 5, 0.30)
    check("'demean+분위'는 no-op (excess_smooth == smooth)", *same(y_ex, y_sm))

    y_th = W.make_labels(df, "excess_smooth_thresh", 5, 0.30, thresh=0.0)
    valid = ~np.isnan(y_th)
    rate = float(np.nanmean(y_th))
    check("excess_smooth_thresh 양성률 0<x<1", 0.0 < rate < 1.0, f"양성률 {rate:.4f} · 유효 {int(valid.sum())}행")
    # '전 종목이 같은 수익률'인 미니 패널: 초과수익이 정확히 0 이므로 라벨은 전부 1(>=0) 이어야 한다.
    # (스무딩 라벨로는 h>1 구간에 다른 날이 섞여 0 이 되지 않으므로 horizon=1·excess_thresh 로 검정한다.)
    rng = np.random.default_rng(11)
    mini = []
    for c in range(4):
        px = 1000.0
        for i, d in enumerate(pd.date_range("2026-01-01", periods=8, freq="B").strftime("%Y-%m-%d")):
            if i > 0:
                px *= 1.02  # 전 종목 동일 일간수익 → 횡단면 초과 = 0
            mini.append({"stock_code": f"M{c}", "date": d, "price": px})
    mini = pd.DataFrame(mini)
    y_mini = W.make_labels(mini, "excess_thresh", 1, 0.30, thresh=0.0)[~np.isnan(W.make_labels(mini, "excess_thresh", 1, 0.30, thresh=0.0))]
    check("전 종목 동일수익 → 초과 라벨 전부 1(초과 0 >= 0)", bool(np.all(y_mini == 1.0)),
          f"{int(len(y_mini))}행 · 양성률 {float(np.mean(y_mini)):.3f}")
    # abs 와 라벨 일치율(다르다는 것 = 새 축이라는 것)
    y_abs = W.make_labels(df, "abs_thresh", 5, 0.30, thresh=0.0)
    both = (~np.isnan(y_abs)) & (~np.isnan(y_th))
    agree = float((y_abs[both] == y_th[both]).mean()) if both.any() else float("nan")
    check("abs_thresh(thresh 0) 와 다른 축", agree < 0.999, f"일치율 {agree:.4f} (유효 {int(both.sum())}행)")

    # ── 마지막 날 라벨은 전부 NaN(앞만 본다) ────────────────────────────────────
    last = df["date"].max()
    mlast = (df["date"] == last).values
    check("마지막 날 라벨 전부 NaN", bool(np.isnan(W.make_labels(df, "excess_thresh", 5, 0.30, thresh=0.0)[mlast]).all()))

    # ── ③④ 실패널: 회귀 + 초과 라벨 실측 ────────────────────────────────────────
    try:
        pj, names = W.build_panel(args.panel, 0, 0, log=lambda *a, **k: None)
        pj["date"] = pj["date"].astype(str)
        print(f"[info] 실패널 {args.panel}: rows={len(pj)} codes={pj['stock_code'].nunique()} "
              f"dates={pj['date'].nunique()} ({pj['date'].min()}~{pj['date'].max()})", flush=True)
        for kind, kw in (("quantile", {}), ("smooth", {}), ("abs_thresh", {"thresh": 0.02})):
            new = W.make_labels(pj, kind, 5, 0.30, **kw)
            old = ref_make_labels(pj, kind, 5, 0.30, **kw)
            check(f"실패널 회귀 무변경({kind})", *same(new, old))
        ys = W.make_labels(pj, "smooth", 5, 0.30)
        ye = W.make_labels(pj, "excess_smooth", 5, 0.30)
        check("실패널 no-op 확인(excess_smooth == smooth)", *same(ye, ys))
        yth = W.make_labels(pj, "excess_smooth_thresh", 5, 0.30, thresh=0.0)
        ok = ~np.isnan(yth)
        rate = float(np.nanmean(yth))
        print(f"[info] 실패널 초과라벨: 유효 {int(ok.sum())}/{len(yth)}행 양성률 {rate:.4f} "
              f"(대조 smooth 라벨 유효 {int((~np.isnan(ys)).sum())}행)", flush=True)
        # 날짜별 양성률 분포(국면 의존성 — 절대문턱 라벨의 특성)
        g = pd.DataFrame({"d": pj["date"], "y": yth}).dropna().groupby("d")["y"].mean()
        if len(g):
            print(f"[info] 날짜별 양성률: mean {g.mean():.4f} · std {g.std():.4f} · "
                  f"min {g.min():.4f} · max {g.max():.4f} (n_dates={len(g)})", flush=True)
        check("실패널 초과라벨 양성률 (0.05, 0.95)", 0.05 < rate < 0.95, f"{rate:.4f}")
    except Exception as exc:  # 패널 없음/락 등은 검정 불가로 보고(실패 아님)
        print(f"[WARN] 실패널 검정 건너뜀: {type(exc).__name__}: {exc}", flush=True)

    print(f"\n결과: {'전부 PASS' if fails == 0 else f'{fails}건 FAIL'}", flush=True)
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

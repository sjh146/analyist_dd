#!/usr/bin/env python3
"""CG241 준비 — 패널에 'as-of 전방채움(ffill)' 파생 컬럼 5종을 **가산만** 한다.

왜(실측 근거, 2026-11-13 엔지니어):
  신선 패널(panel_200liq_fresh.npz, 213피처·200종목·279일)의 SNS/감성 피처는 **죽지 않았는데**
  관측 커버리지가 낮다 — sns_sentiment_score 관측 11.0% · sns_attention_score 12.2% ·
  sns_momentum_score 12.1% · short_selling_ratio 31.9% (관측된 값 자체는 3~5천 유니크로 살아 있음).
  즉 문제는 '값이 없다'(CG60/L3 의 사망 진단)가 아니라 **관측일이 드물다**이다.
  트리는 결측 행에서 그 피처를 못 쓰므로, 신호가 있어도 88% 행에서 사라진다.
  표준 처방은 as-of 전방채움(마지막 관측값 + 경과일 지표)이다 — 오늘 값의 부재를
  '정보 없음'이 아니라 '직전 관측이 n일 전'으로 표현한다.

누수 방어(하드):
  - 채움은 **종목별 시간순 전방(ffill)** 만 쓴다 → t 시점 값은 t 이하 관측값뿐이다.
  - 경과 거래일 상한(기본 5)을 넘으면 채우지 않고 **NaN(결측)** 으로 되돌린다 — 오래된 값을
    '오늘 값'으로 쓰지 않는다. 결측 표식은 패널 규약대로 NaN 이다(0 이 아니다).
  - 원본 213열은 **비트 동일**하게 유지한다(가산만). 자기검정이 이를 확인한다.

사용:
  python3 scripts/_cg239_ffill_panel.py            # 빌드 + 자기검정
  python3 scripts/_cg239_ffill_panel.py --test     # 자기검정만
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

SRC = "/app/app/models/wf/panel_200liq_fresh.npz"
OUT = "/app/app/models/wf/panel_200liq_ffill.npz"

# 채울 소스(관측이 드문 시간가변 피처). 결측은 NaN 으로 표기돼 있다.
FFILL_SRC = ["sns_sentiment_score", "sns_attention_score", "sns_momentum_score"]
STALE_COL = "ffill_sns_stale_days"      # sns_sentiment_score 기준 경과 거래일(관측일=0, 상한 10)
CAP = 5                                 # 채움 최대 경과 거래일


def _staleness(df: pd.DataFrame, col: str) -> pd.Series:
    """종목별 시간순 '마지막 관측 이후 경과 행 수'. 관측 행 = 0, 미관측 선두 = 큰 값."""
    obs = np.isfinite(df[col].to_numpy(dtype=float))
    out = np.full(len(df), 10 ** 6, dtype=float)
    last = -1
    for i in range(len(df)):
        if obs[i]:
            last = i
            out[i] = 0
        elif last >= 0:
            out[i] = i - last
    return pd.Series(out, index=df.index)


def build(src: str = SRC, out: str = OUT, cap: int = CAP) -> dict:
    z = np.load(src, allow_pickle=True)
    X = np.asarray(z["X"], dtype=np.float32)
    names = [str(x) for x in z["feature_names"]]
    dates = np.asarray(z["dates"]).astype(str)
    codes = np.asarray(z["codes"]).astype(str)
    price = np.asarray(z["price"], dtype=float) if "price" in z else None

    df = pd.DataFrame({"date": dates, "stock_code": codes})
    for c in FFILL_SRC:
        if c not in names:
            raise SystemExit(f"소스 피처 없음: {c}")
        df[c] = X[:, names.index(c)].astype(float)

    srt = df.sort_values(["stock_code", "date"], kind="mergesort")
    new_cols, meta = {}, {}
    for c in FFILL_SRC:
        st = _staleness(srt, c)
        filled = srt[c].ffill()
        # 상한 초과 = '정보 없음' → NaN 으로 되돌린다(패널 규약과 동일: 0 이 아니라 결측).
        filled = filled.where(st <= cap)
        new_cols[f"ffill_{c}"] = filled
        meta[c] = {"observed_before": round(float(np.isfinite(srt[c]).mean()), 4),
                   "observed_after": round(float(np.isfinite(filled).mean()), 4)}
    st_main = _staleness(srt, "sns_sentiment_score")
    new_cols[STALE_COL] = st_main.where(st_main < 10 ** 5, 10).clip(upper=10).astype(float)

    # 원래 행 순서로 되돌린다(X 행 순서와 1:1)
    idx_sorted = srt.index.to_numpy()
    pos = np.empty(len(df), dtype=int)
    pos[idx_sorted] = np.arange(len(df))
    add = np.column_stack([np.asarray(v, dtype=float)[pos] for v in new_cols.values()]).astype(np.float32)
    add_names = list(new_cols.keys())

    X2 = np.hstack([X, add])
    np.savez_compressed(out, X=X2, feature_names=np.array(names + add_names, dtype=object),
                        dates=dates, codes=codes, **({"price": price} if price is not None else {}))
    return {"src": src, "out": out, "n_rows": int(X.shape[0]), "n_feat_before": len(names),
            "n_feat_after": len(add_names) + len(names), "added": add_names,
            "coverage": meta, "mtime_src": os.path.getmtime(src)}


def _load(p):
    z = np.load(p, allow_pickle=True)
    return (np.asarray(z["X"], dtype=np.float32), [str(x) for x in z["feature_names"]],
            np.asarray(z["dates"]).astype(str), np.asarray(z["codes"]).astype(str))


def selftest(src: str = SRC, out: str = OUT, cap: int = CAP, n_probe: int = 150) -> int:
    checks, fails = [], 0

    def ck(name, ok, detail=""):
        nonlocal fails
        checks.append({"check": name, "pass": bool(ok), "detail": detail})
        if not ok:
            fails += 1

    Xa, na, da, ca = _load(src)
    Xb, nb, db, cb = _load(out)
    # ① 원본 열 비트 동일(NaN 위치 포함) · 행/날짜/코드 동일 · 열 수 증가
    orig_ok = np.array_equal(np.nan_to_num(Xa, nan=-9e9), np.nan_to_num(Xb[:, :Xa.shape[1]], nan=-9e9))
    ck("원본 213열 비트 동일(NaN 위치 포함)", orig_ok)
    ck("행/날짜/코드 동일", len(da) == len(db) and np.array_equal(da, db) and np.array_equal(ca, cb))
    ck("신규 열 5개", len(nb) - len(na) == len(FFILL_SRC) + 1, f"{len(na)}→{len(nb)}")
    ck("원본 이름 보존", nb[:len(na)] == na)

    # ② 누수 없음: 무작위 표본에서 채움값 == '그 날짜 이하 마지막 관측값'(독립 재계산)
    rng = np.random.default_rng(7)
    bad = 0
    for c in FFILL_SRC:
        raw = Xa[:, na.index(c)].astype(float)
        got = Xb[:, nb.index("ffill_" + c)].astype(float)
        rdf = pd.DataFrame({"d": da, "c": ca, "raw": raw})
        srt = rdf.sort_values(["c", "d"], kind="mergesort")
        picks = rng.choice(len(srt), size=min(n_probe, len(srt)), replace=False)
        for k in picks:
            code, day = srt["c"].iloc[k], srt["d"].iloc[k]
            m = (srt["c"] == code).values & (srt["d"] <= day).values
            hist = srt.loc[m].sort_values("d")
            finite = np.isfinite(hist["raw"].to_numpy())
            if not finite.any():
                expect = np.nan
            else:
                last_i = int(np.where(finite)[0][-1])
                stale = len(hist) - 1 - last_i
                expect = hist["raw"].to_numpy()[last_i] if stale <= cap else np.nan
            g = got[srt.index[k]]
            same = (np.isnan(expect) and np.isnan(g)) or (
                np.isfinite(expect) and np.isfinite(g) and abs(g - expect) <= 1e-6)
            if not same:
                bad += 1
    ck("ffill 누수 없음(독립 재계산 일치)", bad == 0, f"불일치 {bad}건")

    # ③ 관측 커버리지 상승
    cov = {}
    for c in FFILL_SRC:
        before = float(np.isfinite(Xa[:, na.index(c)]).mean())
        after = float(np.isfinite(Xb[:, nb.index("ffill_" + c)]).mean())
        cov[c] = [round(before, 4), round(after, 4)]
        ck(f"관측 커버리지 상승 {c}", after > before, f"{before:.3f}→{after:.3f}")
    # ④ 경과일 지표가 유한·범위 내
    st = Xb[:, nb.index(STALE_COL)]
    ck(f"{STALE_COL} 범위 0~10", bool(np.isfinite(st).all()) and float(st.min()) >= 0 and float(st.max()) <= 10,
       f"[{float(st.min())},{float(st.max())}]")

    print(json.dumps({"pass": fails == 0, "fails": fails, "checks": checks, "coverage": cov},
                     ensure_ascii=False, indent=1))
    return 0 if fails == 0 else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=SRC)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--cap", type=int, default=CAP)
    ap.add_argument("--test", action="store_true")
    a = ap.parse_args()
    if not a.test:
        print(json.dumps(build(a.src, a.out, a.cap), ensure_ascii=False, indent=1))
    sys.exit(selftest(a.src, a.out, a.cap))


if __name__ == "__main__":
    main()

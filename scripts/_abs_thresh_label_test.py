#!/usr/bin/env python3
"""CG107 셀프체크: wf_wave.make_labels(kind='abs_thresh') — 절대 임계 라벨.

검사 항목(전부 순수 파이썬 — 이 스택엔 pytest 가 없다):
  1. 경계값: ret == thresh → 1 · ret < thresh → 0
  2. 결측: 종목별 마지막 h 행(선행수익 불가)은 NaN 유지
  3. 도메인: abs_thresh 는 quantile 과 달리 **중간 분위를 버리지 않는다**(비결측 행 수가 더 많다)
  4. 국면 적응(기제): 상승일의 양성률 > 하락일의 양성률(같은 임계·다른 날)
  5. 시점정합: 행 t 의 라벨은 t+h 가격만 참조한다(가격 한 점을 바꾸면 그 행의 라벨만 바뀐다)
  6. thresh 미지정 → RuntimeError(조용한 폴백 금지)
  7. 회귀: quantile 경로는 thresh 인자와 무관하게 종전과 동일(원소 단위)
  8. 회귀: relative 경로도 thresh 인자에 영향받지 않는다
"""
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")
import wf_wave as W  # noqa: E402

FAIL = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    if not cond:
        FAIL.append(name)


def synth(up=True, n_dates=30, n_stocks=3):
    """합성 가격: 상승일이면 매일 +2% 씩, 하락일이면 매일 -2% 씩(결정적)."""
    step = 1.02 if up else 0.98
    rows = []
    for s in range(n_stocks):
        p = 10000.0 * (1 + 0.01 * s)
        for d in range(n_dates):
            rows.append({"stock_code": f"S{s}", "date": f"2026-01-{d + 1:02d}", "price": p})
            p *= step
    return pd.DataFrame(rows)


H = 5
TH = 0.02

# ── 1·2·3·5: 절대 임계 라벨의 기본 성질 ───────────────────────────────────────
df = synth(up=True, n_dates=30, n_stocks=3)
y = np.asarray(W.make_labels(df, "abs_thresh", H, 0.30, thresh=TH), dtype=float)
g = df.groupby("stock_code", sort=False)["price"]
ret = g.transform(lambda s: s.shift(-H) / s - 1.0).values

# 1. 경계: ret >= TH → 1, ret < TH → 0 (결측 제외)
ok = np.all((y[np.isfinite(ret)] == 1.0) == (ret[np.isfinite(ret)] >= TH))
check("1. 경계값(ret>=thresh → 1, 미만 → 0)", bool(ok))

# 2. 결측: 종목별 마지막 H 행
nan_mask = ~np.isfinite(ret)
check("2. 선행수익 불가 행은 NaN", bool(np.all(np.isnan(y[nan_mask]))))
check("2b. NaN 행 수 == 종목수*H", int(nan_mask.sum()) == 3 * H,
      f"nan={int(nan_mask.sum())}")

# 3. 도메인: quantile 은 양쪽 꼬리만 남기므로 비결측 표본이 절반 이하
yq = np.asarray(W.make_labels(df, "quantile", H, 0.30), dtype=float)
n_abs = int(np.isfinite(y).sum())
n_q = int(np.isfinite(yq).sum())
check("3. abs_thresh 가 중간 분위를 버리지 않는다(표본 > quantile)", n_abs > n_q,
      f"abs={n_abs} quantile={n_q}")

# 5. 시점정합: 한 (종목·날짜) 의 가격을 바꾸면 그 행의 라벨만 영향받아야 한다.
#    t 행 라벨은 가격(t) 과 가격(t+H) 로만 결정된다. 가격(t) 을 바꾸면 t 행은 바뀌고
#    t-H..t-1 행도(그들이 t 를 선행가격으로 참조) 바뀔 수 있다 → 't+H 만' 바꾸면
#    정확히 t 행 하나만 바뀐다(그것이 누수 없음의 지문).
df2 = df.copy()
i_target = 10          # S0 의 11번째 행
df2.loc[i_target, "price"] = df2.loc[i_target, "price"] * 1.5
y2 = np.asarray(W.make_labels(df2, "abs_thresh", H, 0.30, thresh=TH), dtype=float)
# NaN 인식 비교(NaN != NaN 이라 단순 != 로는 결측 행이 전부 '변경'으로 잡힌다 — 실측 함정).
same = (y == y2) | (np.isnan(y) & np.isnan(y2))
diff = np.where(~same)[0]
check("5. 시점정합: t+h 가격 변경 시 t 행만 변한다", set(diff.tolist()) == {i_target},
      f"changed={diff.tolist()}")

# ── 4. 국면 적응(기제): 상승일 양성률 > 하락일 양성률 ─────────────────────────
up = synth(up=True, n_dates=30, n_stocks=5)
dn = synth(up=False, n_dates=30, n_stocks=5)
pr_up = np.nanmean(np.asarray(W.make_labels(up, "abs_thresh", H, 0.30, thresh=TH), dtype=float))
pr_dn = np.nanmean(np.asarray(W.make_labels(dn, "abs_thresh", H, 0.30, thresh=TH), dtype=float))
check("4. 국면 적응: 상승일 양성률 > 하락일 양성률", pr_up > pr_dn,
      f"up={pr_up:.3f} down={pr_dn:.3f}")

# ── 6. thresh 미지정 → 즉시 실패(조용한 폴백 금지) ────────────────────────────
try:
    W.make_labels(df, "abs_thresh", H, 0.30)
    check("6. thresh 미지정 → RuntimeError", False, "예외 없음")
except RuntimeError as e:
    check("6. thresh 미지정 → RuntimeError", "thresh" in str(e), str(e)[:40])

# ── 7·8. 회귀: 기존 경로는 thresh 인자에 영향받지 않는다 ─────────────────────
yq2 = np.asarray(W.make_labels(df, "quantile", H, 0.30, thresh=0.02), dtype=float)
check("7. quantile 경로는 thresh 인자와 무관(원소 단위 동일)",
      bool(np.array_equal(np.nan_to_num(yq, nan=-9), np.nan_to_num(yq2, nan=-9))))
yr = np.asarray(W.make_labels(df, "relative", H, 0.30), dtype=float)
yr2 = np.asarray(W.make_labels(df, "relative", H, 0.30, thresh=0.02), dtype=float)
check("8. relative 경로도 thresh 인자와 무관",
      bool(np.array_equal(np.nan_to_num(yr, nan=-9), np.nan_to_num(yr2, nan=-9))))

print()
if FAIL:
    print(f"RESULT: FAIL ({len(FAIL)}건) — " + ", ".join(FAIL))
    sys.exit(1)
print("RESULT: ALL PASS")
sys.exit(0)

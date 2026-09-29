#!/usr/bin/env python3
"""검증: zscore_ts 변환의 무누수·형태 보존 (2026-09-29, CG32 셋업).

왜: 종목별 시계열 정규화는 '자기 과거'만 써야 한다. 미래 행이 과거 행의 출력을 바꾸면
그 자체가 누수다(패널 전체에 변환을 적용하므로 미래 정보가 학습행에 스며든다). 합성
데이터로 ①형태/컬럼 보존 ②미래 행 변조가 과거 출력에 무영향 ③시프트 기준(당일 값이
자기 통계에 들어가지 않음) ④초기 구간 0 채움 ⑤ 종목 간 독립(다른 종목 값 변조 무영향)
을 확인한다.
"""
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "/app/scripts")
from wf_label_sweep import transform_matrix  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f" — {extra}" if extra else ""))
    if not cond:
        fails.append(name)


rng = np.random.default_rng(0)
codes = ["A"] * 30 + ["B"] * 30
dates = [f"2026-01-{i + 1:02d}" for i in range(30)] * 2
X = pd.DataFrame({"f1": rng.normal(size=60), "f2": rng.normal(size=60)})
Z = transform_matrix(X, dates, "zscore_ts", codes=codes)
check("형태 보존", Z.shape == (60, 2), f"{Z.shape}")
check("유한값", np.isfinite(Z).all())
check("초기 10행 0 채움(과거 부족)", np.allclose(Z[:10], 0.0), str(np.abs(Z[:10]).max()))
check("이후 구간 비영(변환 작동)", np.abs(Z[10:]).mean() > 0.1, f"{np.abs(Z[10:]).mean():.3f}")

# 미래 행 변조 → 과거 출력 불변 (누수 반증)
X2 = X.copy()
X2.loc[50:, "f1"] += 100.0
Z2 = transform_matrix(X2, dates, "zscore_ts", codes=codes)
check("미래 행 변조가 과거 출력에 무영향", np.allclose(Z[:50, 0], Z2[:50, 0]),
      f"max|Δ|={np.abs(Z[:50, 0] - Z2[:50, 0]).max():.2e}")

# 다른 종목(B) 변조 → A 출력 불변 (종목 간 독립)
X3 = X.copy()
X3.loc[30:, "f1"] *= 5.0
Z3 = transform_matrix(X3, dates, "zscore_ts", codes=codes)
check("다른 종목 변조가 A 출력에 무영향", np.allclose(Z[:30, 0], Z3[:30, 0]),
      f"max|Δ|={np.abs(Z[:30, 0] - Z3[:30, 0]).max():.2e}")

# 시프트 검증: 당일 값만 바꾸면 그 날의 z 가 이론값 (x_t - mu_{t-1..t-20})/sd 로 변한다
i = 25
mu = X["f1"].iloc[i - 20:i].mean()
sd = X["f1"].iloc[i - 20:i].std()
want = (X["f1"].iloc[i] - mu) / sd
check("직전 20행 기준 z (shift=1)", abs(Z[i, 0] - want) < 1e-9, f"got {Z[i,0]:.6f} want {want:.6f}")

# 상수 종목은 z=0 (sd=0 → 0 채움)
Xc = pd.DataFrame({"f1": np.ones(30)})
Zc = transform_matrix(Xc, dates[:30], "zscore_ts", codes=["A"] * 30)
check("상수 종목 z=0(sd 0 처리)", np.allclose(Zc, 0.0))

# rank/zscore 경로 회귀 없음
check("rank 회귀", np.allclose(transform_matrix(X, dates, "rank"), X.groupby(dates).rank(pct=True).values))
check("zscore 회귀", np.allclose(transform_matrix(X, dates, "zscore", codes=codes),
                                transform_matrix(X, dates, "zscore")))
check("none 회귀", np.allclose(transform_matrix(X, dates, None), X.values))
try:
    transform_matrix(X, dates, "zscore_ts")
    check("codes 없으면 실패", False)
except ValueError:
    check("codes 없으면 실패", True)

print("결과:", "ALL PASS" if not fails else f"FAIL {len(fails)}: {fails}")
sys.exit(1 if fails else 0)

"""make_labels(exit_at='next_open') 회귀 테스트 (CG529, 2026-11-27).

검증:
  1. 기본 경로(exit_at 미지정)는 'open' 열 없이도 동작하고, 종가→종가 라벨을 그대로 만든다.
  2. exit_at='next_open' 라벨은 open(t+h)/close(t)−1 과 정확히 일치한다(abs_thresh 로 원값 노출).
  3. 축 유효성: 실제 패널처럼 갭이 섞이면 분위 라벨이 exit 규칙에 따라 달라진다(no-op 아님).
  4. entry='next_open' 과 exit_at='next_open' 동시 사용은 명시적 실패.
  5. 'open' 열이 없으면 조용히 폴백하지 않고 실패한다. 스무딩 라벨과의 조합도 실패.

실행: docker exec stock_xgboost_ml sh -c 'cd /app && python scripts/_make_labels_exit_at_test.py'
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wf_wave as W  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f" — {detail}" if detail else ""))
    if not cond:
        FAIL.append(name)


def make_df():
    rows = []
    for i, code in enumerate(["A", "B"]):
        base = 1000.0 + 100.0 * i
        for d in range(6):
            close = base + 10.0 * d
            openp = close + (5.0 if d % 2 == 0 else -5.0)
            rows.append({"stock_code": code, "date": f"2026-01-0{d + 1}",
                         "price": close, "open": openp})
    return pd.DataFrame(rows)


def test_default_path():
    df = make_df()
    df_no_open = df.drop(columns=["open"])
    check("기본 경로는 'open' 열 없이 동작",
          W.make_labels(df_no_open, "quantile", 1, 0.30) is not None)
    sel = df_no_open.sort_values(["stock_code", "date"]).reset_index(drop=True)
    expected = sel.groupby("stock_code", sort=False)["price"].shift(-1) / sel["price"] - 1.0
    got = W.make_labels(sel, "abs_thresh", 1, 0.30, thresh=0.0)
    mask = ~np.isnan(expected.values)
    check("기본 경로 라벨 == 종가→종가 수익 부호",
          bool((got[mask] == (expected.values[mask] >= 0).astype(float)).all()),
          f"n={int(mask.sum())}")


def test_exit_next_open():
    sel = make_df()
    sel = sel.sort_values(["stock_code", "date"]).reset_index(drop=True)
    exp_on = sel.groupby("stock_code", sort=False)["open"].shift(-1) / sel["price"] - 1.0
    got_on = W.make_labels(sel, "abs_thresh", 1, 0.30, thresh=0.0, exit_at="next_open")
    m = ~np.isnan(exp_on.values)
    check("exit_at 라벨 == 익일시가/종가 부호",
          bool((got_on[m] == (exp_on.values[m] >= 0).astype(float)).all()),
          f"n={int(m.sum())}")


def test_axis_effective():
    rng = np.random.default_rng(0)
    rows = []
    for i in range(8):
        code = f"C{i:02d}"
        close = 1000.0
        for d in range(40):
            gap = float(rng.normal(0.0, 0.02))
            rows.append({"stock_code": code, "date": f"2026-02-{d + 1:02d}",
                         "price": close, "open": close * (1.0 + gap)})
            close = close * (1.0 + float(rng.normal(0.0, 0.01)))
    rnd = pd.DataFrame(rows).sort_values(["stock_code", "date"]).reset_index(drop=True)
    q_close = W.make_labels(rnd, "quantile", 1, 0.30)
    q_on = W.make_labels(rnd, "quantile", 1, 0.30, exit_at="next_open")
    both = ~(np.isnan(q_close) | np.isnan(q_on))
    n_diff = int(np.sum(q_close[both] != q_on[both]))
    check("분위 라벨이 exit 규칙에 따라 달라진다(축이 유효)", n_diff > 0,
          f"뒤집힌 행 {n_diff}/{int(both.sum())}")
    last_idx = rnd.groupby("stock_code", sort=False).tail(1).index
    check("마지막 행은 라벨 결측(NaN)",
          bool(pd.isna(pd.Series(q_on, index=rnd.index).loc[last_idx]).all()))


def test_rejections():
    sel = make_df().sort_values(["stock_code", "date"]).reset_index(drop=True)
    no_open = sel.drop(columns=["open"])
    try:
        W.make_labels(sel, "quantile", 1, 0.30, entry="next_open", exit_at="next_open")
        check("entry+exit 동시 사용 → RuntimeError", False, "예외 없음")
    except RuntimeError as e:
        check("entry+exit 동시 사용 → RuntimeError", True, str(e)[:60])
    try:
        W.make_labels(no_open, "quantile", 1, 0.30, exit_at="next_open")
        check("'open' 결측 시 실패", False, "예외 없음(조용한 폴백 위험)")
    except RuntimeError as e:
        check("'open' 결측 시 실패", True, str(e)[:60])
    try:
        W.make_labels(sel, "smooth", 5, 0.30, exit_at="next_open")
        check("smooth+exit_at → RuntimeError", False, "예외 없음")
    except RuntimeError:
        check("smooth+exit_at → RuntimeError", True)


test_default_path()
test_exit_next_open()
test_axis_effective()
test_rejections()

print()
print(f"결과: {len(FAIL)} FAIL / 8 checks")
if FAIL:
    print("실패:", FAIL)
    sys.exit(1)

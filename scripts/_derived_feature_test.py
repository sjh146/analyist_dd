#!/usr/bin/env python3
"""_derived_feature_test.py — CG25(피처 시간 변화율 Δ) 파생 정의 정합성 테스트.

컨테이너: docker exec stock_xgboost_ml python /app/scripts/_derived_feature_test.py

검증:
  T1 npz 원본에서 **독립 계산**한 Δ1(종목별 과거 1행 차분)과 add_derived 결과가 비트 일치
  T2 종목별 첫 k행은 결측(라벨 결측 제거 전 프레임 기준) — 미래 참조 없음(shift(+k) 만 사용)
  T3 소스 6개 전부 패널에 존재(사전 등록 목록이 실재)
  T4 Δ5 도 동일 방식으로 일치
"""
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

import wf_label_sweep as LS  # noqa: E402

PANEL = "/app/app/models/wf/panel_150u.npz"
SRC = ["volatility_20d", "volatility_60d", "atr_pct", "volume_ratio_5", "rsi", "ma_position_20"]
FAIL = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}", flush=True)
    if not ok:
        FAIL.append(name)


def main():
    df, names = LS.W.build_panel(PANEL, 50, 420, log=lambda *a: None)
    base = [n for n in names if n in df.columns]
    print(f"panel rows={len(df)} cols={len(base)}", flush=True)

    check("T3 소스 6개 전부 패널에 존재", all(s in base for s in SRC),
          f"누락={[s for s in SRC if s not in base]}")

    out, bn = LS.add_derived(df, base, {"sources": SRC, "lags": [1, 5]}, log=lambda m: print("   ", m))
    check("확장 목록 = base + 12", len(bn) == len(base) + 12, f"{len(base)} → {len(bn)}")

    # ── 독립 계산: npz 원본 X 에서 직접 종목별 차분을 만들어 비교 ────────────────
    z = np.load(PANEL, allow_pickle=True)
    X = np.asarray(z["X"], dtype=float)
    codes = np.asarray(z["codes"]).astype(str)
    dates = np.asarray(z["dates"]).astype(str)
    znames = [str(n) for n in z["feature_names"]]
    # df 는 build_panel 이 npz 캐시에서 복원한 프레임 — 행 수·(code,date) 순서가 같아야 한다.
    check("T0 npz 행수 == 프레임 행수", X.shape[0] == len(df), f"{X.shape[0]} vs {len(df)}")
    if X.shape[0] != len(df):
        return
    for k in (1, 5):
        for s in SRC:
            col = znames.index(s)
            # 독립 계산 B: npz 원본 X 에서 (code,date) 정렬 후 종목별 차분
            tmp = pd.DataFrame({"c": codes, "d": dates, "v": X[:, col]})
            tmp = tmp.sort_values(["c", "d"], kind="stable").reset_index(drop=True)
            prev = tmp.groupby("c", sort=False)["v"].shift(k)
            exp_prev = prev.values
            # add_derived 결과를 같은 키로 정렬
            mine = pd.DataFrame({"c": df["stock_code"].astype(str).values,
                                 "d": df["date"].astype(str).values,
                                 "v": df[s].values,
                                 "got": out[f"d{k}_{s}"].values})
            mine = mine.sort_values(["c", "d"], kind="stable").reset_index(drop=True)
            same = bool((mine["c"].values == tmp["c"].values).all()
                        and (mine["d"].values == tmp["d"].values).all())
            if not same:
                check(f"T 정렬 정합 Δ{k} {s}", False, "정렬 키가 어긋나 비교 불가")
                continue
            m = ~np.isnan(exp_prev)                       # 첫 k행은 비교 대상 아님
            exp = mine["v"].values - exp_prev
            diff = float(np.max(np.abs(exp[m] - mine["got"].values[m]))) if m.any() else -1.0
            # 첫 k행은 과거 행이 없어 **NaN**(결측) 이어야 한다 — 매트릭스 단계에서
            # np.nan_to_num 으로 0 대체되므로 여기서 0 을 기대하면 안 된다(정의상 결측).
            tail = mine["got"].values[~m]
            nan_ok = bool(np.isnan(tail).all())
            check(f"T{'1' if k == 1 else '4'} Δ{k} {s} 비트 일치", diff == 0.0 and nan_ok,
                  f"max|Δ|={diff:.3e} 첫{k}행NaN={nan_ok} n={int(m.sum())}")
    print(f"\n=== 결과: {'전부 PASS' if not FAIL else 'FAIL ' + str(FAIL)} ===", flush=True)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main() or 0)

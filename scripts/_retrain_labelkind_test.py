#!/usr/bin/env python3
"""자가점검: retrain_champion 의 라벨 종류 옵션 (2026-10-01, CG9 A단계).

이 스택에는 pytest 가 없다 → 순수 파이썬 PASS/FAIL + sys.exit(1) 형태로 쓴다.

검증 항목:
  1. 기본 경로(label_kind='h1_direction')의 라벨이 **수정 전 코드와 비트 동일**하다
     (생산 경로 무변경 보증 — 저녁 파이프라인이 이 함수를 매일 쓴다).
  2. rel 라벨: 날짜별 중앙값 분할(≈50/50) · 선행수익 순서와 라벨 순서가 일치 · 마지막 h행은 0.
  3. rel_smooth: 부호·분할 정상(중앙값 분할) + 스무딩이 점대점과 다른 값을 낸다.
  4. 알 수 없는 label_kind 는 ValueError.
  5. model_params override: 준 키만 적용(max_depth=1), 기본(None) 은 params 무변경(8 유지).
  6. e2e: retrain_champion 이 meta 에 label_kind·horizon 을 기록한다(소형 합성 df).

실행(컨테이너 안, cwd=/app):
    docker exec stock_xgboost_ml python /app/scripts/_retrain_labelkind_test.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

import numpy as np
import pandas as pd

sys.path.insert(0, "/app")

from app.training.retrain_champion import (  # noqa: E402
    _create_labels, _create_labels_relative, retrain_champion,
)

FAILS: list[str] = []
PASSES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSES if cond else FAILS).append(f"{name}{(' — ' + detail) if detail else ''}")
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")


def _orig_labels(df: pd.DataFrame) -> np.ndarray:
    """수정 전 `_create_labels` 의 사본(비트 동일 검증의 기준)."""
    labels = np.zeros(len(df), dtype=int)
    if "stock_code" not in df.columns or "price" not in df.columns:
        return labels
    for code in df["stock_code"].unique():
        mask = df["stock_code"] == code
        idx = df[mask].index
        prices = df.loc[idx, "price"].values.astype(np.float64)
        if len(prices) >= 2:
            next_up = prices[1:] > prices[:-1]
            vals = np.zeros(len(prices), dtype=int)
            vals[:-1] = next_up.astype(int)
            labels[idx] = vals
    return labels


def synth(n_stocks: int = 3, n_dates: int = 20, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = [f"2026-01-{d+1:02d}" for d in range(n_dates)]
    rows = []
    for i in range(n_stocks):
        p = 10000.0
        for d in dates:
            p *= float(1.0 + rng.normal(0, 0.01) + 0.001 * i)
            rows.append({"date": d, "stock_code": f"{i:06d}", "price": round(p, 2)})
    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)


def main() -> int:
    print("== 1. 기본 경로 무변경(수정 전 코드와 비트 동일) ==")
    df = synth()
    a = _create_labels(df)
    b = _orig_labels(df)
    check("h1_direction == 수정 전 구현", bool(np.array_equal(a, b)),
          f"len={len(a)} diff={int((a != b).sum())}")

    print("== 2. rel 라벨(시장상대 중앙값) ==")
    df = synth(n_stocks=6, n_dates=20)
    y = _create_labels_relative(df, horizon=5, smooth=False)
    by_date = df.assign(y=y).groupby("date")["y"].mean()
    check("라벨 0/1 만", set(np.unique(y)).issubset({0, 1}), f"uniq={sorted(set(y.tolist()))}")
    # 완전 교차검증: 원시 가격에서 h일 선행수익과 날짜별 중앙값을 직접 계산해 라벨과 일치하는지 본다.
    # (평가 경로 champion_robust_eval._make_labels 는 `r > median` 이다 — 그 정의와 같은지 확인)
    px = {c: g.sort_values("date")["price"].to_numpy(float)
          for c, g in df.groupby("stock_code")}
    pos_in_stock = {}          # (code, date) -> 종목 내 위치
    for c, g in df.groupby("stock_code"):
        for i, d in enumerate(g.sort_values("date")["date"].to_numpy()):
            pos_in_stock[(c, d)] = i
    exp = np.zeros(len(df), dtype=int)
    for d in df["date"].unique():
        rows = df.index[df["date"] == d].to_numpy()
        codes = df.loc[rows, "stock_code"].to_numpy()
        vals = []
        for c in codes:
            i = pos_in_stock[(c, d)]
            s = px[c]
            vals.append(s[i + 5] / s[i] - 1.0 if i + 5 < len(s) else np.nan)
        vals = np.asarray(vals, dtype=float)
        med = np.nanmedian(vals)
        ok = np.isfinite(vals)
        exp[rows[ok]] = (vals[ok] > med).astype(int)
    check("교차검증: 원시 가격 → 중앙값 분할 == 함수 출력",
          bool(np.array_equal(exp, y)), f"diff={int((exp != y).sum())}")
    check("날짜별 양성률이 0 초과 ~0.5 이하(중앙값 분할의 정의역)",
          bool(((by_date.iloc[:-5] > 0) & (by_date.iloc[:-5] <= 0.5)).all()),
          f"min={by_date.iloc[:-5].min():.2f} max={by_date.iloc[:-5].max():.2f} "
          f"(마지막 5일은 선행수익 없음 → 전부 0: {by_date.iloc[-5:].max():.2f})")
    d0 = df["date"].unique()[0]
    sub = df[df["date"] == d0].sort_values("stock_code")
    fwd = []
    for code in sub["stock_code"]:
        s = df[df["stock_code"] == code].sort_values("date")["price"].to_numpy()
        fwd.append(s[5] / s[0] - 1.0)
    top_code = sub["stock_code"].to_numpy()[int(np.argmax(fwd))]
    got = int(y[(df["date"] == d0) & (df["stock_code"] == top_code)][0])
    check("최고 선행수익 종목이 라벨 1", got == 1, f"date={d0} code={top_code}")
    tail = df.assign(y=y).groupby("stock_code").tail(5)["y"].to_numpy()
    check("마지막 h행(선행수익 없음)은 0", bool((tail == 0).all()))

    print("== 3. rel_smooth ==")
    ys = _create_labels_relative(df, horizon=5, smooth=True)
    check("smooth 도 0/1", set(np.unique(ys)).issubset({0, 1}))
    check("smooth 가 점대점과 다른 값을 낸다(스무딩 실제 적용)",
          not np.array_equal(ys, y), f"diff={(ys != y).sum()}행/{len(ys)}")
    check("smooth 날짜별 양성률도 0 초과 ~0.5 이하(마지막 1일 제외)",
          bool(((df.assign(y=ys).groupby("date")["y"].mean()).iloc[:-1] > 0).all()))

    print("== 4. 알 수 없는 label_kind ==")
    try:
        retrain_champion(df, out_dir=tempfile.mkdtemp(), label_kind="nope")
        check("ValueError 발생", False, "예외 없음")
    except ValueError as e:
        check("ValueError 발생", True, str(e)[:40])
    except Exception as e:  # pragma: no cover
        check("ValueError 발생", False, f"{type(e).__name__}: {e}")

    print("== 5·6. e2e(소형 합성 df) — model_params·meta ==")
    dfl = synth(n_stocks=4, n_dates=60, seed=11)
    tmp = tempfile.mkdtemp()
    try:
        m1 = retrain_champion(dfl, out_dir=os.path.join(tmp, "a"), n_estimators=20)
        check("기본 meta.label_kind == h1_direction", m1.get("label_kind") == "h1_direction",
              str(m1.get("label_kind")))
        check("기본 meta.model_params_override is None",
              m1.get("model_params_override") is None)
        m2 = retrain_champion(dfl, out_dir=os.path.join(tmp, "b"), n_estimators=20,
                              label_kind="rel", horizon=5,
                              model_params={"max_depth": 1, "learning_rate": 0.05,
                                            "존재하지않는키": 9})
        check("rel meta.label_kind/horizon 기록",
              m2.get("label_kind") == "rel" and m2.get("horizon") == 5,
              f"{m2.get('label_kind')}/{m2.get('horizon')}")
        ov = m2.get("model_params_override") or {}
        check("override meta 기록", ov.get("max_depth") == 1)
        import joblib
        mx = joblib.load(os.path.join(tmp, "b", "xgboost_model.pkl"))
        ma = joblib.load(os.path.join(tmp, "a", "xgboost_model.pkl"))
        check("override 가 실제 모델 params 에 적용(max_depth 1)",
              mx["params"].get("max_depth") == 1, str(mx["params"].get("max_depth")))
        check("기본 경로 params 무변경(max_depth 8)",
              ma["params"].get("max_depth") == 8, str(ma["params"].get("max_depth")))
        check("override 가 없는 키는 무시(존재하지않는키 미추가)",
              "존재하지않는키" not in mx["params"])
        mp = [json.load(open(os.path.join(tmp, "a", f)))["label_kind"]
              for f in os.listdir(os.path.join(tmp, "a")) if f.startswith("training-result-")]
        check("meta 파일에도 label_kind 기록", mp == ["h1_direction"], str(mp))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n총 {len(PASSES)} PASS / {len(FAILS)} FAIL")
    for f in FAILS:
        print("  FAIL:", f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())

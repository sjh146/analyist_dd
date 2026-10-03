#!/usr/bin/env python3
"""CG92 셋업 자체점검 — 분위 꼬리 라벨(q) 배선의 정합성/회귀 검증.

왜 필요한가(2026-10-04): CG89/90/91 이 청정 패널 스윕에서 라벨 꼬리(q0.05) 레버를 확정했고,
승격 전 관문은 **생산 경로 재현**이다. 그 셋업으로 retrain_champion(dict 학습)과
champion_robust_eval(평가)에 `--label-q` 를 추가했는데, 이 코드가 (a) 스윕과 **같은 정의**이고
(b) 기본값(미지정)에서 **비트 동일**임을 증명하지 않으면 '다른 과제를 재고 신호라고 부르는'
사고(CG36/37)가 재발한다.

검사(모두 순수 파이썬 — 이 스택엔 pytest 가 없다):
  1. `_create_labels_quantile` == `wf_wave.make_labels(kind='quantile')` (원소 단위 동일)
  2. 꼬리 구조: 양성/음성이 그날 상위/하위 q, 가운데 NaN, 마지막 h행 NaN
  3. `champion_robust_eval._make_labels(kind='quantile')` == 학습측 꼬리 분할(같은 q)
     + 가운데는 None(채점 제외)
  4. 회귀: `_create_labels`(절대 h1)·`_make_labels('rel'/'abs')` 값이 종전 정의와 동일
  5. 인자 검증: quantile 인데 q 없음 / q 범위 밖 / q 만 주고 kind 불일치 → ValueError
  6. CLI 검증: `python -m app.training.retrain_champion --label-kind quantile` → rc!=0 + 안내문
  7. e2e(모델 스텁): retrain_champion(label_kind='quantile', label_q=0.05) 이
     meta 에 label_kind/label_q/n_middle_dropped 를 기록하고 가운데 행을 실제로 버리는가
  8. CLI `--help` 에 --label-q 가 노출되고 choices 에 quantile 이 있는가

실행(컨테이너):
  docker exec -e PYTHONPATH=/app stock_xgboost_ml python /app/scripts/_label_q_test.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

import numpy as np
import pandas as pd

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

FAILS: list[str] = []
NOTES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"PASS  {name}")
    else:
        print(f"FAIL  {name}  {detail}")
        FAILS.append(name)


def _synth_panel(n_stocks: int = 100, n_days: int = 30, seed: int = 7) -> pd.DataFrame:
    """합성 패널 — 종목별 랜덤워크 + 그날 횡단면 분산(분위 라벨이 의미를 갖게)."""
    rng = np.random.default_rng(seed)
    rows = []
    dates = pd.date_range("2025-09-01", periods=n_days, freq="D").strftime("%Y-%m-%d")
    for si in range(n_stocks):
        code = f"{si:06d}"
        p = 10000.0
        for di, d in enumerate(dates):
            p = max(1.0, p * (1.0 + rng.normal(0, 0.02)))
            rows.append({"stock_code": code, "date": d, "price": p, "return_5d": rng.normal()})
    return pd.DataFrame(rows)


def main() -> int:
    from app.training.retrain_champion import (
        _create_labels, _create_labels_quantile, retrain_champion,
    )
    import champion_robust_eval as cre

    df = _synth_panel()
    H, Q = 5, 0.05

    # ── 1. 스윕(wf_wave)과 같은 정의인가 ────────────────────────────────────────
    y_mine = _create_labels_quantile(df, horizon=H, q=Q)
    try:
        import wf_wave as W
        y_ref = np.asarray(W.make_labels(df, "quantile", H, Q), dtype=float)
        same = np.array_equal(np.isnan(y_mine), np.isnan(y_ref)) and np.allclose(
            y_mine[~np.isnan(y_mine)], y_ref[~np.isnan(y_ref)])
        check("1. wf_wave.make_labels(quantile) 와 원소 단위 동일", bool(same),
              f"nan_mismatch={int((np.isnan(y_mine) != np.isnan(y_ref)).sum())} "
              f"val_mismatch={int((~np.isnan(y_mine) & (y_mine != y_ref)).sum())}")
    except Exception as e:                      # import 실패 시 내부 참조 구현으로 대조
        NOTES.append(f"wf_wave import 실패({type(e).__name__}: {e}) — 참조 구현으로 대조")
        price = df.groupby("stock_code", sort=False)["price"]
        ret = price.transform(lambda s: s.shift(-H) / s - 1.0)
        day = df["date"]
        hi = ret.groupby(day).transform(lambda s: s.quantile(1 - Q))
        lo = ret.groupby(day).transform(lambda s: s.quantile(Q))
        y_ref = pd.Series(np.nan, index=df.index, dtype=float)
        y_ref[ret > hi] = 1.0
        y_ref[ret < lo] = 0.0
        y_ref = y_ref.values
        same = np.array_equal(np.isnan(y_mine), np.isnan(y_ref)) and np.allclose(
            y_mine[~np.isnan(y_mine)], y_ref[~np.isnan(y_ref)])
        check("1. 참조 구현(스윕 본문 복제)과 원소 단위 동일", bool(same))

    # ── 2. 꼬리 구조 ────────────────────────────────────────────────────────────
    fin = np.isfinite(y_mine)
    tot = len(y_mine)
    pos_frac = float(np.nansum(y_mine == 1.0) / tot)   # 전체 행 대비(꼬리=전체의 q)
    neg_frac = float(np.nansum(y_mine == 0.0) / tot)
    check("2a. 가운데 분위는 제외(양성+음성 == 유한행 수)",
          int(np.nansum(y_mine == 1.0) + np.nansum(y_mine == 0.0)) == int(fin.sum()),
          "라벨 0/1 외 값 존재")
    check("2b. 양성·음성 비율이 각각 q 근처(전체 행의 0.02~0.12)",
          0.02 <= pos_frac <= 0.12 and 0.02 <= neg_frac <= 0.12,
          f"pos={pos_frac:.3f} neg={neg_frac:.3f} (전체 {tot}행)")
    check("2c. 유한 라벨 비율 ≈ 2q+여유 (0.08~0.25)",
          0.08 <= fin.mean() <= 0.25, f"finite={fin.mean():.3f}")
    # 마지막 h행(종목별)은 라벨 없음
    tail_missing = True
    for _c, g in df.groupby("stock_code", sort=False):
        if not np.all(np.isnan(y_mine[g.index.values[-H:]])):
            tail_missing = False
            break
    check("2d. 종목별 마지막 h행은 라벨 NaN", tail_missing)

    # ── 3. 평가측 라벨이 학습측과 같은 분할인가(그날 표본 기준) ──────────────────
    ok_partition = True
    # 종목 시계열 기준 선행수익(둘 다 같은 정의를 쓰는지 원소 단위로 대조)
    fwd = np.full(len(df), np.nan)
    for _c, g in df.groupby("stock_code", sort=False):
        idx = g.index.values
        p = df.loc[idx, "price"].values.astype(float)
        v = np.full(len(p), np.nan)
        v[:-H] = p[H:] / p[:-H] - 1.0
        fwd[idx] = v
    for d, g in df.groupby("date", sort=False):
        idx = g.index.values
        rr = fwd[idx]
        if not np.isfinite(rr).any():
            continue
        y_ev = cre._make_labels(list(rr), "quantile", Q)
        y_tr = y_mine[idx]
        for a, b in zip(y_ev, y_tr):
            if (a is None) != (not np.isfinite(b)):
                ok_partition = False
                break
            if a is not None and float(a) != float(b):
                ok_partition = False
                break
        if not ok_partition:
            break
    check("3. 평가측 _make_labels(quantile) == 학습측 꼬리 분할 + 가운데 None", ok_partition)

    # ── 4. 기본 경로 회귀(값 동일) ──────────────────────────────────────────────
    y_abs = _create_labels(df)
    ref_abs = np.zeros(len(df), dtype=int)
    for _c, g in df.groupby("stock_code", sort=False):
        idx = g.index.values
        p = df.loc[idx, "price"].values.astype(float)
        v = np.zeros(len(p), dtype=int)
        v[:-1] = (p[1:] > p[:-1]).astype(int)
        ref_abs[idx] = v
    check("4a. _create_labels(절대 h1) 값 회귀 없음", np.array_equal(y_abs, ref_abs))
    rets = [0.01, -0.02, 0.03, 0.0, -0.01]
    check("4b. _make_labels('abs') 회귀 없음",
          cre._make_labels(rets, "abs") == [1, 0, 1, 0, 0])
    check("4c. _make_labels('rel') 회귀 없음(중앙값 분할)",
          cre._make_labels(rets, "rel") == [1, 0, 1, 0, 0])
    check("4d. 필수 컬럼 결측 시 _create_labels_quantile 은 전부 NaN(예외 아님)",
          bool(np.all(np.isnan(_create_labels_quantile(df[['stock_code', 'price']].copy(), H, Q)))))

    # ── 5. 인자 검증 ────────────────────────────────────────────────────────────
    bad = 0
    try:
        retrain_champion(df, out_dir="/tmp/_lq_bad1", label_kind="quantile", label_q=None,
                         n_estimators=5)
        bad += 1
    except ValueError:
        pass
    try:
        retrain_champion(df, out_dir="/tmp/_lq_bad2", label_kind="quantile", label_q=0.9,
                         n_estimators=5)
        bad += 1
    except ValueError:
        pass
    try:
        retrain_champion(df, out_dir="/tmp/_lq_bad3", label_kind="nope", n_estimators=5)
        bad += 1
    except ValueError:
        pass
    check("5. 잘못된 label_q/label_kind → ValueError(조용한 폴백 금지)", bad == 0, f"bad={bad}")

    # ── 6. CLI 검증 ─────────────────────────────────────────────────────────────
    r = subprocess.run([sys.executable, "-m", "app.training.retrain_champion",
                        "--label-kind", "quantile"], capture_output=True, text=True, cwd="/app")
    check("6a. CLI: quantile 인데 --label-q 없음 → rc!=0 + 안내",
          r.returncode != 0 and "--label-q" in (r.stderr + r.stdout),
          f"rc={r.returncode}")
    r2 = subprocess.run([sys.executable, "-m", "app.training.retrain_champion",
                         "--label-q", "0.05"], capture_output=True, text=True, cwd="/app")
    check("6b. CLI: --label-q 만 주고 kind 불일치 → rc!=0",
          r2.returncode != 0, f"rc={r2.returncode}")
    r3 = subprocess.run([sys.executable, "-m", "app.training.retrain_champion", "--help"],
                        capture_output=True, text=True, cwd="/app")
    check("6c. CLI --help 에 --label-q 노출", "--label-q" in (r3.stdout + r3.stderr))
    r4 = subprocess.run([sys.executable, "/app/scripts/champion_robust_eval.py", "--help"],
                        capture_output=True, text=True, cwd="/app")
    check("6d. champion_robust_eval --help 에 --label-q·quantile 노출",
          "--label-q" in (r4.stdout + r4.stderr) and "quantile" in (r4.stdout + r4.stderr))

    # ── 7. e2e(모델 스텁) — 가운데 행이 실제로 버려지고 meta 가 기록되는가 ────────
    import app.feature_engine.feature_pipeline as fpmod
    orig_fp = fpmod.FeaturePipeline

    class _StubFP:                                   # canonical 피처 목록만 제공(모델은 실물)
        def get_feature_names(self):
            return ["price", "return_5d", "rank_return_5d"]

    fpmod.FeaturePipeline = _StubFP
    try:
        df2 = _synth_panel(n_stocks=200, n_days=30, seed=11)
        with tempfile.TemporaryDirectory() as td:
            meta = retrain_champion(df2, out_dir=td, label_kind="quantile", label_q=0.05,
                                    n_estimators=20, seed=0, data_start="2025-09-01",
                                    data_end="2025-09-25")
        fin2 = np.isfinite(_create_labels_quantile(df2, horizon=H, q=Q))
        check("7a. meta['label_kind']/'label_q' 기록",
              meta.get("label_kind") == "quantile" and meta.get("label_q") == 0.05,
              str({k: meta.get(k) for k in ("label_kind", "label_q")}))
        check("7b. meta['n_middle_dropped'] == 가운데 행 수",
              int(meta.get("n_middle_dropped", -1)) == int((~fin2).sum()),
              f"meta={meta.get('n_middle_dropped')} calc={(~fin2).sum()}")
        check("7c. 학습행 = 꼬리 행만(가운데 제외)",
              int(meta.get("n_rows", -1)) == int(fin2.sum()),
              f"n_rows={meta.get('n_rows')} tail={int(fin2.sum())}")
        check("7d. 기본 경로(quantile 미지정) meta['label_q'] is None",
              _default_meta_ok(df2, retrain_champion))
    finally:
        fpmod.FeaturePipeline = orig_fp

    print()
    for n in NOTES:
        print("NOTE ", n)
    if FAILS:
        print(f"\n{len(FAILS)} FAIL: {FAILS}")
        return 1
    print("ALL PASS")
    return 0


def _default_meta_ok(df2: pd.DataFrame, rc) -> bool:
    import app.feature_engine.feature_pipeline as fpmod
    orig = fpmod.FeaturePipeline

    class _StubFP:
        def get_feature_names(self):
            return ["price", "return_5d", "rank_return_5d"]

    fpmod.FeaturePipeline = _StubFP
    try:
        with tempfile.TemporaryDirectory() as td:
            m = rc(df2, out_dir=td, n_estimators=20, seed=0)
        return m.get("label_kind") == "h1_direction" and m.get("label_q") is None \
            and int(m.get("n_middle_dropped", -1)) == 0
    finally:
        fpmod.FeaturePipeline = orig


if __name__ == "__main__":
    raise SystemExit(main())

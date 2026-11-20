"""horizon_expectancy 순수 함수 회귀 테스트 (스펙 §2, 4개).

WHY (2026-11-20): scripts/horizon_expectancy.py 가 내는 '건당 기대값'이 지평 승격 논의의
근거가 된다(docs/spec_horizon_expectancy.md). 기대값이 ①비용 ②픽 순서 ③purge ④재현성에
어떻게 의존하는지는 돈 판정의 전제이므로 스펙이 정한 4개 축을 고정한다.
모델 학습 경로(wf_wave.train_seed 재사용)는 컨테이너 전용 import 라 호스트 테스트에서 못
돌린다 — horizon_expectancy.evaluate() 는 make_labels/select/train_predict 를 주입받으므로
가짜 모델로 전체 파이프라인(라벨→폴드→픽→시뮬→요약)을 호스트에서 돌릴 수 있다.
"""
import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import horizon_expectancy as he  # noqa: E402


# ── 합성 데이터 헬퍼 ──────────────────────────────────────────────────────────

def _quantile_label(df, horizon, q):
    """wf_wave.make_labels(kind='quantile') 의 규칙을 재현한 테스트용 라벨
    (wf_wave 는 컨테이너 의존 import 라 호스트에서 못 쓴다 — 스펙 규칙 그대로).
    close[t+h]/close[t]-1 의 날짜별 분위 1-q 초과=1 / q 미만=0, 중간은 결측."""
    ret = df.groupby("stock_code", sort=False)["price"].transform(
        lambda s: s.shift(-horizon) / s - 1.0)
    hi = ret.groupby(df["date"]).transform(lambda s: s.quantile(1 - q))
    lo = ret.groupby(df["date"]).transform(lambda s: s.quantile(q))
    y = pd.Series(np.nan, index=df.index, dtype=float)
    y[ret > hi] = 1.0
    y[ret < lo] = 0.0
    return y.values


def _make_panel(n_dates=30, n_codes=20, n_feats=4, seed=7, gap_days=None):
    """결정적 합성 패널: (date, stock_code, price, f0..f{n_feats-1}).
    gap_days: {code: [날짜 인덱스...]} — 해당 종목의 날짜를 비워 _ref 가 폴드 경계를
    건너뛰는(라벨 누출 가능) 상황을 만든다."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2026-01-01", periods=n_dates, freq="D").strftime("%Y-%m-%d")
    gap_days = gap_days or {}
    rows = []
    for c in range(n_codes):
        code = f"{c:06d}"
        prices = 1000.0 + np.cumsum(rng.normal(0, 50, n_dates))
        keep = np.ones(n_dates, dtype=bool)
        for gd in gap_days.get(code, ()):
            keep[gd] = False
        for i in np.where(keep)[0]:
            feats = rng.normal(size=n_feats)
            rows.append({"date": str(dates[i]), "stock_code": code,
                         "price": float(prices[i]),
                         **{f"f{j}": float(feats[j]) for j in range(n_feats)}})
    df = pd.DataFrame(rows)
    return df, [f"f{j}" for j in range(n_feats)]


# ── 스펙 §2 ① 비용이 기대값을 정확히 깎는다 ──────────────────────────────────────

def test_cost_deduction_exact():
    df, _names = _make_panel(n_dates=12, n_codes=3)
    picks = pd.DataFrame([
        {"fold": 1, "date": "2026-01-03", "stock_code": "000000", "prob": 0.9},
        {"fold": 1, "date": "2026-01-05", "stock_code": "000001", "prob": 0.8},
        {"fold": 1, "date": "2026-01-08", "stock_code": "000002", "prob": 0.7},
    ])
    t0, sk0 = he.simulate_trades(df, picks, horizon=2, cost_bps=0, slip_bps=0)
    t21, sk21 = he.simulate_trades(df, picks, horizon=2, cost_bps=21, slip_bps=0)
    t_slip, _ = he.simulate_trades(df, picks, horizon=2, cost_bps=0, slip_bps=20)
    assert len(t0) == len(picks) == 3 and not sk0 and not sk21
    for a, b, c in zip(t0, t21, t_slip):
        # 왕복 수수료 21bp = 0.21%p 차감, 슬리피지는 편도×2 = 40bp = 0.40%p 차감
        assert b["net_pct"] == pytest.approx(a["net_pct"] - 0.21, abs=1e-9)
        assert c["net_pct"] == pytest.approx(a["net_pct"] - 0.40, abs=1e-9)
    s0 = he.summarize_trades([t["net_pct"] for t in t0])
    s21 = he.summarize_trades([t["net_pct"] for t in t21])
    assert s21["mean_net_pct"] == pytest.approx(s0["mean_net_pct"] - 0.21, abs=1e-9)
    # 승률·std·낙폭은 비용과 무관한 축이면 안 바뀐다(비용은 상수 이동)
    assert s21["win_rate"] == s0["win_rate"]
    assert s21["std"] == pytest.approx(s0["std"], abs=1e-9)


# ── 스펙 §2 ② 상위 N 선택이 확률 순서를 지킨다 ───────────────────────────────────

def test_top_picks_respect_prob_order():
    te = pd.DataFrame({
        "date": ["2026-01-01"] * 2 + ["2026-01-02"] * 3,
        "stock_code": ["000002", "000001", "000005", "000004", "000003"],
        "_y": [1.0, 0.0, 1.0, 0.0, 1.0],
        "price": [1000.0] * 5,
    })
    probs = np.array([0.5, 0.9, 0.7, 0.7, 0.3])   # d2: 000005/000004 동률 0.7
    picks = he.pick_indices(te, probs, top=2)
    by_date = {d: [int(i) for i in rows] for d, rows in picks}
    # (a) 날짜별로 뽑힌 확률의 최소 ≥ 안 뽑힌 확률의 최대 (안 뽑힌 게 없으면 스킵)
    for d, rows in by_date.items():
        pos = np.where(te["date"].values == d)[0]
        chosen = probs[np.asarray(rows)]
        rest = probs[np.setdiff1d(pos, rows)]
        if len(rest):
            assert chosen.min() >= rest.max()
    # (b) 확률 내림차순 정렬 (0.9 먼저, 0.5 뒤)
    assert by_date["2026-01-01"] == [1, 0]
    # (c) 동률은 코드 오름차순: 000004 < 000005
    assert [te["stock_code"].iloc[i] for i in by_date["2026-01-02"]] == \
        ["000004", "000005"]


# ── 스펙 §2 ③ purge 가 라벨 누출을 막는다(경계일 미포함) ─────────────────────────

def test_purge_blocks_label_leak():
    # 12 거래일 × 2 종목, folds=2 → step=12//3=4. fold1: cut=dd[3], first_test=dd[4],
    # purge={dd[2], dd[3]}. 종목 B 는 dd[2], dd[4] 결번 → dd[1] 행의 라벨 참조일
    # (_ref = B 시계열 shift(-2)) = dd[5] ≥ dd[4] = 테스트 구간 가격 → purge 없이는
    # 라벨 누출. fold_split 은 이 행을 학습에서 빼야 한다.
    dates = [f"2026-01-{d:02d}" for d in range(1, 13)]
    df, _ = _make_panel(n_dates=12, n_codes=2, seed=3)
    df = df[df["stock_code"] != "000001"]          # B 를 갭 버전으로 직접 재구성
    rows = [{"date": d, "stock_code": "000001", "price": 1000.0 + i * 10.0}
            for i, d in enumerate(dates) if i not in (2, 4)]
    df = pd.concat([df, pd.DataFrame(rows)], ignore_index=True)
    h = 2
    d = df.copy()
    d["_y"] = 1.0
    d["_ref"] = df.groupby("stock_code", sort=False)["date"].shift(-h)
    d = d[~pd.isna(d["_y"])].reset_index(drop=True)
    folds = he.fold_split(d, folds=2, horizon=h)
    assert len(folds) == 2
    f1 = folds[0]
    assert f1["first_test"] == dates[4]
    assert set(f1["purge"]) == {dates[2], dates[3]}
    for f in folds:
        tr, te = f["train"], f["test"]
        # (a) purge 창 날짜는 학습에 없다 (경계일 미포함)
        assert not set(tr["date"]).intersection(f["purge"])
        # (b) 라벨 참조일이 테스트 구간을 가리키는 학습 행이 없다
        assert not (tr["_ref"] >= f["first_test"]).any()
        # (c) 테스트 구간 = (cut, nxt]
        assert (te["date"] > f["cut"]).all() and (te["date"] <= f["nxt"]).all()
    # (d) 갭 종목 B 의 dd[1] 행(라벨 참조일 dd[5] ≥ first_test dd[4])이 실제로 제거됐는가
    assert not ((f1["train"]["stock_code"] == "000001") &
                (f1["train"]["date"] == dates[1])).any()
    # (e) fold2 경계도 같은 보증: B dd[6] 은 purge 창({dd[6], dd[7]})으로 학습에서 제거된다
    f2 = folds[1]
    assert f2["first_test"] == dates[8]
    assert not ((f2["train"]["stock_code"] == "000001") &
                (f2["train"]["date"] == dates[6])).any()


# ── 스펙 §2 ④ 결정적 입력에서 결과 재현(같은 시드 2회 → 동일 mean) ───────────────

def test_deterministic_same_seed():
    df, names = _make_panel(n_dates=30, n_codes=20, n_feats=4, seed=7)
    seen = {}

    def _fake_train(Xtr, ytr, Xte, yte, sel, out_dir, seed):
        # 파이프라인이 seed 를 모델에 그대로 전달하는지 캡처(유일한 확률 지점)
        seen["seed"] = seed
        rng = np.random.default_rng(seed)
        return rng.uniform(0.0, 1.0, size=len(Xte))

    def _select(fn, sel_str, Xtr, ytr):
        return list(range(min(3, len(fn)))), "fake-top3"

    kw = dict(horizons=[1, 3], folds=2, top=2, cost_bps=21, slip_bps=20, seed=0,
              make_labels_fn=lambda d, kind, h, q: _quantile_label(d, h, q),
              select_fn=_select, train_predict_fn=_fake_train,
              log=lambda *a, **k: None)
    r1 = he.evaluate(df, names, **kw)
    r2 = he.evaluate(df, names, **kw)
    assert seen["seed"] == kw["seed"]
    # 같은 입력 + 같은 시드 → 산출 JSON(요약·by_fold·픽샘플·verdict 전부) 비트 동일
    assert json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True)
    assert r1["horizons"]["1"]["n"] > 0
    assert r1["horizons"]["1"]["mean_net_pct"] == r2["horizons"]["1"]["mean_net_pct"]
    assert r1["self_report"]["trades_completed"]["1"] > 0
    # self_report 3분리(소스 수신/파서 생성/저장)가 채워진다
    assert r1["self_report"]["source_rows"] == len(df)
    assert r1["self_report"]["label_rows"]["1"] > 0
    assert r1["self_report"]["picks_generated"]["1"] == \
        r1["self_report"]["trades_completed"]["1"] + \
        sum(r1["self_report"]["skipped_by_reason"]["1"].values())

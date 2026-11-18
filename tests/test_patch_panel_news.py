"""test_patch_panel_news — 스펙 docs/spec_panel_news_patch.md §3 테스트 계약(신규 5개).

WHY (2026-11-19): 패널 뉴스 컬럼 패치(patch_panel_news)의 as-of 계산·결측 NaN·최소 변경·
원본 보호·append 모드를 합성 패널로 검증한다. 실제 패널·DB 는 쓰지 않는다
(--mentions-file 로 언급 데이터를 주입 — 스펙 §4 "DB 쓰기 금지(SELECT 전용)"과 무관하게
테스트가 hermetic 해야 하기 때문).

  ① as-of 계산(당일 제외, 5거래일 창)   ② 결측 NaN 유지(0 아님)
  ③ 대상 컬럼만 변경·나머지 동일        ④ --out 이 원본과 같으면 exit 2
  ⑤ --append 모드 feature_names 길이 증가 + 기존 컬럼 보존

합성 패널: 3종목 × 12일(2026-10-01~12 연속일 — 합성 거래일 달력) = 36행,
패치 창 = 2026-10-08~2026-10-11(4일). 뉴스 컬럼의 창 밖 값은 1.0 으로 채워
"창 밖 유지"가 우연한 동일값(전부 0)이 아니라 실제 검증이 되게 한다.
기대값은 아래 주석에 손계산으로 적어 두었다(추측 금지 — 전부 이 테스트가 정의한 입력에서
도출). 스크립트 규칙(실제 날짜 문자열)에 따라 2026-10-XX 실제 달력 날짜를 쓴다.
"""
import json
import os
import sys
from datetime import date, timedelta

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import patch_panel_news as pnp  # noqa: E402

CODES = ["A00001", "A00002", "A00003"]
_DATES = [(date(2026, 10, 1) + timedelta(days=d)).isoformat() for d in range(12)]
FROM, TO = "2026-10-08", "2026-10-11"  # 창: _DATES[7:11]


def _panel(features, with_news=True):
    """features: {name: (36,) float64} → (X float32, names, dates, codes, price)."""
    codes = np.repeat(np.array(CODES), 12)
    dates = np.tile(np.array(_DATES), 3)
    if with_news:  # 죽은 컬럼 흉내: 창 밖은 1.0(비영), 창 안에서 교체된다
        features = dict(features)
        features["news_count_5d"] = np.full(36, 1.0)
        features["news_count_20d"] = np.full(36, 1.0)
    names = list(features)
    X = np.column_stack([features[n] for n in names]).astype(np.float32)
    price = np.arange(1, 37, dtype=np.float64) * 1000.0
    return X, np.array(names), dates, codes, price


def _save_panel(path, X, names, dates, codes, price):
    np.savez(path, X=X, feature_names=names, dates=dates, codes=codes, price=price)


def _save_mentions(path, daily, grid, coverage_start):
    path.write_text(json.dumps({"daily": daily, "grid": grid,
                                "coverage_start": coverage_start}),
                    encoding="utf-8")


def _grid(codes, dates, m7=7.0, m30=30.0):
    return [[c, d, m7, m30] for c in codes for d in dates]


def _window_rows(col):
    """(3,12) 로 펴서 창(열 7:11)만 본다."""
    return col.reshape(3, 12)[:, 7:11]


# ── ① as-of 계산(당일 제외, 5거래일 창) ────────────────────────────────────────────
def test_asof_5day_window_excludes_today():
    ds = _DATES[:10]  # 2026-10-01~10
    codes = np.array(["C1"] * 10)
    dates = np.array(ds)
    mask = (dates >= "2026-10-08") & (dates <= "2026-10-10")  # ds[7:10]
    grid = {(codes[0], d): (0.0, 0.0) for d in ds}
    # 언급: ds[2]=1(창 밖), ds[4]=1, ds[5]=1, ds[8]=1, ds[9]=1(당일)
    daily = {"C1": {ds[2]: 1, ds[4]: 1, ds[5]: 1, ds[8]: 1, ds[9]: 1}}
    vals = pnp.compute_trading_day_counts(codes, dates, mask, daily, grid, ds[0], k=5)
    # ds[9]: 이전 5거래일 = ds[4..8], lo=ds[4] → [ds[4], ds[9]) = ds[4]+ds[5]+ds[8] = 3
    #        (ds[2] 는 창 밖, ds[9] 는 당일 → 제외)
    assert vals[9] == 3.0
    # ds[8]: 이전 5거래일 = ds[3..7], lo=ds[3] → [ds[3], ds[8]) = ds[4]+ds[5] = 2
    assert vals[8] == 2.0
    # ds[7]: 이전 5거래일 = ds[2..6], lo=ds[2] → [ds[2], ds[7]) = ds[2]+ds[4]+ds[5] = 3
    assert vals[7] == 3.0


# ── ② 결측 NaN 유지(0 아님) ───────────────────────────────────────────────────────
def test_missing_is_nan_not_zero():
    ds = _DATES[:6]
    codes = np.array(["B1"] * 6 + ["B2"] * 6 + ["B3"] * 6)
    dates = np.tile(np.array(ds), 3)
    mask = np.ones(18, dtype=bool)
    grid = {(c, d): (m7, m30) for c, d, m7, m30 in _grid(["B1", "B2"], ds)}
    grid.pop(("B2", ds[3]))  # B2 의 한 날짜만 그리드에서 빠짐(거래중지 흉내)
    daily = {"B1": {}}  # 그리드에 있지만 언급 0건 → 0("뉴스 없는 날")
    vals = pnp.compute_trading_day_counts(codes, dates, mask, daily, grid, ds[0], k=5)
    assert np.all(vals[0:6] == 0.0), vals[0:6]      # B1: 0, NaN 아님
    assert np.isnan(vals[9])                        # B2 ds[3] → 결측 NaN (0 채움 금지)
    assert np.all(vals[6:9] == 0.0) and np.all(vals[10:12] == 0.0)  # 나머지 B2 → 0
    assert np.all(np.isnan(vals[12:18]))            # B3: 언급 테이블에 없는 종목 → NaN

    # 창이 수집 시작일 이전으로 넘어가면 NaN(부분 정보는 0으로 위장하지 않는다)
    ds2 = _DATES[:8]
    codes2 = np.array(["B4"] * 8)
    dates2 = np.array(ds2)
    mask2 = np.ones(8, dtype=bool)
    grid2 = {(codes2[0], d): (0.0, 0.0) for d in ds2}
    daily2 = {"B4": {ds2[5]: 1}}  # lo 경계(10-06)에 있는 언급 — 창 [lo, date) 에 포함
    vals2 = pnp.compute_trading_day_counts(codes2, dates2, mask2, daily2, grid2,
                                           coverage_start=ds2[5], k=2)
    # ds2[6]: lo=ds2[4] < ds2[5] → NaN. ds2[7]: lo=ds2[5] == coverage_start → 경계 포함, 1.
    assert np.isnan(vals2[6])
    assert vals2[7] == 1.0


# ── ③ 대상 컬럼만 변경·나머지 동일 (+ 멱등: 재실행 리포트 바이트 동일) ────────────
def test_only_target_columns_change(tmp_path):
    # 손계산(창 2026-10-08~11, k=5):
    #   A00001 언급: 10-01=1, 10-05=2, 10-07=3, 10-08=1(당일), 10-10=2(당일), 10-11=1(당일)
    #   news_count_5d  = [5, 6, 6, 6]   (10-08: [10-03,10-08)→2+3=5 | 10-09: [10-04,10-09)→2+3+1=6
    #                                    10-10: [10-05,10-10)→2+3+1=6 | 10-11: [10-06,10-11)→3+1+2=6)
    #   news_count_20d = [6, 7, 7, 9]   (기록 초반 절단: lo=10-01 — 10-08: 1+2+3=6,
    #                                    10-09: 6+1=7 | 10-10: 7(당일 2 제외) | 10-11: 7+2=9)
    #   A00002: 그리드에 있고 언급 0건 → 0.  A00003: 그리드 없음 → NaN.
    daily = [["A00001", "2026-10-01", 1], ["A00001", "2026-10-05", 2],
             ["A00001", "2026-10-07", 3], ["A00001", "2026-10-08", 1],
             ["A00001", "2026-10-10", 2], ["A00001", "2026-10-11", 1]]
    grid = _grid(["A00001", "A00002"], _DATES)
    X, names, dates, codes, price = _panel({"f_a": np.arange(36),
                                            "f_b": 1000 + np.arange(36)})
    panel = tmp_path / "panel.npz"
    _save_panel(panel, X, names, dates, codes, price)
    mentions = tmp_path / "mentions.json"
    _save_mentions(mentions, daily, grid, "2026-10-01")

    out = tmp_path / "panel_v2.npz"
    report = tmp_path / "report.json"
    args = ["--panel", str(panel), "--out", str(out), "--from", FROM, "--to", TO,
            "--mentions-file", str(mentions), "--report", str(report)]
    assert pnp.main(args) == 0

    with np.load(out, allow_pickle=True) as s:
        out_X = s["X"]
        out_names = [str(n) for n in s["feature_names"]]
    assert out_X.shape == X.shape
    assert out_names == [str(n) for n in names]           # ② feature_names 순서 동일
    for j in range(2):                                     # ③ 비대상 컬럼 바이트 동일
        assert np.array_equal(out_X[:, j], X[:, j], equal_nan=True)
    p5, p20 = out_names.index("news_count_5d"), out_names.index("news_count_20d")
    # 창 밖 유지 + 창 안 값 교체 (코드 블록 경계와 무관하게 날짜 마스크로 판정)
    mask_out = ~((dates >= FROM) & (dates <= TO))
    assert np.array_equal(out_X[mask_out, p5], X[mask_out, p5])
    assert np.array_equal(_window_rows(out_X[:, p5]), np.array([[5, 6, 6, 6],
                                                                [0, 0, 0, 0],
                                                                [np.nan] * 4]),
                          equal_nan=True)
    assert np.array_equal(_window_rows(out_X[:, p20]), np.array([[6, 7, 7, 9],
                                                                 [0, 0, 0, 0],
                                                                 [np.nan] * 4]),
                          equal_nan=True)
    # 리포트: 스펙 필드 + 자기검증 + 멱등
    payload = json.loads(report.read_text(encoding="utf-8"))
    c5 = next(c for c in payload["columns"] if c["name"] == "news_count_5d")
    assert c5["action"] == "replace" and c5["k"] == 5
    assert c5["nnz_before"] == 36 and c5["nnz_after"] == 28   # 24(창 밖 1.0)+4(A00001)
    assert c5["nonzero_rows"] == 4 and c5["changed_rows"] == 12
    assert c5["null_rows"] == 4 and c5["null_ratio"] == pytest.approx(4 / 36)
    assert c5["mean"] == pytest.approx(1.46875)                # (24+5+6+6+6)/32
    c20 = next(c for c in payload["columns"] if c["name"] == "news_count_20d")
    assert c20["mean"] == pytest.approx(1.65625)               # (24+6+7+7+9)/32
    v = payload["verify"]
    assert v["rows_identical"] and v["feature_names_identical"]
    assert v["non_target_columns_identical"] and v["target_columns_outside_window_identical"]
    assert v["checks_failed"] == 0
    # 멱등: 같은 인자 재실행 → 리포트 바이트 동일(실행 시각 미포함)
    first = report.read_bytes()
    assert pnp.main(args) == 0
    assert report.read_bytes() == first


# ── ④ --out 이 원본과 같으면 exit 2 ───────────────────────────────────────────────
def test_out_same_as_panel_exits_2(tmp_path):
    X, names, dates, codes, price = _panel({"f_a": np.arange(36)})
    panel = tmp_path / "panel.npz"
    _save_panel(panel, X, names, dates, codes, price)
    before = panel.read_bytes()
    args = ["--panel", str(panel), "--out", str(panel), "--from", FROM, "--to", TO]
    assert pnp.main(args) == 2
    assert panel.read_bytes() == before  # 원본 불변


# ── ⑤ --append 모드 feature_names 길이 증가 + 기존 컬럼 보존 ──────────────────────
def test_append_mode_grows_feature_names_and_keeps_others(tmp_path):
    X, names, dates, codes, price = _panel({"f_a": np.arange(36),
                                            "f_b": 1000 + np.arange(36)},
                                           with_news=False)  # 뉴스 컬럼 없음
    panel = tmp_path / "panel.npz"
    _save_panel(panel, X, names, dates, codes, price)
    mentions = tmp_path / "mentions.json"
    _save_mentions(mentions, [], _grid(["A00001", "A00002"], _DATES), "2026-10-01")

    out = tmp_path / "panel_appended.npz"
    report = tmp_path / "report.json"
    args = ["--panel", str(panel), "--out", str(out), "--from", FROM, "--to", TO,
            "--append", "--mentions-file", str(mentions), "--report", str(report)]
    assert pnp.main(args) == 0

    with np.load(out, allow_pickle=True) as s:
        out_X = s["X"]
        out_names = [str(n) for n in s["feature_names"]]
    assert out_names == [str(n) for n in names] + ["mention_7d", "mention_30d"]
    assert out_X.shape == (36, 4)                     # feature_names 길이 +2
    for j in range(2):                                # 기존 컬럼 바이트 보존
        assert np.array_equal(out_X[:, j], X[:, j], equal_nan=True)
    m7 = _window_rows(out_X[:, 2])
    assert np.array_equal(m7, np.array([[7.0] * 4, [7.0] * 4, [np.nan] * 4]),
                          equal_nan=True)             # 그리드 조인 값 / 없는 종목 NaN
    mask_out = ~((dates >= FROM) & (dates <= TO))
    assert np.all(np.isnan(out_X[mask_out, 2]))  # 창 밖 NaN (신규 컬럼 — 값 없음)
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["mode"] == "append"
    c7 = next(c for c in payload["columns"] if c["name"] == "mention_7d")
    assert c7["nnz_before"] is None and c7["nnz_after"] == 8 and c7["null_rows"] == 4
    assert payload["verify"]["non_target_columns_identical"]
    assert payload["verify"]["target_columns_outside_window_identical"]

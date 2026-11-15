#!/usr/bin/env python3
"""CG285 준비 — 패널에 **종목별 as-of 거래대금(유동성)** 파생 컬럼 4종을 가산만 한다.

왜 (실측 근거, 2026-11-15 엔지니어)
  CG283 실측: 돈 지표 측정기의 체결성 필터가 버리는 71.6% 의 주원인은 상한가(0.3%, 34행)가
  아니라 **거래대금 10억 미만**이다. 그런데 panel_420_asofpatch 의 피처 210개에는 종목별
  거래대금이 없다 — `krx_total_trading_value` 는 날짜별 유니크값 1개(=시장 전체 집계, 시장레벨)
  이고 `volume_ratio_20`·`price_volume` 은 거래량 스케일이지 금액이 아니다. 즉 모델은
  "살 수 있는 종목"의 축을 학습할 수 없다.

  ⚠ 정정(2026-11-15 18:0x): CG285 등록 문구 "거래대금 피처 0개" 는 **부정확**했다.
  `krx_total_trading_value` 가 존재한다 — 다만 median_unique_per_date=1 인 시장레벨 집계다
  (실측). 정확한 진술은 "**종목별(횡단면) 거래대금 피처가 0개**" 이다.

시점정합 (하드)
  파이프라인 규약과 동일하게 **행 날짜 t 이하**만 쓴다(feature_pipeline 의 `trade_date <= %s`).
  라벨은 t 이후 선행수익이므로 t 종가 시점 정보 사용은 누수가 아니다.
  모든 변환은 후방(rolling / 당일 횡단면)만 본다 → 미래값 참조 불가.

  tv_log          = log1p(당일 거래대금)
  tv_rank_cs_20   = 당일까지 20거래일 평균거래대금의 **당일 횡단면 백분위**(0~1)
  tv_rank_cs_60   = 위 60거래일판
  tv_ratio_5_20   = 5거래일 평균 / 20거래일 평균 (거래대금 급증도)

사용 (=/app/scripts/_cg285_tv_panel.py 로도 보임)
  python3 scripts/_cg285_tv_panel.py            # 빌드 + 자기검정
  python3 scripts/_cg285_tv_panel.py --test     # 자기검정만
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

SRC = "/app/app/models/wf/panel_420_asofpatch.npz"
OUT = "/app/app/models/wf/panel_420_tv.npz"
LOOKBACK_CALENDAR_DAYS = 120      # 60거래일 MA 확보용 버퍼
NEW = ["tv_log", "tv_rank_cs_20", "tv_rank_cs_60", "tv_ratio_5_20"]


def _connect():
    sys.path.insert(0, "/app")
    sys.path.insert(0, "/app/scripts")
    import train_curated as tc  # noqa: F401  (ml 로더 경유)
    from extra_experiments import _load_driver
    ml = _load_driver()
    return ml.connect_pg()


# ── 데이터 적재 ────────────────────────────────────────────────────────────────
def load_tv(codes, dmin, dmax):
    """DB market_data 에서 (code, date) 별 거래대금을 읽는다."""
    pg = _connect()
    try:
        cur = pg.cursor()
        cur.execute(
            """
            SELECT stock_code, trade_date::text, trading_value
            FROM market_data
            WHERE stock_code = ANY(%s)
              AND trade_date >= (%s::date - %s)
              AND trade_date <= %s
            ORDER BY stock_code, trade_date
            """,
            (list(codes), dmin, LOOKBACK_CALENDAR_DAYS, dmax),
        )
        rows = cur.fetchall()
        cur.close()
    finally:
        try:
            pg.close()
        except Exception:
            pass
    df = pd.DataFrame(rows, columns=["stock_code", "date", "tv"])
    df["tv"] = pd.to_numeric(df["tv"], errors="coerce")
    # 거래정지/결측 → 0 (패널 규약: 금액 0 은 '거래 없음'이라는 사실 자체가 정보다)
    df["tv"] = df["tv"].fillna(0.0).clip(lower=0.0)
    return df


def compute_features(tv_df: pd.DataFrame, panel: pd.DataFrame) -> pd.DataFrame:
    """종목별 후방 이동평균 → 패널 행에 매핑 → 당일 횡단면 백분위."""
    # groupby-rolling 은 인덱스 정렬이 필요 → 정렬 프레임에서 직접 계산
    srt = tv_df.sort_values(["stock_code", "date"], kind="mergesort").reset_index(drop=True)
    grp = srt.groupby("stock_code", sort=False)["tv"]
    srt["ma5"] = grp.transform(lambda s: s.rolling(5, min_periods=1).mean())
    srt["ma20"] = grp.transform(lambda s: s.rolling(20, min_periods=5).mean())
    srt["ma60"] = grp.transform(lambda s: s.rolling(60, min_periods=20).mean())
    srt["tv_log"] = np.log1p(srt["tv"].to_numpy())
    with np.errstate(divide="ignore", invalid="ignore"):
        srt["tv_ratio_5_20"] = np.where(srt["ma20"].to_numpy() > 0,
                                        srt["ma5"].to_numpy() / srt["ma20"].to_numpy(), np.nan)

    # 패널 행에 left join (패널이 기준 — 패널에 없는 (code,date) 는 버린다)
    m = panel.merge(srt[["stock_code", "date", "ma20", "ma60", "tv_log", "tv_ratio_5_20"]],
                    on=["stock_code", "date"], how="left", validate="one_to_one")
    assert len(m) == len(panel), (len(m), len(panel))

    # 당일 횡단면 백분위(패널 유니버스 기준). rank 는 NaN 을 자동 제외한다.
    for col, src in (("tv_rank_cs_20", "ma20"), ("tv_rank_cs_60", "ma60")):
        m[col] = m.groupby("date")[src].rank(pct=True)
    return m


# ── 빌드 ──────────────────────────────────────────────────────────────────────
def _load_panel(p):
    z = np.load(p, allow_pickle=True)
    return (np.asarray(z["X"], dtype=np.float32), [str(x) for x in z["feature_names"]],
            np.asarray(z["dates"]).astype(str), np.asarray(z["codes"]).astype(str),
            (np.asarray(z["price"], dtype=float) if "price" in z else None))


def build(src: str = SRC, out: str = OUT) -> dict:
    X, names, dates, codes, price = _load_panel(src)
    panel = pd.DataFrame({"stock_code": codes, "date": dates, "_row": np.arange(len(dates))})
    tv_df = load_tv(sorted(set(codes)), str(panel["date"].min()), str(panel["date"].max()))
    m = compute_features(tv_df, panel)
    m = m.sort_values("_row")

    add = {c: pd.to_numeric(m[c], errors="coerce").to_numpy(dtype=float) for c in NEW}
    X2 = np.hstack([X, np.column_stack([add[c] for c in NEW]).astype(np.float32)])
    np.savez_compressed(out, X=X2, feature_names=np.array(names + NEW, dtype=object),
                        dates=dates, codes=codes, **({"price": price} if price is not None else {}))

    # ── 단일피처 AUC (누수 게이트: >0.75 면 즉시 반려) ──────────────────────────
    aucs = _single_feature_auc(out)
    return {
        "src": src, "out": out, "n_rows": int(X.shape[0]),
        "n_feat_before": len(names), "n_feat_after": len(names) + len(NEW),
        "added": NEW,
        "coverage": {c: round(float(np.isfinite(add[c]).mean()), 4) for c in NEW},
        "nonzero": {c: round(float(np.nan_to_num(add[c]).astype(bool).mean()), 4) for c in NEW},
        "const_ratio": {c: round(float(np.nanmax(pd.Series(add[c]).value_counts(dropna=True).to_numpy())
                                      / max(1, pd.Series(add[c]).notna().sum())), 4) for c in NEW},
        "single_feature_auc": aucs,
        "mtime_src": os.path.getmtime(src),
        "ts": pd.Timestamp.now(tz="Asia/Seoul").isoformat(),
    }


def _single_feature_auc(panel_path: str) -> dict:
    """누수 게이트 — 같은 라벨(스무딩 h5 q0.30)에 대한 단일피처 AUC."""
    try:
        sys.path.insert(0, "/app/scripts")
        import wf_wave as W
        from sklearn.metrics import roc_auc_score
    except Exception as e:      # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}
    X, names, dates, codes, price = _load_panel(panel_path)
    df = pd.DataFrame(X, columns=names)
    df["stock_code"] = codes
    df["date"] = dates
    df["price"] = price
    df = df.sort_values(["stock_code", "date"], kind="mergesort").reset_index(drop=True)
    y = W.make_labels(df, "smooth", 5, 0.30)
    mask = np.isfinite(np.asarray(y, dtype=float))
    out = {}
    for c in NEW:
        v = df[c].to_numpy(dtype=float)
        m = mask & np.isfinite(v)
        if m.sum() < 100 or len(np.unique(y[m])) < 2:
            out[c] = None
            continue
        out[c] = round(float(roc_auc_score(y[m], v[m])), 4)
    out["_n"] = int(mask.sum())
    return out


# ── 자기검정 ──────────────────────────────────────────────────────────────────
def selftest(src: str = SRC, out: str = OUT, n_probe: int = 60) -> int:
    checks, fails = [], 0

    def ck(name, ok, detail=""):
        nonlocal fails
        checks.append({"check": name, "pass": bool(ok), "detail": detail})
        if not ok:
            fails += 1

    Xa, na, da, ca, _ = _load_panel(src)
    Xb, nb, db, cb, _ = _load_panel(out)
    ck("원본 210열 비트 동일(NaN 위치 포함)",
       np.array_equal(np.nan_to_num(Xa, nan=-9e9), np.nan_to_num(Xb[:, :Xa.shape[1]], nan=-9e9)))
    ck("행/날짜/코드 동일", len(da) == len(db) and np.array_equal(da, db) and np.array_equal(ca, cb))
    ck("신규 열 4개", len(nb) - len(na) == len(NEW), f"{len(na)}→{len(nb)}")
    ck("원본 이름 보존", nb[:len(na)] == na)

    # ② 횡단면 rank 범위
    for c in ("tv_rank_cs_20", "tv_rank_cs_60"):
        v = Xb[:, nb.index(c)].astype(float)
        v = v[np.isfinite(v)]
        ck(f"{c} ∈ (0,1]", (v.min() > 0) and (v.max() <= 1.0 + 1e-6),
           f"min={v.min():.4f} max={v.max():.4f} n={len(v)}")

    # ③ 미래참조 없음: 날짜 T 로 프레임을 자른 뒤 재계산하면 T 이하 행이 동일해야 한다
    tv_df = load_tv(sorted(set(ca.tolist())), str(min(da.tolist())), str(max(da.tolist())))
    panel = pd.DataFrame({"stock_code": ca, "date": da, "_row": np.arange(len(da))})
    full = compute_features(tv_df, panel).sort_values("_row").reset_index(drop=True)
    probe_dates = sorted(set(da))[len(set(da)) // 2: len(set(da)) // 2 + 3]
    bad = 0
    for T in probe_dates:
        tv_cut = tv_df[tv_df["date"] <= T]
        pan_cut = panel[panel["date"] <= T]
        cut = compute_features(tv_cut, pan_cut).sort_values("_row").reset_index(drop=True)
        same_rows = full[full["date"] <= T].reset_index(drop=True)
        for c in NEW:
            a = same_rows[c].to_numpy(dtype=float)
            b = cut[c].to_numpy(dtype=float)
            if not np.allclose(np.nan_to_num(a, nan=-9e9), np.nan_to_num(b, nan=-9e9), atol=1e-9):
                bad += 1
    ck("절단 재계산 동일(미래참조 없음)", bad == 0, f"불일치 {bad}건 · 프로브 날짜 {probe_dates}")

    # ④ 저장값 == 재계산값
    dl = {}
    for c in NEW:
        a = Xb[:, nb.index(c)].astype(float)
        b = full[c].to_numpy(dtype=float)
        dl[c] = bool(np.allclose(np.nan_to_num(a, nan=-9e9), np.nan_to_num(b, nan=-9e9), atol=1e-5))
    ck("저장값 == 재계산값", all(dl.values()), json.dumps(dl))

    for ch in checks:
        print(("PASS " if ch["pass"] else "FAIL ") + ch["check"] + (f"  [{ch['detail']}]" if ch["detail"] else ""))
    print(f"\n{len(checks) - fails}/{len(checks)} PASS")
    return 0 if fails == 0 else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=SRC)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--test", action="store_true")
    a = ap.parse_args()
    if a.test:
        raise SystemExit(selftest(a.src, a.out))
    meta = build(a.src, a.out)
    print(json.dumps(meta, ensure_ascii=False, indent=1))
    print("\n--- 자기검정 ---")
    rc = selftest(a.src, a.out)
    raise SystemExit(rc)


if __name__ == "__main__":
    main()

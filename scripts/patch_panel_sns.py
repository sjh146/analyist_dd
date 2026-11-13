#!/usr/bin/env python3
"""패널의 **SNS 컬럼만** `sns_post_features` 의 (종목·일) 값으로 교체한다 — 전체 재빌드 대체 경로.

WHY (2026-11-13, CG257 — CG252 의 비용 대체)
- CG252 는 'SNS 관측률 12% → 30%+' 를 재려고 `wf_wave.py` 로 패널 전체(200종목·420일)를 다시 깔려
  했는데 est 900분(15시간)이다. 그런데 실제로 낡은 것은 **SNS 8개 원천 열**뿐이고, 원천
  `sns_post_features` 는 이미 최신(2026-11-13 · 18,807행 · 366종목)이다.
- `patch_panel_fin_rcept.py` 가 재무 19열에 대해 이미 같은 '같은 행 위 컬럼 교체' 방식을 쓴다
  (A/B 통제가 깨끗하고 몇 분이면 끝난다). 같은 논리를 SNS 에 적용한다.

as-of 규칙(중요)
- 원 파이프라인과 **동일 규칙**으로 `trade_date` 당일 값만 쓴다(전방 채움·미래일 사용 금지).
  격자에 없는 (종목·일)은 0 을 유지한다 — '없음'을 과거 값으로 덮으면 그 자체가 시점 위반이다.
- `kalman_*` 2열은 원본과 중복(CG248 실측)이라 그대로 덮어도 정보가 늘지 않는다. 기록만 한다.

사용 (xgboost-ml 컨테이너, cwd=/app):
    docker exec stock_xgboost_ml python /app/scripts/patch_panel_sns.py \
        --in app/models/wf/panel_200liq_fresh.npz \
        --out app/models/wf/panel_200liq_sns.npz \
        --report /app/reports/overnight/cg257_sns_patch.json
"""
from __future__ import annotations

import argparse
import json
import os

import numpy as np
import psycopg2

# 패널 열 이름 → sns_post_features 열 이름 (실효 신규 정보는 sentiment·attention·momentum·post_count)
COL_MAP = {
    "sns_sentiment_score": "sentiment_score",
    "sns_attention_score": "attention_score",
    "sns_momentum_score": "momentum_score",
    "sns_author_quality_score": "author_quality_score",
    "sns_post_count": "post_count",
    "sns_bot_filtered_count": "bot_filtered_count",
    "kalman_sentiment": "kalman_sentiment",
    "kalman_attention": "kalman_attention",
}
# 이 열들이 하나라도 비영이면 그 (종목·일)은 'SNS 관측 있음'으로 센다(관측률 지표)
OBS_COLS = ["sns_sentiment_score", "sns_attention_score", "sns_momentum_score",
            "sns_author_quality_score", "sns_post_count"]


def _pg_connect():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def _obs_mask(row):
    """'관측 있음' = 유한하고 0 이 아닌 값. NaN 은 0 이 아니다(품질계약 ②) — NaN != 0 이 True 라
    `col != 0` 으로 세면 전부 관측으로 오독한다(2026-11-13 실측 함정: 98% NaN 컬럼의 비영이 100%로
    계산됐다)."""
    with np.errstate(invalid="ignore"):
        return np.isfinite(row) & (row != 0)


def _obs_rate(X, names, idxs):
    if not idxs:
        return 0.0
    sub = X[:, idxs]
    return round(100.0 * float(np.mean(np.any(_obs_mask(sub), axis=1))), 2)


def _nz_pct(col):
    return round(100.0 * float(np.mean(_obs_mask(col))), 2)


def main() -> int:
    ap = argparse.ArgumentParser(description="패널 SNS 컬럼 as-of 교체")
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", dest="dst", required=True)
    ap.add_argument("--cols", default="", help="쉼표 구분 패널 컬럼(기본: 패널∩COL_MAP)")
    ap.add_argument("--report", default="")
    args = ap.parse_args()

    z = np.load(args.src, allow_pickle=True)
    X = z["X"].astype(np.float64, copy=True)
    names = [str(n) for n in z["feature_names"]]
    dates = [str(d)[:10] for d in z["dates"]]
    codes = [str(c) for c in z["codes"]]

    want = ([c.strip() for c in args.cols.split(",") if c.strip()] if args.cols
            else [c for c in COL_MAP if c in names])
    bad = [c for c in want if c not in names or c not in COL_MAP]
    if bad:
        print(f"STOP: 패널에 없거나 매핑 없는 컬럼 {bad}")
        return 1
    if not want:
        print("STOP: 교체할 SNS 컬럼이 없음")
        return 1

    idx = {c: names.index(c) for c in want}
    db_cols = [COL_MAP[c] for c in want]
    obs_idx = [names.index(c) for c in OBS_COLS if c in names]
    uniq_codes = sorted(set(codes))
    dmin, dmax = min(dates), max(dates)

    conn = _pg_connect()
    cur = conn.cursor()
    ph = "(" + ",".join(["%s"] * len(uniq_codes)) + ")"
    sel = ", ".join(["stock_code", "trade_date::text"] + db_cols)
    cur.execute(
        f"SELECT {sel} FROM sns_post_features "
        f"WHERE stock_code IN {ph} AND trade_date BETWEEN %s AND %s",
        uniq_codes + [dmin, dmax],
    )
    grid = {}
    for row in cur.fetchall():
        grid[(str(row[0]), str(row[1])[:10])] = [
            (float(v) if v is not None else 0.0) for v in row[2:]
        ]
    conn.close()

    obs_before = _obs_rate(X, names, obs_idx)
    before = {c: {"nz_pct": _nz_pct(X[:, idx[c]]),
                  "nan_pct": round(100.0 * float(np.mean(~np.isfinite(X[:, idx[c]]))), 2),
                  "median": float(np.nanmedian(X[:, idx[c]]))} for c in want}
    hit = 0
    changed = {c: 0 for c in want}
    for i in range(len(codes)):
        g = grid.get((codes[i], dates[i]))
        if g is None:
            continue
        hit += 1
        for j, c in enumerate(want):
            if g[j] != X[i, idx[c]]:
                changed[c] += 1
            X[i, idx[c]] = g[j]

    obs_after = _obs_rate(X, names, obs_idx)
    rep = {
        "panel_in": args.src, "panel_out": args.dst,
        "rows": len(codes), "codes": len(uniq_codes), "date_range": [dmin, dmax],
        "grid_rows": len(grid), "grid_hit": hit,
        "grid_hit_pct": round(100.0 * hit / max(len(codes), 1), 2),
        "sns_observation_rate_pct": {"before": obs_before, "after": obs_after},
        "cols": {},
        "note": ("trade_date 당일 값만 사용(전방채움 없음) · 격자 미적중 행은 0 유지 "
                 "· kalman_* 2열은 원본 중복(CG248)"),
    }
    print(f"패널 {len(codes)}행 · 격자 적중 {hit} ({rep['grid_hit_pct']}%) · "
          f"SNS 관측률 {obs_before}% → {obs_after}%")
    for c in want:
        after_nz = _nz_pct(X[:, idx[c]])
        rep["cols"][c] = {
            "db_col": COL_MAP[c], "nz_before_pct": before[c]["nz_pct"],
            "nz_after_pct": after_nz,
            "changed_pct": round(100.0 * changed[c] / max(len(codes), 1), 2),
            "median_before": before[c]["median"],
            "median_after": float(np.nanmedian(X[:, idx[c]])),
        }
        print(f"  {c:26s} nz {before[c]['nz_pct']:5.2f}%→{after_nz:5.2f}% "
              f"(nan {before[c]['nan_pct']:5.2f}%) changed {rep['cols'][c]['changed_pct']:5.2f}%")

    out = dict(z)
    out["X"] = X
    os.makedirs(os.path.dirname(args.dst) or ".", exist_ok=True)
    np.savez_compressed(args.dst, **out)
    print(f"저장: {args.dst}")

    meta_path = args.dst + ".build_meta.json"
    if os.path.exists(meta_path):
        try:
            meta = json.load(open(meta_path, encoding="utf-8"))
        except Exception:
            meta = {}
    else:
        meta = {}
    meta.update({
        "builder": "patch_panel_sns", "created_at": np.datetime64("now", "s").astype(str),
        "source_panel": os.path.basename(args.src), "patched_cols": want,
        "grid_rows": len(grid), "sns_observation_rate_pct": rep["sns_observation_rate_pct"],
    })
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=2)
        print(f"검증 리포트: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

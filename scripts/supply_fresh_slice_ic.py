#!/usr/bin/env python3
"""supply_fresh_slice_ic — 수급·시장 피처의 날짜별 횡단면 IC(스피어만)를 구간 비교로 낸다.

WHY (2026-11-09): 호스트가 2026-10-11~11-09 30일 꺼져 있었고 foreign_institutional(수급 원천)이
2026-10-08 에 멈춰 21거래일이 비었다. 복구 후 "새로 들어온 21일이 실제로 정보를 담고 있는가"를
패널 재빌드(수 시간) 없이 분 단위로 확인하는 용도다. 목적은 **데이터 계약 점검**이지 승격 근거가
아니다(단일 구간 IC 로 승격 판단 금지 — SKILL 최우선 규칙).

사용:
  set -a; . ./.env; set +a; export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434
  /usr/bin/python3 scripts/supply_fresh_slice_ic.py --start 2026-10-09 --end 2026-11-06 --label recovered
  /usr/bin/python3 scripts/supply_fresh_slice_ic.py --start 2026-08-01 --end 2026-09-23 --label control
  ... --json out.json 두 구간을 각각 저장해 비교

원칙:
  · 피처는 D일 값, 수익률은 D→D+h (h=1,5) — 미래 정보 없음.
  · 종목 교집합은 학습 유니버스 파일(기본 data/kis/train_universe_200.txt).
  · 종목 상수(유니크 1) 피처는 IC 계산 전에 표시만 하고 제외한다(횡단면 변별력 0).
  · 표본 게이트: 날짜당 종목 ≥ 30, 구간 날짜 ≥ 5 아니면 판정 불가로 표기.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date

import numpy as np
import psycopg2

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEATURES = [
    "institution_net_buy", "institution_net_buy_5d", "foreign_net_buy", "foreign_net_buy_5d",
    "foreign_ownership_pct", "retail_ownership_pct", "short_selling_ratio",
    "momentum_3_12m", "momentum_ni", "momentum_op", "krx_advance_decline_ratio",
    "krx_total_trading_value", "market_breadth", "market_impact_score",
    "relative_strength", "bb_position",
]
MARKET_LEVEL = {"krx_advance_decline_ratio", "krx_total_trading_value", "market_breadth"}


def _ranks(a: np.ndarray) -> np.ndarray:
    """평균 순위(타이 처리) — scipy 없이 스피어만."""
    order = np.argsort(a, kind="mergesort")
    ranks = np.empty(len(a), dtype=float)
    ranks[order] = np.arange(1, len(a) + 1, dtype=float)
    # 동률 평균화
    uniq, inv, cnt = np.unique(a, return_inverse=True, return_counts=True)
    if (cnt > 1).any():
        sums = np.zeros(len(uniq))
        np.add.at(sums, inv, ranks)
        ranks = (sums / cnt)[inv]
    return ranks


def spearman(x: np.ndarray, y: np.ndarray) -> float:
    if len(x) < 5:
        return float("nan")
    rx, ry = _ranks(x), _ranks(y)
    rx = rx - rx.mean()
    ry = ry - ry.mean()
    den = np.sqrt((rx ** 2).sum() * (ry ** 2).sum())
    return float("nan") if den == 0 else float((rx * ry).sum() / den)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--horizons", default="1,5")
    ap.add_argument("--label", default="")
    ap.add_argument("--stocks-file", default=os.path.join(PROJ, "data/kis/train_universe_200.txt"))
    ap.add_argument("--min-codes-per-day", type=int, default=30)
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    codes = [c.strip() for c in open(args.stocks_file) if c.strip()]
    horizons = [int(h) for h in args.horizons.split(",")]
    conn = psycopg2.connect(host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
                            port=int(os.environ.get("POSTGRES_PORT", "5434")),
                            user=os.environ.get("POSTGRES_USER", "stock_user"),
                            password=os.environ.get("POSTGRES_PASSWORD", ""),
                            dbname=os.environ.get("POSTGRES_DB", "stock_trading"))
    cur = conn.cursor()

    # 가격: end 이후 최대 10거래일 여유까지 읽어 forward return 계산
    cur.execute("""
        SELECT stock_code, trade_date, close_price, volume
          FROM market_data
         WHERE stock_code = ANY(%s) AND trade_date >= %s::date - interval '10 days'
         ORDER BY trade_date""", (codes, args.start))
    px: dict[str, list] = {}
    for c, d, close, vol in cur.fetchall():
        px.setdefault(c, []).append((d, float(close) if close else None, float(vol or 0)))
    dates = sorted({d for v in px.values() for d, _, _ in v})
    idx = {c: {d: i for i, (d, _, _) in enumerate(v)} for c, v in px.items()}

    cur.execute(f"""
        SELECT stock_code, trade_date, {', '.join(FEATURES)}
          FROM supply_market_features
         WHERE stock_code = ANY(%s) AND trade_date BETWEEN %s AND %s""",
                (codes, args.start, args.end))
    rows = cur.fetchall()

    # 낡은 표를 새 결과로 오독하지 않기 위한 가드: 피처 테이블이 구간 끝까지 차 있지 않으면
    # 그 구간을 잰 것이 아니다(2026-11-09: 원천 21거래일 공백 사고).
    cur.execute("SELECT max(trade_date) FROM supply_market_features")
    _r = cur.fetchone()
    feat_max = _r[0] if _r else None
    out_max = date.fromisoformat(args.end)
    if feat_max is None or feat_max < out_max:
        print(f"중단: supply_market_features max={feat_max} < 요청 구간 끝 {args.end} — "
              f"원천 백필/빌드가 끝나지 않았다(빌드: scripts/build_supply_market_features.py).")
        return 4


    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    by_date: dict = {}
    for r in rows:
        c, d = r[0], r[1]
        if not (start <= d <= end):
            continue
        by_date.setdefault(d, []).append((c, r[2:]))

    out = {"label": args.label, "start": args.start, "end": args.end,
           "universe_file": os.path.relpath(args.stocks_file, PROJ), "n_universe": len(codes),
           "n_rows": len(rows), "n_dates": len(by_date), "features": {}, "errors": []}

    for h in horizons:
        per_feat = {f: [] for f in FEATURES}          # 날짜별 IC
        npts = {f: 0 for f in FEATURES}
        for d in sorted(by_date):
            xs, ys = [], []
            for code, vals in by_date[d]:
                v = px.get(code)
                i = idx.get(code, {}).get(d)
                if v is None or i is None or i + h >= len(v):
                    continue
                c0, c1 = v[i][1], v[i + h][1]
                if not c0 or not c1:
                    continue
                xs.append((code, vals))
                ys.append(c1 / c0 - 1.0)
            if len(xs) < args.min_codes_per_day:
                continue
            yret = np.asarray(ys, dtype=float)
            if float(np.std(yret)) == 0:
                continue
            for j, f in enumerate(FEATURES):
                col = np.asarray([np.nan if r[1][j] is None else float(r[1][j]) for r in xs])
                mask = np.isfinite(col)
                if mask.sum() < args.min_codes_per_day:
                    continue
                if float(np.nanstd(col[mask])) == 0:
                    continue      # 종목 상수/무변동 → 변별력 없음
                ic = spearman(col[mask], yret[mask])
                if np.isfinite(ic):
                    per_feat[f].append(ic)
                    npts[f] += int(mask.sum())
        for f in FEATURES:
            arr = np.asarray(per_feat[f], dtype=float)
            if arr.size == 0:
                out["features"][f"{f}@h{h}"] = {"n_dates": 0, "verdict": "측정 불가(비영 표본 없음)"}
                continue
            mean = float(arr.mean())
            sd = float(arr.std(ddof=1)) if arr.size > 1 else float("nan")
            t = mean / (sd / np.sqrt(arr.size)) if arr.size > 1 and sd > 0 else float("nan")
            out["features"][f"{f}@h{h}"] = {
                "n_dates": int(arr.size), "n_points": int(npts[f]),
                "ic_mean": round(mean, 4), "ic_sd": None if not np.isfinite(sd) else round(sd, 4),
                "t": None if not np.isfinite(t) else round(float(t), 2),
                "pos_frac": round(float((arr > 0).mean()), 3),
                "market_level": f in MARKET_LEVEL,
                "verdict": ("신호 후보 |IC|≥0.03·t≥2" if (abs(mean) >= 0.03 and np.isfinite(t) and abs(t) >= 2)
                            else "노이즈(|IC|<0.03 또는 t<2)"),
            }
    conn.close()

    print(f"구간 {args.start}~{args.end} ({args.label}) · 날짜 {out['n_dates']} · 행 {out['n_rows']} · 유니버스 {len(codes)}")
    for h in horizons:
        print(f"\n[h={h}] 피처별 횡단면 IC")
        for f in FEATURES:
            v = out["features"][f"{f}@h{h}"]
            if v.get("n_dates", 0) == 0:
                print(f"  {f:28s} 측정 불가")
                continue
            print(f"  {f:28s} IC {v['ic_mean']:+.4f} sd {v['ic_sd']} t {v['t']} "
                  f"양(+) {v['pos_frac']:.2f}·{v['n_dates']}일  {v['verdict']}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        print(f"\n저장: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

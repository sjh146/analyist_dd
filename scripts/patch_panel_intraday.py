#!/usr/bin/env python3
"""패널에 **인트라데이(분봉) 피처 열만** as-of 로 추가한다 (전체 재빌드 대체).

WHY (2026-10-06, CG101 준비물 ③)
- 데이터 축의 마지막 미측정 정보 클래스는 가격경로(분봉)다. 현 `minute_bars` 는 수집기 결함(XR26)으로
  15:01~15:30 30봉·6~7일 표본뿐이라 패널 전체 재빌드는 무의미하다(대부분 결측). 수집기 수리·백필이
  승인되면 **행 집합을 그대로 둔 채 열만** 붙여 같은 런·같은 행 A/B 를 돌려야 통제가 깨끗하다
  (patch_panel_asof/disclosure/fin_rcept 와 같은 논리).
- 정의의 단일 진실원은 `app/feature_engine/intraday_features.py` 다(스크린 `intraday_feature_screen.py`
  의 l30_* 와 원소 단위 동일). 이 도구는 그 모듈을 그대로 import 해 계산한다 — 복제하지 마라.

as-of: 학습행 (종목, t) 은 종가 시점의 결정이고 라벨은 close(t)→close(t+h) 이므로 t 세션(≤15:30)의
봉은 관측 가능하다. 봉이 없는 (종목, 날짜)는 0.0(파이프라인 결측 규약) — 커버리지(비영 비율)를
리포트에 찍어 판독한다.

사용 (xgboost-ml 컨테이너, cwd=/app):
    docker exec -e PYTHONPATH=/app stock_xgboost_ml python /app/scripts/patch_panel_intraday.py \
        --in app/models/wf/panel_prod200.npz --out app/models/wf/panel_prod200_id.npz \
        --report /app/reports/overnight/panel_intraday_patch.json
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import defaultdict

import numpy as np
import psycopg2

from app.feature_engine.intraday_features import compute_intraday_features, feature_names


def _pg_connect():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="패널에 인트라데이 피처 열 as-of 추가")
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", dest="dst", required=True)
    ap.add_argument("--report", default="", help="커버리지 리포트 json 경로")
    ap.add_argument("--limit", type=int, default=0, help="앞 N행만 처리(스모크, 0=전체)")
    args = ap.parse_args()

    new_names = feature_names()
    z = np.load(args.src, allow_pickle=True)
    X = z["X"].astype(np.float32, copy=True)
    names = [str(n) for n in z["feature_names"]]
    dates = [str(d)[:10] for d in z["dates"]]
    codes = [str(c) for c in z["codes"]]
    price = z["price"].astype(np.float64, copy=True)

    if X.shape[1] != len(names):
        print(f"STOP: X 열 {X.shape[1]}개 vs 피처명 {len(names)}개 — 이름↔열 매핑이 깨진 파일이다.")
        return 1
    clash = [n for n in new_names if n in names]
    if clash:
        print(f"STOP: 이미 있는 열 {clash[:5]} — 덮어쓰려면 원본을 고치지 말고 새 출력 파일명을 써라.")
        return 1

    n_rows = len(dates)
    take = n_rows if args.limit <= 0 else min(args.limit, n_rows)
    pairs = [(codes[i], dates[i]) for i in range(n_rows)]
    want_codes = sorted({c for c, _ in pairs})
    dmin, dmax = min(d for _, d in pairs), max(d for _, d in pairs)

    t0 = time.time()
    conn = _pg_connect()
    cur = conn.cursor()
    # 봉: 패널에 등장하는 종목·구간만(불필요한 전량 스캔 방지). 시각 문자열 사전순 = 시각순.
    bars = defaultdict(list)
    cur.execute(
        "SELECT stock_code, trade_date, \"time\", open_price, high_price, low_price, close_price, "
        "volume, trading_value FROM minute_bars "
        "WHERE stock_code = ANY(%s) AND trade_date BETWEEN %s::date AND %s::date "
        "ORDER BY stock_code, trade_date, \"time\"",
        (want_codes, dmin, dmax))
    for code, dt, tm, o, h, l, c, v, tv in cur.fetchall():
        bars[(str(code), str(dt)[:10])].append(
            (str(tm), float(o or 0), float(h or 0), float(l or 0), float(c or 0),
             float(v or 0), float(tv or 0)))
    # 일 거래량·거래대금(vol_share/tv_share 분모) — market_data.
    daily = {}
    cur.execute(
        "SELECT stock_code, trade_date, volume, trading_value FROM market_data "
        "WHERE stock_code = ANY(%s) AND trade_date BETWEEN %s::date AND %s::date",
        (want_codes, dmin, dmax))
    for code, dt, v, tv in cur.fetchall():
        daily[(str(code), str(dt)[:10])] = (float(v or 0.0), float(tv or 0.0))
    conn.close()

    added = np.zeros((n_rows, len(new_names)), dtype=np.float32)
    patched = 0
    for i in range(take):
        key = (codes[i], dates[i])
        bl = bars.get(key)
        if not bl:
            continue
        feats = compute_intraday_features(bl, daily.get(key))
        added[i] = np.array([feats[n] for n in new_names], dtype=np.float32)
        patched += 1

    Xout = np.concatenate([X, added], axis=1)
    names_out = names + new_names
    # 값이 하나라도 들어간 (행, 열) 수 — 교체가 실제로 일어났다는 증명.
    cell_report = {}
    for j, n in enumerate(new_names):
        col = added[:, j]
        nz = int(np.count_nonzero(col))
        cell_report[n] = {
            "nonzero": nz,
            "nonzero_ratio": round(nz / max(1, take), 6),
            "min": float(col.min()) if nz else 0.0,
            "max": float(col.max()) if nz else 0.0,
        }
    summary = {
        "src": args.src, "dst": args.dst,
        "rows": n_rows, "rows_scanned": take, "rows_with_bars": patched,
        "with_bars_ratio": round(patched / max(1, take), 6),
        "date_range": [dmin, dmax], "n_codes": len(want_codes),
        "n_features_before": len(names), "n_features_after": len(names_out),
        "minute_bars_rows_loaded": sum(len(v) for v in bars.values()),
        "columns": cell_report,
        "elapsed_sec": round(time.time() - t0, 1),
        "note": "0.0 = 결측(봉 없음). 커버리지가 낮으면 열을 붙여도 top30 선별에 진입하지 않는다"
                "(희석계수=비영²·CG67/CG76 실측).",
    }

    os.makedirs(os.path.dirname(args.dst) or ".", exist_ok=True)
    tmp = args.dst + ".tmp.npz"
    np.savez_compressed(tmp, X=Xout.astype(np.float32), feature_names=np.array(names_out),
                        dates=np.array([str(d) for d in z["dates"]]),
                        codes=np.array([str(c) for c in z["codes"]]), price=price)
    os.replace(tmp, args.dst)

    if args.report:
        os.makedirs(os.path.dirname(args.report) or ".", exist_ok=True)
        with open(args.report, "w") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in summary.items() if k != "columns"},
                     ensure_ascii=False))
    print("컬럼 커버리지(비영):",
          {k: v["nonzero"] for k, v in cell_report.items() if v["nonzero"]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

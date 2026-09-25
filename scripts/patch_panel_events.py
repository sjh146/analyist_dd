#!/usr/bin/env python3
"""패널에 **이벤트 피처 18개를 추가**한다 (기존 피처는 그대로 두고 컬럼만 append).

WHY (2026-09-25)
  죽어 있던 event_*_5d 18개를 부활시켜 모델에 투입했을 때 로버스트 AUC가 움직이는지 측정해야 한다
  (docs/DEAD_FEATURE_REVIVAL.md #1, 백로그 L3). 전체 패널을 재빌드하면 150종목×281일 = 몇 시간이
  걸리므로, 검증된 컬럼 패치 방식(patch_panel_asof.py 와 동일 전략)으로 **같은 행·같은 다른 피처
  위에서** 컬럼만 추가한다 → A/B 통제가 깨끗하고 수 분 내 끝난다.

설계
  - event_features 는 **0 이 아닌 행만** 저장돼 있다(공간 절약) → 결측은 0 으로 채운다.
    이는 "직전 5거래일에 이벤트 없음"과 같은 의미다(as-of 규율: 창은 D-5..D-1).
  - 추가 컬럼의 종목당 유니크값과 non-zero 비율을 출력해 **죽은 채로 넣지 않았는지** 확인한다.
  - 피처 순서는 기존 뒤에 붙인다(기존 인덱스 보존 → `--only` 실험 선택자 영향 없음).

사용 (xgboost-ml 컨테이너, cwd=/app):
    docker exec stock_xgboost_ml python scripts/patch_panel_events.py \
        --in app/models/wf/panel_420_asofpatch.npz \
        --out app/models/wf/panel_420_asofpatch_ev.npz
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np

sys.path.insert(0, "/app")

import psycopg2  # noqa: E402

EVENT_COLS = [
    "event_capital_increase_5d", "event_cb_bw_5d", "event_contract_5d", "event_delisting_5d",
    "event_disaster_5d", "event_exec_change_5d", "event_litigation_5d", "event_mna_5d",
    "event_new_product_5d", "event_partnership_5d", "event_patent_5d", "event_realized_5d",
    "event_recall_5d", "event_regulation_5d", "event_stake_change_5d", "event_treasury_5d",
    "disclosure_count_5d",
]


def _pg_connect():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="패널에 이벤트 피처 컬럼 추가")
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", dest="dst", required=True)
    ap.add_argument("--fill", type=float, default=0.0, help="결측(이벤트 없음) 채움값")
    args = ap.parse_args()

    z = np.load(args.src, allow_pickle=True)
    X = z["X"].astype(np.float32, copy=True)
    names = [str(n) for n in z["feature_names"]]
    dates = [str(d)[:10] for d in z["dates"]]
    codes = [str(c) for c in z["codes"]]
    n_rows, n_feat = X.shape
    print(f"[이벤트 패치] 입력 {n_rows}행 × {n_feat}피처, 종목 {len(set(codes))}개")

    conn = _pg_connect()
    cur = conn.cursor()
    uniq_codes = sorted(set(codes))
    cur.execute(
        f"""SELECT stock_code, trade_date, {', '.join(EVENT_COLS)}
            FROM event_features
            WHERE stock_code = ANY(%s) AND trade_date BETWEEN %s AND %s""",
        (uniq_codes, min(dates), max(dates)))
    lut = {}
    for row in cur.fetchall():
        lut[(str(row[0]), str(row[1]))] = [float(v or 0) for v in row[2:]]
    conn.close()
    print(f"[이벤트 패치] event_features 조회 {len(lut)}행 (패널 종목 범위)")

    add = np.full((n_rows, len(EVENT_COLS)), args.fill, dtype=np.float32)
    hit = 0
    for i in range(n_rows):
        v = lut.get((codes[i], dates[i]))
        if v is not None:
            add[i] = v
            hit += 1
    print(f"[이벤트 패치] 매칭 {hit}/{n_rows}행 ({hit / n_rows * 100:.1f}%)")

    # 죽은 컬럼 감시: 값이 전부 같은 컬럼은 모델에 아무 정보를 주지 않는다.
    # ⚠ 종목당 유니크는 numpy 마스크로 계산해야 한다 — `codes == c_` 는 list 비교라 항상 False 다
    #   (실측 2026-09-25: 그래서 전 컬럼이 "상수"로 잘못 표시됐다).
    codes_arr = np.array(codes)
    for j, c in enumerate(EVENT_COLS):
        col = add[:, j]
        nz = int((col != args.fill).sum())
        uniq = [len(np.unique(col[codes_arr == c_])) for c_ in set(codes)]
        per_stock_unique = float(np.median(uniq)) if uniq else 0.0
        flag = "  ⚠ 상수(정보 없음)" if per_stock_unique <= 1 else ""
        print(f"    {c:26s} non-zero {nz:6d}행 ({nz / n_rows * 100:5.1f}%) "
              f"종목당유니크중앙 {per_stock_unique:.0f}{flag}")

    X_out = np.hstack([X, add])
    names_out = names + EVENT_COLS
    np.savez_compressed(args.dst, X=X_out, feature_names=np.array(names_out, dtype=object),
                        dates=z["dates"], codes=z["codes"], price=z["price"])
    print(f"[이벤트 패치] 저장: {args.dst} ({X_out.shape[0]}행 × {X_out.shape[1]}피처)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

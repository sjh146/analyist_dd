#!/usr/bin/env python3
"""패널에 **공시 이벤트 피처 4종**을 as-of 로 추가한다 (기존 컬럼 유지·새 컬럼 append).

WHY (2026-10-05 엔지니어 실측)
- `disclosures` 원천이 크게 자랐다: **214,982행 · 3,056종목 · 427 rcept_dt · 2025-01-02~2026-10-02**
  (월별 5,672~27,072행 · 월 1,633~2,916종목). CG67(2026-10-02)이 '공시 기여 0'으로 닫을 때의 원천은
  **1,712행·49종목**(패널 42.2% 셀 비영)이었다 → 그 결론은 커버리지 1/120 규모 위의 결론이다.
- 공시 접수일(rcept_dt)은 **공시 시점 그 자체**라 as-of 가정(90/45일 지연)이 필요 없다 —
  `rcept_dt <= date` 만 지키면 무누수가 보장된다(재무 피처와 달리 지연 규칙 불필요).

추가 피처(모두 시간가변 후보, 가격·수급과 독립 정보 클래스):
  1. disc_ev_count_5d   — 최근 5일 공시 건수
  2. disc_ev_count_20d  — 최근 20일 공시 건수
  3. disc_ev_days_since — 최근 공시 이후 경과일(공시 없으면 -1)
  4. disc_ev_cap_20d    — 최근 20일 자본조달·지배구조성 공시 건수(유상/무상증자·CB·합병·분할·감자 등)

사용 (xgboost-ml 컨테이너, cwd=/app):
    docker exec stock_xgboost_ml python /app/scripts/patch_panel_disclosures_multi.py \\
        --in app/models/wf/panel_prod200.npz --out app/models/wf/panel_prod200_disc.npz
"""
from __future__ import annotations

import argparse
import bisect
import os

import numpy as np
import psycopg2

CAP_EVENT_KEYWORDS = ["유상증자", "무상증자", "전환사채", "신주인수권", "합병", "분할",
                      "감자", "자기주식", "소각", "제3자배정"]
NEW_FEATURES = ["disc_ev_count_5d", "disc_ev_count_20d", "disc_ev_days_since", "disc_ev_cap_20d"]


def _pg_connect():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def _shift(d: str, days: int) -> str:
    return str((np.datetime64(d) + np.timedelta64(days, "D")).astype("datetime64[D]"))


def main() -> int:
    ap = argparse.ArgumentParser(description="패널 공시 이벤트 피처 append( as-of )")
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", dest="dst", required=True)
    ap.add_argument("--show-names", action="store_true", help="report_nm 상위 15개 출력")
    args = ap.parse_args()

    z = np.load(args.src, allow_pickle=True)
    X = z["X"].astype(np.float64, copy=True)
    names = [str(n) for n in z["feature_names"]]
    dates = [str(d)[:10] for d in z["dates"]]
    codes = [str(c) for c in z["codes"]]

    dup = [f for f in NEW_FEATURES if f in names]
    if dup:
        print(f"STOP: 이미 있는 컬럼 {dup} — 패널이 이미 패치됨")
        return 1

    conn = _pg_connect()
    cur = conn.cursor()
    uniq = sorted(set(codes))
    ph = "(" + ",".join(["%s"] * len(uniq)) + ")"
    cur.execute(f"SELECT stock_code, rcept_dt, report_nm FROM disclosures "
                f"WHERE stock_code IN {ph} ORDER BY stock_code, rcept_dt", uniq)
    all_by: dict[str, list[str]] = {}
    cap_by: dict[str, list[str]] = {}
    name_counts: dict[str, int] = {}
    n_src = 0
    for code, dt, nm in cur.fetchall():
        n_src += 1
        code, d = str(code), str(dt)[:10]
        all_by.setdefault(code, []).append(d)
        nm = str(nm or "")
        name_counts[nm] = name_counts.get(nm, 0) + 1
        if any(k in nm for k in CAP_EVENT_KEYWORDS):
            cap_by.setdefault(code, []).append(d)
    conn.close()

    print(f"원천 행(패널 유니버스 한정): {n_src} · 종목 {len(all_by)} / 패널 종목 {len(uniq)}")
    if args.show_names:
        for nm, c in sorted(name_counts.items(), key=lambda kv: -kv[1])[:15]:
            print(f"   {c:>6}  {nm[:70]}")
    print(f"자본조달성 공시 종목: {len(cap_by)} · 행 {sum(len(v) for v in cap_by.values())}")

    n = len(codes)
    F = np.zeros((n, len(NEW_FEATURES)), dtype=np.float64)
    nz = np.zeros(len(NEW_FEATURES), dtype=np.int64)
    for i in range(n):
        seq = all_by.get(codes[i])
        d = dates[i]
        if seq:
            c5 = bisect.bisect_right(seq, d) - bisect.bisect_left(seq, _shift(d, -5))
            c20 = bisect.bisect_right(seq, d) - bisect.bisect_left(seq, _shift(d, -20))
            hi = bisect.bisect_right(seq, d)
            dsi = float((np.datetime64(d) - np.datetime64(seq[hi - 1])).astype(int)) if hi else -1.0
            F[i, 0], F[i, 1], F[i, 2] = c5, c20, dsi
        cseq = cap_by.get(codes[i])
        if cseq:
            F[i, 3] = bisect.bisect_right(cseq, d) - bisect.bisect_left(cseq, _shift(d, -20))
        for j in range(len(NEW_FEATURES)):
            if F[i, j] != 0:
                nz[j] += 1

    for j, f in enumerate(NEW_FEATURES):
        print(f"   {f:20s} 비영 {nz[j]}/{n} ({100.0 * nz[j] / n:.2f}%) "
              f"min {F[:, j].min():.0f} max {F[:, j].max():.0f}")

    Xn = np.hstack([X, F]).astype(np.float32)
    np.savez_compressed(args.dst, X=Xn, feature_names=np.array(names + NEW_FEATURES, dtype="<U40"),
                        dates=z["dates"], codes=z["codes"], price=z["price"])
    print(f"saved: {args.dst}  ({X.shape[0]}행 × {Xn.shape[1]}피처)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

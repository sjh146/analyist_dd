#!/usr/bin/env python3
"""_cg279_block_bootstrap.py — 세션별 Δ(top-k − 풀평균)의 **이동블록 부트스트랩** + 최근 구간 단독 검정.

WHY(2026-11-14 CG278 실측): 배포가능 arm(CO_smooth_d1_h5) h5 k=3 Δ = +1.703%p/세션 · t 3.50 이지만
세션 Δ 의 sd 가 7.3%p 이고 같은 종목이 연속 세션에 반복 선택된다(자기상관) → 단순 t 는 유효표본을
과대평가한다. 앞/뒤 절반도 +3.36 / −0.08 로 갈렸고 최근 2026-08·09 는 음(−0.39 / −1.07)이었다.
따라서 ① 이동블록(기본 10세션) 부트스트랩 5,000회로 평균의 95% CI 를 내고 ② `--since` 이후 구간만의
Δ 를 따로 재서 '시간 안정'인지 '국면 의존'인지 판정한다. 새 학습 0 · 읽기 전용.

사용(컨테이너):
  python scripts/_cg279_block_bootstrap.py --jsonl /app/reports/overnight/cg278_preds_allcodes.jsonl \
    --tag CO_smooth_d1_h5 --k 3 --horizon 5 --block 10 --reps 5000 --since 2026-04-01 \
    --json-out /app/reports/overnight/cg279_block_bootstrap.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fillable_topk_expectancy as fte   # noqa: E402  (계산 단일 진실원 재사용)


def block_bootstrap_ci(x: np.ndarray, block: int, reps: int, alpha: float = 0.05, seed: int = 7):
    """원형(circular) 이동블록 부트스트랩 — 자기상관을 보존한 평균 분포."""
    n = len(x)
    if n == 0:
        return None, None
    rng = np.random.default_rng(seed)
    n_blocks = int(np.ceil(n / max(1, block)))
    starts = rng.integers(0, n, size=(reps, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]) % n
    idx = idx.reshape(reps, -1)[:, :n]
    means = x[idx].mean(axis=1)
    lo, hi = np.quantile(means, [alpha / 2, 1 - alpha / 2])
    return float(lo), float(hi)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--block", type=int, default=10)
    ap.add_argument("--reps", type=int, default=5000)
    ap.add_argument("--since", default=None, help="YYYY-MM-DD — 이 날짜 이후 세션만 따로 검정")
    ap.add_argument("--json-out", required=True)
    a = ap.parse_args(argv)

    rows, bad = fte.load_rows(a.jsonl, a.tag)
    if not rows:
        print("[FAIL] rows 0", file=sys.stderr)
        return 2
    codes = sorted({r["code"] for r in rows})
    dmin = min(r["date"] for r in rows)
    dmax = max(r["date"] for r in rows)
    import pandas as pd
    d0 = (pd.Timestamp(dmin) - pd.Timedelta(days=6)).date()
    d1 = (pd.Timestamp(dmax) + pd.Timedelta(days=6 * (a.horizon + 2))).date()
    conn = fte.db_connect()
    series = fte.price_series(conn, codes, d0, d1)
    er, _ = fte.enrich(rows, series, a.horizon, "close_h")
    ns = argparse.Namespace(exclude_limit_up=True, max_day_chg=25.0, min_value=1e9,
                            min_price=None, max_price=None)
    fa, _ = fte.apply_filters(er, ns, True)
    rt = (fte.FEE_BUY + fte.FEE_SELL + fte.TAX_SELL) * 100.0
    ba = fte.baskets(fa, [a.k], rt)[a.k]
    pool = fte.pool_series(fa, rt)
    keys = sorted((kk for kk in ba if kk in pool), key=lambda kk: (kk[1], kk[0]))
    delta = np.asarray([ba[kk] - pool[kk] for kk in keys], dtype=float)
    dates = [kk[1] for kk in keys]

    def _stat(x):
        if len(x) < 2:
            return {"n": int(len(x))}
        sd = float(x.std(ddof=1))
        return {"n": int(len(x)), "mean": float(x.mean()), "sd": sd,
                "t": float(x.mean() / (sd / np.sqrt(len(x)))) if sd > 0 else None,
                "pos_pct": float((x > 0).mean() * 100),
                "median": float(np.median(x))}

    out = {"tag": a.tag, "k": a.k, "horizon": a.horizon, "fee_roundtrip_pct": rt,
           "block": a.block, "reps": a.reps, "n_sessions": int(len(delta)),
           "all": _stat(delta), "since": a.since, "recent": None, "null_note": "널 = 세션 풀평균(무작위 k 기대)"}
    lo, hi = block_bootstrap_ci(delta, a.block, a.reps)
    out["all"]["boot_ci95"] = [lo, hi]
    out["all"]["ci_excludes_zero"] = bool(lo is not None and (lo > 0 or hi < 0))
    if a.since:
        mask = np.asarray([dt >= str(a.since) for dt in dates])
        rec = delta[mask]
        out["recent"] = _stat(rec)
        if len(rec) > 1:
            lo2, hi2 = block_bootstrap_ci(rec, a.block, a.reps, seed=11)
            out["recent"]["boot_ci95"] = [lo2, hi2]
            out["recent"]["ci_excludes_zero"] = bool(lo2 is not None and (lo2 > 0 or hi2 < 0))
        out["recent"]["from"] = str(a.since)

    # 판정(사전등록, CG279 success): CI 하한 > 0 & 최근 구간 Δ > 0 → 시간 안정 / 아니면 국면 의존
    ci_ok = bool(out["all"].get("ci_excludes_zero") and (out["all"]["boot_ci95"][0] or 0) > 0)
    rec_ok = bool(out["recent"] and (out["recent"].get("mean") or 0) > 0)
    out["verdict"] = ("시간 안정 — 배포 후보 검토(승격 아님)" if (ci_ok and rec_ok)
                      else "국면 의존 — 돈 축 종결 후보")
    out["criteria"] = {"ci_lower_gt_zero": ci_ok, "recent_mean_gt_zero": rec_ok}

    os.makedirs(os.path.dirname(a.json_out) or ".", exist_ok=True)
    with open(a.json_out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"[ok] {a.json_out}")
    print(f"  전체: n={out['all']['n']} Δ {out['all'].get('mean'):+.3f}%p t {out['all'].get('t')} "
          f"CI95 [{lo:+.3f},{hi:+.3f}] 양세션 {out['all'].get('pos_pct'):.1f}%")
    if out["recent"]:
        r = out["recent"]
        print(f"  최근({a.since}~): n={r['n']} Δ {r.get('mean'):+.3f}%p t {r.get('t')} "
              f"CI95 [{r['boot_ci95'][0]:+.3f},{r['boot_ci95'][1]:+.3f}] 양세션 {r.get('pos_pct'):.1f}%")
    print(f"  판정: {out['verdict']} {out['criteria']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

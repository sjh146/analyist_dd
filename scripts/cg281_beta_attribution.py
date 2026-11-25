#!/usr/bin/env python3
"""CG281 — top-k 초과기대의 베타 귀속: 세션별 Δ(top-k − 풀평균)를 풀 수익률에 회귀해
'선택 알파(intercept)'와 '고베타 노출(slope)'을 가른다.

WHY(2026-11-15 CG280 실측): 49종목 청정 패널에서 CO_smooth_d1_h5 의 h5 k3 Δ 는 +0.625%p/세션
(t 1.63) 이었지만 **분할표본이 unstable** 였다 — 앞 절반 +2.752 / 뒤 절반 −1.692. 같은 구간의
풀평균도 +1.98 / −2.17 로 부호가 바뀐다. 즉 Δ 의 부호가 '시장 국면'과 함께 움직인다는 뜻이고,
그렇다면 초과는 '선택 능력'이 아니라 **상승장에서 고베타 종목을 고르는 노출**일 수 있다.
판정: Δ = a + b·pool 로 회귀해
  - a ≥ +0.1%p/세션 & t(a) ≥ 2  → 선택 알파 존재(다음 실험은 이 축을 계속)
  - a < +0.1 또는 t < 2          → 초과는 베타 조건부 = 선택 알파 미검출(돈 축 종결 근거)
부가로 pool>0 / pool≤0 국면별 Δ 평균·t 를 함께 싣는다(단조 가설 검정).

읽기 전용. 재학습 없음(이미 덤프된 예측 + DB 가격만 사용). 프로토콜은
fillable_topk_expectancy 와 **같은 함수**를 재사용한다(필터·수수료·체결성 동일).
"""
import argparse
import json
import math
import random
import sys
from collections import defaultdict

import fillable_topk_expectancy as F


def boot_intercept(xs, ys, block, n_boot=4000, seed=20261123):
    """이동블록 부트스트랩으로 회귀 intercept 의 SE·t 를 다시 잡는다.

    WHY(2026-11-25 CG488): h5 라벨은 세션마다 5일 보유창이 겹쳐 iid 가정 t 가 sqrt(h) 배 부풀려진다
    (CG486 실측: stride1 Δ t 3.21 → 비중첩 t 0.67). 점추정은 그대로 두고 **검정만** 교체하려면
    블록 길이 h 의 이동블록 부트스트랩이 필요하다(F.overlap_robust 와 같은 원리, 여기선 OLS 절편).
    """
    n = len(xs)
    if n < 8:
        return None
    L = max(2, min(int(block or 1), n))
    point = (ols(xs, ys) or {}).get("intercept")
    if point is None:
        return None
    rnd = random.Random(seed)
    est = []
    for _ in range(n_boot):
        bx, by = [], []
        while len(bx) < n:
            s = rnd.randrange(n)
            for j in range(L):
                i = (s + j) % n
                bx.append(xs[i])
                by.append(ys[i])
        r = ols(bx, by)
        if r:
            est.append(r["intercept"])
    if len(est) < 100:
        return None
    mu = sum(est) / len(est)
    se = math.sqrt(sum((e - mu) ** 2 for e in est) / (len(est) - 1))
    srt = sorted(est)
    return {"block": L, "n_boot": len(est), "se": round(se, 4),
            "t": round(point / se, 2) if se > 0 else None,
            "ci95": [round(srt[int(0.025 * len(srt))], 4), round(srt[int(0.975 * len(srt))], 4)]}


def ols(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx == 0:
        return None
    b = sxy / sxx
    a = my - b * mx
    resid = [y - (a + b * x) for x, y in zip(xs, ys)]
    sse = sum(r * r for r in resid)
    sst = sum((y - my) ** 2 for y in ys)
    se_b = math.sqrt(sse / (n - 2) / sxx) if n > 2 and sse > 0 else float("nan")
    se_a = math.sqrt(sse / (n - 2) * (1.0 / n + mx * mx / sxx)) if n > 2 and sse > 0 else float("nan")
    return {
        "n": n, "intercept": round(a, 4), "slope": round(b, 4),
        "t_intercept": round(a / se_a, 2) if se_a and se_a == se_a and se_a > 0 else None,
        "t_slope": round(b / se_b, 2) if se_b and se_b == se_b and se_b > 0 else None,
        "r2": round(1 - sse / sst, 4) if sst > 0 else None,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="CG281 베타 귀속 검정")
    ap.add_argument("--arm-jsonl", required=True)
    ap.add_argument("--arm-tag", required=True)
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--regime-window", type=int, default=20,
                    help="트레일링 국면 창(세션 수) — t 이전 w 세션 풀평균의 부호로 국면 판정(진입 시점 정보만)")
    ap.add_argument("--stride", type=int, default=1,
                    help="세션 채점 간격 — h5 처럼 보유창이 겹치는 라벨은 --stride 5 로 비중첩 채점")
    ap.add_argument("--block-boot", type=int, default=0,
                    help="이동블록 부트스트랩 길이(0=자동: horizon). 겹침 반영한 intercept t 를 함께 낸다")
    ap.add_argument("--json-out", required=True)
    a = ap.parse_args(argv)

    filt = argparse.Namespace(exclude_limit_up=True, max_day_chg=25.0, min_value=1e9,
                              min_price=None, max_price=None)
    rt_pct = (F.FEE_BUY + F.FEE_SELL + F.TAX_SELL) * 100.0

    rows, bad = F.load_rows(a.arm_jsonl, a.arm_tag)
    if not rows:
        print(f"[FAIL] 행 없음 tag={a.arm_tag}", file=sys.stderr)
        return 2
    codes = sorted({r["code"] for r in rows})
    dmin = min(r["date"] for r in rows)
    dmax = max(r["date"] for r in rows)
    d0 = (F.pd.Timestamp(dmin) - F.pd.Timedelta(days=6)).date()
    d1 = (F.pd.Timestamp(dmax) + F.pd.Timedelta(days=6 * (a.horizon + 2))).date()
    conn = F.db_connect()
    series = F.price_series(conn, codes, d0, d1)
    erows, drop = F.enrich(rows, series, a.horizon, "close_h")
    krows, filt_out = F.apply_filters(erows, filt, True)
    keep = F.stride_keys(krows, a.stride)
    if keep is not None:
        krows = [r for r in krows if (r["fold"], r["date"]) in keep]

    top = F.baskets(krows, [a.k], rt_pct)[a.k]
    pool = F.pool_series(krows, rt_pct)
    keys = sorted(set(top) & set(pool))
    delta = {k: top[k] - pool[k] for k in keys}

    xs = [pool[k] for k in keys]
    ys = [delta[k] for k in keys]
    reg = ols(xs, ys)
    blk = boot_intercept(xs, ys, a.block_boot or a.horizon)

    overlap = bool(a.stride < a.horizon)
    if overlap:
        _keep_ns = F.stride_keys(krows, a.horizon) or set()
        keys_ns = [k for k in keys if k in _keep_ns]
    else:
        keys_ns = list(keys)
    xs_ns = [pool[k] for k in keys_ns]
    ys_ns = [delta[k] for k in keys_ns]
    reg_ns = ols(xs_ns, ys_ns)
    blk_ns = boot_intercept(xs_ns, ys_ns, max(a.block_boot or 0, a.horizon))
    up = [delta[k] for k in keys if pool[k] > 0]
    dn = [delta[k] for k in keys if pool[k] <= 0]

    # 트레일링 국면(진입 시점 정보만): t 이전 w 세션의 풀평균 부호.
    # 같은 세션 수익률로 국면을 나누면 그건 라벨을 보고 고르는 것(비거래)이므로, 실전 판단에
    # 쓰려면 반드시 '과거 창'으로 나눠야 한다. 날짜가 여러 폴드에 중복되면 평균을 쓴다.
    by_date = defaultdict(list)
    for k in keys:
        by_date[k[1]].append(k)
    dates = sorted(by_date)
    d_pool = {}
    for d in dates:
        d_pool[d] = sum(pool[k] for k in by_date[d]) / len(by_date[d])
    trail = {}
    for i, d in enumerate(dates):
        prev = [d_pool[x] for x in dates[max(0, i - a.regime_window):i]]
        if len(prev) < max(3, a.regime_window // 2):
            continue
        trail[d] = sum(prev) / len(prev)
    t_up = [delta[k] for k in keys if k[1] in trail and trail[k[1]] > 0]
    t_dn = [delta[k] for k in keys if k[1] in trail and trail[k[1]] <= 0]

    out = {
        "metric_name": "beta_attribution",
        "arm_tag": a.arm_tag, "k": a.k, "horizon": a.horizon,
        "exit": "close_h", "fee_roundtrip_pct": round(rt_pct, 4),
        "rows": {"used": len(krows), "dropped_price": drop, "filtered_out": filt_out,
                 "bad_lines": bad},
        "n_sessions": len(keys),
        "stride": a.stride,
        "pool": F.stat(xs),
        "delta": F.stat(ys),
        "halves": F.halves(delta),
        "regression_delta_on_pool": reg,
        "regression_block_boot": blk,
        "by_regime": {"pool_positive": F.stat(up), "pool_nonpositive": F.stat(dn)},
        "by_trailing_regime": {
            "window": a.regime_window,
            "trail_positive": F.stat(t_up), "trail_nonpositive": F.stat(t_dn),
            "note": "트레일링 국면은 진입 시점에 알 수 있는 정보만 사용(거래 가능 후보)",
        },
        "threshold": ("intercept ≥ +0.1%p/세션 & t ≥ 2(블록t>iid t 중 보수적) & 분할 both_positive "
                      "→ 선택 알파 / 미달 → 베타 조건부 초과"),
        "overlap_corrected": overlap,
        "regression_nonoverlap": {
            "stride": a.horizon, "n_sessions": len(keys_ns),
            "regression": reg_ns, "block_boot": blk_ns,
            "halves": F.halves({k: delta[k] for k in keys_ns}),
        },
    }
    t_use = (blk or {}).get("t") if blk and blk.get("t") is not None else (reg or {}).get("t_intercept")
    out["t_used"] = t_use
    ok = bool(reg) and reg.get("intercept") is not None and reg["intercept"] >= 0.1 \
        and (t_use or 0) >= 2 and (out["halves"] or {}).get("stable") == "both_positive"
    if overlap:
        t_ns = (blk_ns or {}).get("t") if blk_ns and blk_ns.get("t") is not None else (reg_ns or {}).get("t_intercept")
        out["t_used"] = t_ns
        ok = bool(reg_ns) and reg_ns.get("intercept") is not None and reg_ns["intercept"] >= 0.1 \
            and (t_ns or 0) >= 2 \
            and (out["regression_nonoverlap"]["halves"] or {}).get("stable") == "both_positive"
        out["verdict"] = ("선택 알파 있음(비중첩 판정)" if ok
                          else "베타 조건부 초과 — 선택 알파 미검출(중첩 표본 → 비중첩 재판정)")
    else:
        out["verdict"] = "선택 알파 있음" if ok else "베타 조건부 초과 — 선택 알파 미검출"

    with open(a.json_out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print(f"[CG281] {a.arm_tag} h{a.horizon} k{a.k} · 세션 {len(keys)} · "
          f"Δ 평균 {out['delta'].get('mean')}%p (t {out['delta'].get('t')})")
    if reg:
        print(f"  회귀 Δ = {reg['intercept']:+.4f} + {reg['slope']:+.4f}·pool "
              f"(t_a {reg['t_intercept']} · t_b {reg['t_slope']} · R² {reg['r2']})")
    if blk:
        print(f"  블록t(겹침보정, L={blk['block']}) intercept t {blk['t']} · CI95 {blk['ci95']} "
              f"→ 판정에 쓴 t {out['t_used']}")
    print(f"  국면별 Δ: 상승장 {out['by_regime']['pool_positive'].get('mean')}%p "
          f"(t {out['by_regime']['pool_positive'].get('t')}, n {out['by_regime']['pool_positive'].get('n')}) · "
          f"하락장 {out['by_regime']['pool_nonpositive'].get('mean')}%p "
          f"(t {out['by_regime']['pool_nonpositive'].get('t')}, n {out['by_regime']['pool_nonpositive'].get('n')})")
    if overlap:
        rn = out["regression_nonoverlap"]
        print(f"  ⚠ 중첩 표본(stride {a.stride} < h{a.horizon}) → 비중첩 재판정(세션 {rn['n_sessions']}) "
              f"intercept {rn['regression']['intercept']:+.4f} (t {out['t_used']}) "
              f"CI95 {(rn['block_boot'] or {}).get('ci95')}")
    h = out["halves"]
    if h:
        print(f"  분할 {h['stable']} [{h['front']['mean']:+.3f}/{h['back']['mean']:+.3f}]")
    print(f"  판정: {out['verdict']} → {a.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

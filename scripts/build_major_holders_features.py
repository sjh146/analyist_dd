#!/usr/bin/env python3
"""DART 대량보유(major_holders) 피처 빌더 — CG465 데이터 축 (2026-11-24).

WHY
    미배선 원천 스윕(source_coverage_sweep)이 2026-11-24 틱에 major_holders 를 신규 후보로 올렸다
    (게이트: 종목커버 >=0.75 · 거래일 >=250). 원천은 DART 5% 대량보유 보고로 rcept_dt(접수일) 기준
    시점가변 정보이며 현 어느 패널에도 배선돼 있지 않다. 다른 미사용 원천은 '커버리지 부족'
    (minute_bars 봉30 · 뉴스 0.185) 또는 'edge 0'(event·수급)으로 종결된 반면 이 원천은 미측정이다.

as-of 규칙 (하드)
    값은 (stock_code, d) 에서 rcept_dt <= d 인 보고만 쓴다. 소비 측(patch_panel_ss.py)이 한 번 더
    strict < 지연을 걸므로 패널 날짜 D 에는 rcept_dt < D 인 값만 들어간다 — 어떤 해석에서도 미래정보가 아니다.

출력: data/reports/mh_features.npz
    codes(U12) · dates(U10 = rcept_dt 격자) · F(n_codes, n_dates, k) · names · meta
    names 기본 = [mh_stake_last, mh_stake_max1y, mh_n_rep_180, mh_rep_cnt_90, mh_days_since, mh_stake_chg]
    결측 98% 초과 열은 자동 제외하고 meta.dropped_sparse 에 남긴다(mh_stake_chg 는 실측 100% 결측).

자기검증: 커버(패널종목∩원천) · 각 열 비상수 · as-of 위반 0 · 최소 규모.
"""
from __future__ import annotations

import bisect
import datetime as dt
import json
import os
import subprocess
import sys

import numpy as np

DB = ["docker", "exec", "stock_postgres", "psql", "-U", "stock_user", "-d", "stock_trading",
      "-t", "-A", "-F", "|", "-c"]
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.abspath(os.path.join(HERE, "..", "data", "reports", "mh_features.npz"))
PANEL_CODES = "/tmp/v6ocodes.json"
START = "2024-11-01"
NAMES = ["mh_stake_last", "mh_stake_max1y", "mh_n_rep_180", "mh_rep_cnt_90",
         "mh_days_since", "mh_stake_chg"]


def psql(sql: str, timeout: int = 300) -> list[list[str]]:
    out = subprocess.run(DB + [sql], capture_output=True, text=True, timeout=timeout)
    if out.returncode != 0:
        raise RuntimeError("psql rc=%s: %s" % (out.returncode, out.stderr.strip()[:300]))
    return [l.split("|") for l in out.stdout.splitlines() if l.strip()]


def to_float(x: str):
    try:
        return float(x)
    except ValueError:
        return None


def main() -> int:
    global NAMES
    codes = sorted(set(json.load(open(PANEL_CODES))))
    rows = psql(
        "SELECT stock_code, rcept_dt::text, coalesce(stake_pct,0)::text, reporter, report_tp "
        "FROM major_holders WHERE rcept_dt >= DATE '%s' ORDER BY stock_code, rcept_dt;" % START)
    if not rows:
        print("원천 0행 — 중단", file=sys.stderr)
        return 2

    hist: dict[str, list[tuple[dt.date, float, str]]] = {}
    all_dates: set[dt.date] = set()
    n_bad = 0
    for code, dstr, sp, rep, _tp in rows:
        d = dt.date.fromisoformat(dstr)
        spf = to_float(sp)
        if spf is None:
            n_bad += 1
            continue
        hist.setdefault(code, []).append((d, spf, rep or ""))
        all_dates.add(d)

    codes = [c for c in codes if c in hist]
    if len(codes) < 100:
        print("커버 부족 %d/200 — 중단" % len(codes), file=sys.stderr)
        return 3
    dates = sorted(all_dates)
    dstrs = [d.isoformat() for d in dates]
    nC, nD = len(codes), len(dates)
    F = np.full((nC, nD, len(NAMES)), np.nan, dtype=np.float64)

    for i, code in enumerate(codes):
        recs = hist[code]
        rdates = [r[0] for r in recs]
        for j, d in enumerate(dates):
            k = bisect.bisect_right(rdates, d)
            if k == 0:
                continue
            last_d, last_s, last_rep = recs[k - 1]
            F[i, j, 4] = (d - last_d).days
            lo365 = bisect.bisect_left(rdates, d - dt.timedelta(days=365))
            win = recs[lo365:k]
            F[i, j, 1] = max(w[1] for w in win) if win else np.nan
            lo180 = bisect.bisect_left(rdates, d - dt.timedelta(days=180))
            F[i, j, 2] = len({w[2] for w in recs[lo180:k]})
            lo90 = bisect.bisect_left(rdates, d - dt.timedelta(days=90))
            F[i, j, 3] = float(k - lo90)
            F[i, j, 0] = last_s
            prev = [w for w in recs[:k - 1] if w[2] == last_rep]
            F[i, j, 5] = (last_s - prev[-1][1]) if prev else np.nan

    snap = {
        "ts": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "major_holders(DART 대량보유)",
        "panel_codes": 200, "covered_codes": nC, "cover_frac": round(nC / 200.0, 4),
        "n_dates": nD, "dates": [dstrs[0], dstrs[-1]], "rows_src": len(rows), "rows_bad_stake": n_bad,
        "asof_rule": "값은 rcept_dt <= d, 소비측(patch)은 strict < 재적용",
    }

    keep = []
    dropped = {}
    for k, f in enumerate(NAMES):
        col = F[:, :, k]
        mf = float(np.mean(np.isnan(col)))
        if mf > 0.98:
            dropped[f] = round(mf, 4)
            continue
        assert float(np.nanstd(col)) > 0, "%s 상수" % f
        keep.append(k)
    if dropped:
        print("제외(결측>98%):", json.dumps(dropped, ensure_ascii=False))
        F = F[:, :, keep]
        NAMES = [NAMES[k] for k in keep]
    assert NAMES, "남은 열 없음 — 원천이 패널을 덮지 못한다"
    assert nC >= 100 and nD >= 100, (nC, nD)

    for i, code in enumerate(codes[:50]):
        first = min(r[0] for r in hist[code])
        j0 = bisect.bisect_left(dates, first)
        assert np.all(np.isnan(F[i, :j0, :])), "as-of 위반 %s" % code

    snap["names"] = NAMES
    snap["dropped_sparse"] = dropped
    snap["missing_frac"] = {NAMES[k]: round(float(np.mean(np.isnan(F[:, :, k]))), 4) for k in range(len(NAMES))}
    snap["std"] = {NAMES[k]: round(float(np.nanstd(F[:, :, k])), 4) for k in range(len(NAMES))}
    np.savez_compressed(OUT, codes=np.array(codes), dates=np.array(dstrs), F=F.astype(np.float32),
                        names=np.array(NAMES), meta=np.array(json.dumps(snap, ensure_ascii=False)))
    print(json.dumps(snap, ensure_ascii=False, indent=1))
    print("저장:", OUT)
    print("자체검증 PASS (커버·비상수·as-of)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

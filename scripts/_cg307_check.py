#!/usr/bin/env python3
"""_cg307_check.py — CG307 돈 지표 정합성 진단(임시).

목적: fillable_topk_expectancy 의 gross_pct(가격테이블 5일 수익)와 dump jsonl 의
      fwd_ret(라벨 선행수익) 불일치(corr·스케일)와, 급락(액면분할 흔적) 행이
      top-k 기대값을 오염시키는지 확인한다.
"""
import sys

sys.path.insert(0, "/app/scripts")
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import fillable_topk_expectancy as F  # noqa: E402


class A:
    exclude_limit_up = True
    max_day_chg = 25.0
    min_value = 1e9
    min_price = None
    max_price = None


def tstat(x):
    x = np.asarray(x, dtype=float)
    if len(x) < 3 or x.std(ddof=1) == 0:
        return float("nan")
    return float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x))))


def topk_series(rows, k=3, key="gross_pct"):
    from collections import defaultdict
    by = defaultdict(list)
    for r in rows:
        by[r["date"]].append(r)
    out = []
    for dt, rs in by.items():
        rs.sort(key=lambda r: -r["score"])
        t3 = rs[:k]
        out.append(sum(r[key] for r in t3) / k)
    return np.array(out)


def main():
    arm, _ = F.load_rows("/app/reports/overnight/cg291_preds.jsonl", "CO_q05_h5")
    codes = sorted({r["code"] for r in arm})
    dmin = min(r["date"] for r in arm)
    dmax = max(r["date"] for r in arm)
    d0 = (pd.Timestamp(dmin) - pd.Timedelta(days=6)).date()
    d1 = (pd.Timestamp(dmax) + pd.Timedelta(days=6 * 7)).date()
    conn = F.db_connect()
    series = F.price_series(conn, codes, d0, d1)
    enr, drop = F.enrich(arm, series, 5, "close_h")
    kept, fo = F.apply_filters(enr, A(), True)
    print("rows %d enriched %d fillable %d filtered_out %d" % (len(arm), len(enr), len(kept), fo))

    both = [r for r in kept if r.get("fwd_ret") is not None]
    g = np.array([r["gross_pct"] for r in both])
    f = np.array([r["fwd_ret"] for r in both])
    d = g - f
    print("corr(gross,fwd)=%.4f  gross mean %+.3f sd %.3f [%+.1f, %+.1f]  fwd mean %+.3f sd %.3f [%+.2f, %+.2f]"
          % (np.corrcoef(g, f)[0, 1], g.mean(), g.std(), g.min(), g.max(), f.mean(), f.std(), f.min(), f.max()))
    print("diff mean %+.3f | |>1: %.1f%%  |>5: %.1f%%  |>20: %.1f%%"
          % (d.mean(), 100 * (np.abs(d) > 1).mean(), 100 * (np.abs(d) > 5).mean(), 100 * (np.abs(d) > 20).mean()))

    def _dc(r):
        v = r.get("day_chg_pct")
        try:
            return float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            return 0.0
    dc = np.array([_dc(r) for r in both], dtype=float)
    print("entry-day chg: |chg|>=20%% rows %d (%.2f%%)  <=-20%% rows %d  >=+20%% rows %d"
          % (int((np.abs(dc) >= 20).sum()), 100 * (np.abs(dc) >= 20).mean(),
             int((dc <= -20).sum()), int((dc >= 20).sum())))

    base = topk_series(kept, 3, "gross_pct")
    print("top3 gross  mean %+.3f median %+.3f t %.2f n %d" % (base.mean(), np.median(base), tstat(base), len(base)))
    fbase = topk_series(kept, 3, "fwd_ret")
    print("top3 fwdret mean %+.3f median %+.3f t %.2f" % (fbase.mean(), np.median(fbase), tstat(fbase)))

    for th in (10, 15, 20, 25):
        f_k = [r for r in kept if r.get("day_chg_pct") is None or abs(_dc(r)) < th]
        s = topk_series(f_k, 3, "gross_pct")
        print("sym-filter |entry chg|<%2d%%: rows %5d  top3 gross mean %+.3f median %+.3f t %.2f n %d"
              % (th, len(f_k), s.mean(), np.median(s), tstat(s), len(s)))

    # 분할 흔적이 top3 에 얼마나 들어오는가
    from collections import defaultdict
    by = defaultdict(list)
    for r in kept:
        by[r["date"]].append(r)
    big = 0
    tot = 0
    for rs in by.values():
        rs.sort(key=lambda r: -r["score"])
        for r in rs[:3]:
            tot += 1
            if abs(_dc(r)) >= 20:
                big += 1
    print("top3 picks with |entry chg|>=20%%: %d/%d (%.1f%%)" % (big, tot, 100.0 * big / max(tot, 1)))


if __name__ == "__main__":
    main()

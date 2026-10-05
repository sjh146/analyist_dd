#!/usr/bin/env python3
"""인트라데이(분봉) 피처 **사전 스크린** — 데이터 축(CG101)의 첫 측정 도구.

WHY (2026-10-06, CG128)
- 2026-10-04 이후 실측으로 모델측 레버(변환·HP·앙상블·선별·가중·목적함수·라벨·유니버스·창·
  정규화·국면)는 전부 닫혔고, 남은 축은 데이터뿐이다. 데이터 축 후보 중 커버리지가
  '미확보'인 것은 인트라데이(분봉) 하나다 — news_events 는 패널 200종목 중 4종목,
  sns_post_features 22종목, stock_sentiment 0행(2026-10-06 실측)으로 확장 불가다.
- 분봉은 collector 결함(XR26) 때문에 지금 **장 마감 30분(15:01~15:30)** 구간만 매일 쌓인다.
  60분봉 전체가 아니어도 이 구간은 '종가 동시호가 전 매매압력'이라는 별개 정보를 담는다 →
  수집기 수정 승인 전에 **현재 쌓인 표본으로 먼저 채점**해 축의 기대값을 판정한다.

정의 (읽기 전용)
- 피처(종목·일별, 30봉에서 유도):
    l30_ret       = close(마지막봉)/open(첫봉) - 1
    l30_range     = (max high - min low)/close(마지막봉)
    l30_vwap_dev  = close(마지막봉)/VWAP(30봉) - 1     (VWAP = Σtrading_value/Σvolume)
    l30_slope     = OLS(close ~ i)/평균 close          (마감 방향 기울기)
    l30_vol_share = Σvolume(30봉)/일별 volume          (일 거래량 대비 마감 비중)
    l30_tv_share  = Σtrading_value(30봉)/일별 trading_value
    l30_up_ratio  = close > 직전 close 인 봉의 비율
    l30_realvol   = 1분 로그수익률 표준편차
- 대조군(같은 행): 일봉에서 유도한 return_1d(전일 종가 대비) · gap(시가/전일종가-1) ·
  day_range((고-저)/종가) · null(해시 기반 결정적 난수 = 잡음 바닥).
- 라벨: r_h = close(t+h)/close(t) - 1 > 0  (h=1, h=5). t = 분봉이 있는 거래일.
- 판정: **날짜별 횡단면 AUC 평균**(pooled 아님) + 날짜별 Spearman IC 평균·t.

사용(컨테이너):
  docker exec -e PYTHONPATH=/app stock_xgboost_ml python /app/scripts/intraday_feature_screen.py \
      --json-out /app/reports/overnight/cg128_intraday_screen.json
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
from collections import defaultdict

import psycopg2


def _pg_connect():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def _ranks(vals):
    """평균동점 순위(1-based)."""
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + 1 + j + 1) / 2.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _auc(scores, ys):
    """rank 기반 AUC(동점 평균순위). 한 클래스뿐이면 None."""
    pos = [i for i, y in enumerate(ys) if y == 1]
    neg = [i for i, y in enumerate(ys) if y == 0]
    if not pos or not neg:
        return None
    r = _ranks([float(s) for s in scores])
    s_pos = sum(r[i] for i in pos)
    n1, n0 = len(pos), len(neg)
    return (s_pos - n1 * (n1 + 1) / 2.0) / (n1 * n0)


def _spearman(xs, ys):
    if len(xs) < 3:
        return None
    rx, ry = _ranks([float(v) for v in xs]), _ranks([float(v) for v in ys])
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def _null_feature(code, date):
    """결정적 난수(잡음 바닥): 문자열 해시를 [0,1) 로."""
    import hashlib
    h = hashlib.md5(f"{code}|{date}".encode()).hexdigest()
    return int(h[:8], 16) / float(0xFFFFFFFF)


def _slope(vals):
    """OLS 기울기(정규화 전)."""
    n = len(vals)
    if n < 3:
        return None
    mx = (n - 1) / 2.0
    my = sum(vals) / n
    den = sum((i - mx) ** 2 for i in range(n))
    if den == 0:
        return None
    num = sum((i - mx) * (v - my) for i, v in enumerate(vals))
    return num / den


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-stocks", type=int, default=100, help="완전일 최소 종목수")
    ap.add_argument("--min-bars", type=int, default=20, help="완전일 최소 봉수")
    ap.add_argument("--horizons", default="1,5")
    ap.add_argument("--json-out", default="")
    args = ap.parse_args()
    horizons = [int(x) for x in args.horizons.split(",") if x.strip()]

    conn = _pg_connect()
    cur = conn.cursor()

    cur.execute("SELECT trade_date, count(distinct stock_code), count(*) FROM minute_bars "
                "GROUP BY 1 ORDER BY 1")
    day_stat = [(str(d)[:10], int(n), int(c)) for d, n, c in cur.fetchall()]
    full_days = [d for d, n, c in day_stat if n >= args.min_stocks and c >= args.min_bars * n]
    print("minute_bars 일자별:", {d: (n, c) for d, n, c in day_stat})
    print("완전일(스크린 대상):", full_days)

    cur.execute("SELECT trade_date, stock_code, \"time\", open_price, high_price, low_price, "
                "close_price, volume, trading_value FROM minute_bars WHERE trade_date = ANY(%s::date[]) "
                "ORDER BY stock_code, trade_date, \"time\"", (full_days,))
    bars = defaultdict(list)
    for dt, code, tm, o, h, l, c, v, tv in cur.fetchall():
        bars[(str(code), str(dt)[:10])].append(
            (str(tm), float(o or 0), float(h or 0), float(l or 0), float(c or 0),
             float(v or 0), float(tv or 0)))

    cur.execute("SELECT stock_code, trade_date, open_price, high_price, low_price, close_price, "
                "volume, trading_value FROM market_data WHERE close_price IS NOT NULL "
                "ORDER BY stock_code, trade_date")
    daily = defaultdict(dict)
    seq = defaultdict(list)
    for code, dt, o, h, l, c, v, tv in cur.fetchall():
        d = str(dt)[:10]
        code = str(code)
        daily[code][d] = (float(o or 0), float(h or 0), float(l or 0), float(c), float(v or 0),
                          float(tv or 0))
        seq[code].append(d)
    conn.close()

    # 피처 행 생성
    rows = []  # (date, code, feat_dict)
    for (code, d), bl in bars.items():
        if len(bl) < args.min_bars:
            continue
        bl.sort(key=lambda x: x[0])
        closes = [b[4] for b in bl]
        highs = [b[2] for b in bl]
        lows = [b[3] for b in bl]
        vols = [b[5] for b in bl]
        tvs = [b[6] for b in bl]
        if closes[0] <= 0 or closes[-1] <= 0:
            continue
        sv, st = sum(vols), sum(tvs)
        vwap = (st / sv) if sv > 0 else closes[-1]
        f = {
            "l30_ret": closes[-1] / closes[0] - 1.0,
            "l30_range": (max(highs) - min(lows)) / closes[-1],
            "l30_vwap_dev": closes[-1] / vwap - 1.0 if vwap > 0 else 0.0,
            "l30_up_ratio": (sum(1 for i in range(1, len(closes)) if closes[i] > closes[i - 1])
                             / max(1, len(closes) - 1)),
        }
        sl = _slope(closes)
        f["l30_slope"] = (sl / (sum(closes) / len(closes))) if sl is not None else 0.0
        rets = [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes)) if closes[i - 1] > 0]
        f["l30_realvol"] = statistics.pstdev(rets) if len(rets) > 1 else 0.0
        dd = daily.get(code, {}).get(d)
        f["l30_vol_share"] = (sv / dd[4]) if dd and dd[4] > 0 else None
        f["l30_tv_share"] = (st / dd[5]) if dd and dd[5] > 0 else None
        # 대조군(일봉)
        days = seq.get(code) or []
        if d in days:
            i = days.index(d)
            prev = daily[code].get(days[i - 1]) if i >= 1 else None
            f["return_1d"] = (closes[-1] / prev[3] - 1.0) if prev and prev[3] > 0 else None   # 전일 종가
            f["return_1d"] = (dd[0] / prev[3] - 1.0) if (dd and prev and prev[3] > 0) else f["return_1d"]
            f["gap"] = (dd[0] / prev[3] - 1.0) if (dd and prev and prev[3] > 0) else None
            f["day_range"] = ((dd[1] - dd[2]) / dd[3]) if (dd and dd[3] > 0) else None
        else:
            prev = None
            f["return_1d"] = f["gap"] = f["day_range"] = None
        f["null"] = _null_feature(code, d)
        rows.append((d, code, f))

    feats = ["l30_ret", "l30_range", "l30_vwap_dev", "l30_slope", "l30_up_ratio", "l30_realvol",
             "l30_vol_share", "l30_tv_share", "return_1d", "gap", "day_range", "null"]

    out = {"full_days": full_days, "n_rows": len(rows), "horizons": {}}
    for h in horizons:
        per_date = defaultdict(list)  # date -> [(code, fwd_ret)]
        for d, code, f in rows:
            days = seq.get(code) or []
            if d not in days:
                continue
            i = days.index(d)
            if i + h >= len(days):
                continue
            c0, c1 = daily[code][days[i]][3], daily[code][days[i + h]][3]
            if c0 <= 0:
                continue
            per_date[d].append((code, c1 / c0 - 1.0, f))
        hres = {"n_dates": len(per_date), "dates": sorted(per_date), "features": {}}
        for fn in feats:
            aucs, ics = [], []
            for d, lst in per_date.items():
                sub = [(c, r, f.get(fn)) for c, r, f in lst if f.get(fn) is not None]
                if len(sub) < 20:
                    continue
                sc = [s for _, _, s in sub]
                ys = [1 if r > 0 else 0 for _, r, _ in sub]
                a = _auc(sc, ys)
                if a is not None:
                    aucs.append(a)
                ic = _spearman(sc, [r for _, r, _ in sub])
                if ic is not None:
                    ics.append(ic)
            rec = {
                "n_dates": len(aucs),
                "daily_auc_mean": (sum(aucs) / len(aucs)) if aucs else None,
                "daily_auc_std": statistics.pstdev(aucs) if len(aucs) > 1 else None,
                "daily_auc_min": min(aucs) if aucs else None,
                "daily_auc_max": max(aucs) if aucs else None,
                "daily_auc_list": [round(a, 4) for a in aucs],
                "ic_mean": (sum(ics) / len(ics)) if ics else None,
                "ic_t": ((sum(ics) / len(ics)) / (statistics.stdev(ics) / len(ics) ** 0.5))
                        if len(ics) > 2 else None,
            }
            hres["features"][fn] = rec
        out["horizons"][f"h{h}"] = hres

    print(json.dumps(out, ensure_ascii=False, indent=2))
    if args.json_out:
        try:
            os.makedirs(os.path.dirname(args.json_out), exist_ok=True)
            with open(args.json_out, "w") as fh:
                json.dump(out, fh, ensure_ascii=False, indent=2)
            print("saved:", args.json_out)
        except Exception as e:  # noqa: BLE001
            print("save failed:", e)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

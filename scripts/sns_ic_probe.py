#!/usr/bin/env python3
"""sns_ic_probe.py — SNS 파생 피처의 **일별 횡단면 IC** 를 잰다 (패널 빌드 전 게이트).

왜 필요한가 (2026-11-13 실측, CG248):
  `sns_post_features` 는 writer 스케줄 부재로 2026-09-30 이후 사실상 비어 있었다(최근일 종목 1~15개).
  원천 `sns_posts` 는 살아 있었고(11-13 12,886건·245종목), writer 를 45일 창으로 한 번 돌리자
  18,807행(11-13 245종목)으로 복구됐다. 그런데 **패널 재빌드는 수 시간**이다. 그 전에 물어야 한다:
  "이 피처가 다음날 수익률을 가로질러 설명하는가"(횡단면 IC). IC≈0 이면 패널 빌드는 낭비다.

계산 규칙:
  · 같은 날짜 안에서만 순위상관(Spearman) — 시장 공통분을 날짜 고정효과로 제거한다.
  · 전방수익: fwd1 = 다음 종가/오늘 종가 − 1, fwd5 = 5거래일 뒤 종가/오늘 종가 − 1.
  · 체결성 오염 방지: |당일 등락| ≥ 25% 행은 제외(상한가 근처 — 살 수 없는 가격).
    ⚠ 이건 돈 지표가 아니라 **피처 진단**이다. 수수료·체결 시뮬은 하지 않는다(그건
      fillable_expectancy·money_anchor_repeat 소관).
  · 날짜별 IC 의 평균·표준편차·t = mean/(std/√n), 양(+) 날짜 비율을 낸다.

사용(컨테이너, cwd=/app):
  python -u scripts/sns_ic_probe.py --days 120 --json-out /app/reports/overnight/cg248_sns_ic.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from statistics import mean, pstdev

import psycopg2

FEATURES = ["sentiment_score", "attention_score", "momentum_score", "author_quality_score",
            "post_count", "kalman_sentiment", "kalman_attention", "kalman_momentum",
            "kalman_activity"]


def connect():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
    )


def spearman(xs, ys):
    """순위 피어슨 — scipy 없이도 되게 직접 계산(동순위는 평균순위)."""
    n = len(xs)
    if n < 5:
        return None

    def rank(v):
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = rank(xs), rank(ys)
    mx, my = mean(rx), mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SNS 피처 일별 횡단면 IC(CG248)")
    ap.add_argument("--days", type=int, default=120, help="달력일 창(전방 5일 여유 포함)")
    ap.add_argument("--horizons", default="1,5", help="전방 수익 지평(콤마)")
    ap.add_argument("--min-stocks", type=int, default=20, help="날짜별 최소 종목수")
    ap.add_argument("--max-day-chg", type=float, default=25.0, help="당일 등락 절대값 상한(%)")
    ap.add_argument("--json-out", default="/app/reports/overnight/cg248_sns_ic.json")
    ap.add_argument("--universe", default="sns", choices=["sns", "market"],
                    help="sns=SNS 관측 종목 풀(기본) · market=전 시장(market_data) 대조군")
    ap.add_argument("--money", action="store_true",
                    help="IC 와 함께 'SNS 점수 top-k 체결가능 순기대 − 풀평균'을 잰다(돈 지표)")
    ap.add_argument("--money-k", default="3,5,10", help="top-k 목록(콤마)")
    ap.add_argument("--money-features",
                    default="sentiment_score,kalman_activity,attention_score",
                    help="돈 지표를 잴 SNS 점수 열(콤마)")
    ap.add_argument("--money-invert", default="",
                    help="역방향(낮은 값 top)으로 잴 열 목록(콤마) — 음(−) IC 피처의 반대편 확인용")
    ap.add_argument("--min-value", type=float, default=1e9, help="일 거래대금 하한(원)")
    ap.add_argument("--fee", type=float, default=0.21, help="수수료 왕복(%p)")
    args = ap.parse_args(argv)

    horizons = [int(x) for x in args.horizons.split(",") if x.strip()]
    since = date.today() - timedelta(days=args.days)
    conn = connect()
    cur = conn.cursor()
    if args.universe == "market":
        # 전 시장 대조군 모드 — SNS 조인 없이 market_data 만 쓴다.
        # WHY(2026-11-13 CG250 후속): 저거래대금 Δ(+0.70%p, 30억 하한)가 **SNS 에 언급된 종목**이라는
        # 조건에서만 나오는 것인지, 아니면 시장 전체의 유동성 효과인지 갈라야 한다. 전자면 SNS 축,
        # 후자면 트레이더 선택규칙 축이다(모델 AUC 와 무관).
        rows = []
        cur.close()
        conn.close()
    else:
        cur.execute(
            f"""
            SELECT s.stock_code, s.trade_date, {', '.join('s.' + f for f in FEATURES)},
                   m.close_price
            FROM sns_post_features s
            JOIN market_data m
              ON m.stock_code = s.stock_code AND m.trade_date = s.trade_date
            WHERE s.trade_date >= %s AND m.close_price > 0
            ORDER BY s.stock_code, s.trade_date
            """,
            (since,),
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        if not rows:
            print("[ic] 표본 없음 — sns_post_features/market_data 조인 결과 0행")
            return 2

    # 종목별 시계열에서 전방수익을 만든다.
    # ⚠ 전방수익은 **관측일이 아니라 거래일 달력**으로 계산한다 — SNS 관측이 띄엄띄엄하면
    #   '다음 관측일' 수익이 5일치가 되어 지평이 뒤섞인다(측정 무효). 그래서 market_data 의
    #   전체 종가 시계열을 따로 읽어 '다음 거래일'을 정의한다.
    by_code: dict[str, list] = {}
    for r in rows:
        by_code.setdefault(r[0], []).append(r[1:])   # (trade_date, f0..f8, close)

    conn = connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT stock_code, trade_date, close_price, trading_value
        FROM market_data
        WHERE trade_date >= %s AND close_price > 0
        ORDER BY stock_code, trade_date
        """,
        (since - timedelta(days=20),),               # 전방 5일 확보용 여유
    )
    price_rows = cur.fetchall()
    cur.close()
    conn.close()
    px: dict[str, list] = {}
    for code, d, c, v in price_rows:
        px.setdefault(code, []).append((d, float(c), float(v or 0.0)))
    closes: dict[str, dict] = {}
    tvals: dict[str, dict] = {}
    dates_by_code: dict[str, list] = {}
    for code, seq in px.items():
        seq.sort(key=lambda t: t[0])
        closes[code] = {d: c for d, c, _ in seq}
        tvals[code] = {d: v for d, _, v in seq}
        dates_by_code[code] = [d for d, _, _ in seq]

    records = []
    if args.universe == "market":
        for code, seq in px.items():
            ds = dates_by_code[code]
            for i, (d, c, v) in enumerate(seq):
                rec = {"date": d, "code": code, "vals": {}, "close": c, "value": v}
                for h in horizons:
                    j = i + h
                    rec[f"fwd{h}"] = ((closes[code][ds[j]] / c - 1.0) * 100.0
                                      if j < len(ds) else None)
                records.append(rec)
    for code, seq in by_code.items():
        ds = dates_by_code.get(code)
        if not ds:
            continue
        idx = {d: i for i, d in enumerate(ds)}
        for t in seq:
            d = t[0]
            i = idx.get(d)
            if i is None:
                continue
            vals = {f: t[1 + k] for k, f in enumerate(FEATURES)}
            rec = {"date": d, "code": code, "vals": vals, "close": float(t[-1]),
                   "value": tvals.get(code, {}).get(d, 0.0)}
            for h in horizons:
                j = i + h
                rec[f"fwd{h}"] = ((closes[code][ds[j]] / float(t[-1]) - 1.0) * 100.0
                                  if j < len(ds) else None)
            records.append(rec)

    # 당일 등락 필터(체결 불가 급등주 제거) — 종목 내 직전 관측 종가 대비.
    # ⚠ 관측일이 띄엄띄엄하면 '그 사이 수익'이 잡히므로 보수적으로 크게 잡힌 행만 제외한다.
    by_date: dict = {}
    for r in records:
        by_date.setdefault(r["date"], []).append(r)

    day_chg: dict[tuple, float] = {}
    for code, ds in dates_by_code.items():
        for i in range(1, len(ds)):
            p0, p1 = closes[code][ds[i - 1]], closes[code][ds[i]]
            if p0 > 0:
                day_chg[(code, ds[i])] = (p1 / p0 - 1.0) * 100.0

    out = {"generated_at": date.today().isoformat(), "since": str(since),
           "rows_joined": len(rows), "features": FEATURES, "horizons": horizons,
           "min_stocks": args.min_stocks, "max_day_chg_pct": args.max_day_chg, "result": {}}

    # 전 시장 모드엔 SNS 열이 없다(IC 계산 불가) — 돈 지표만 잰다.
    for f in (FEATURES if args.universe == "sns" else []):
        out["result"][f] = {}
        for h in horizons:
            ics, per_date = [], {}
            for d, recs in sorted(by_date.items()):
                xs, ys = [], []
                for r in recs:
                    y = r.get(f"fwd{h}")
                    if y is None:
                        continue
                    c = day_chg.get((r["code"], d))
                    if c is not None and abs(c) >= args.max_day_chg:
                        continue
                    v = r["vals"][f]
                    if v is None:
                        continue
                    xs.append(float(v))
                    ys.append(float(y))
                if len(xs) < args.min_stocks:
                    continue
                ic = spearman(xs, ys)
                if ic is not None:
                    ics.append(ic)
                    per_date[str(d)] = round(ic, 4)
            if not ics:
                out["result"][f][f"fwd{h}"] = {"n_dates": 0, "mean_ic": None}
                continue
            m = mean(ics)
            sd = pstdev(ics) if len(ics) > 1 else 0.0
            t = m / (sd / (len(ics) ** 0.5)) if sd > 0 else 0.0
            out["result"][f][f"fwd{h}"] = {
                "n_dates": len(ics), "mean_ic": round(m, 4), "std": round(sd, 4),
                "t": round(t, 2), "pos_dates_pct": round(100.0 * sum(1 for x in ics if x > 0) / len(ics), 1),
                "dates": per_date}

    if args.money:
        ks = [int(x) for x in args.money_k.split(",") if x.strip()]
        mfeats = [f.strip() for f in args.money_features.split(",") if f.strip()]
        inv = {f.strip() for f in args.money_invert.split(",") if f.strip()}
        out["money"] = {"fee_roundtrip_pct": args.fee, "min_value_krw": args.min_value,
                        "max_day_chg_pct": args.max_day_chg, "horizon": 1,
                        "note": "SNS 점수 top-k vs 풀평균(같은 날·같은 필터). "
                                "체결성 필터: 당일등락 <25% & 거래대금 ≥10억 — 살 수 없는 가격 제외.",
                        "result": {}}
        for f in mfeats:
            out["money"]["result"][f] = {}
            for k in ks:
                deltas, kept_n, tops, pools = [], [], [], []
                for d, recs in sorted(by_date.items()):
                    def mval(r):
                        """랭킹 변수 — SNS 열 또는 시장 대조 변수(mkt_value·abs_day_chg)."""
                        if f in r["vals"]:
                            return r["vals"][f]
                        if f == "mkt_value":
                            return r.get("value", 0.0)
                        if f == "abs_day_chg":
                            return abs(day_chg.get((r["code"], d), 0.0))
                        return None

                    pool = [r for r in recs
                            if r.get("fwd1") is not None
                            and r.get("value", 0.0) >= args.min_value
                            and abs(day_chg.get((r["code"], d), 0.0)) < args.max_day_chg
                            and mval(r) is not None]
                    if len(pool) < max(args.min_stocks, k + 5):
                        continue
                    rev = f not in inv
                    ranked = sorted(pool, key=lambda r: float(mval(r)), reverse=rev)[:k]
                    pool_mean = mean(float(r["fwd1"]) for r in pool)
                    top_mean = mean(float(r["fwd1"]) for r in ranked)
                    deltas.append(top_mean - pool_mean)
                    tops.append(top_mean - args.fee)
                    pools.append(pool_mean - args.fee)
                    kept_n.append(len(pool))
                if not deltas:
                    out["money"]["result"][f][f"k{k}"] = {"n_dates": 0}
                    continue
                m = mean(deltas)
                sd = pstdev(deltas) if len(deltas) > 1 else 0.0
                t = m / (sd / (len(deltas) ** 0.5)) if sd > 0 else 0.0
                half = len(deltas) // 2
                out["money"]["result"][f][f"k{k}"] = {
                    "n_dates": len(deltas), "delta_mean_pct": round(m, 4),
                    "std": round(sd, 4), "t": round(t, 2),
                    "pos_dates_pct": round(100.0 * sum(1 for x in deltas if x > 0) / len(deltas), 1),
                    "split_half_pct": [round(mean(deltas[:half]), 4) if half else None,
                                       round(mean(deltas[half:]), 4) if half else None],
                    "topk_net_pct": round(mean(tops), 4),
                    "pool_net_pct": round(mean(pools), 4),
                    "avg_pool_rows": round(mean(kept_n), 1)}
        print("[money] SNS 점수 top-k − 풀평균(체결성 필터·수수료 0.21%p 차감은 fwd 수익 안에서 미적용 — "
              "양쪽 동일 적용이므로 Δ 에서 상쇄됨)")
        for f in mfeats:
            for k in ks:
                r = out["money"]["result"][f][f"k{k}"]
                if r.get("n_dates"):
                    print(f"   {f:<22} k={k:<3} Δ {r['delta_mean_pct']:+.4f}%p "
                          f"(t {r['t']:+.2f}, 날짜 {r['n_dates']}, +{r['pos_dates_pct']:.0f}%, "
                          f"앞/뒤 {r['split_half_pct']})")

    os.makedirs(os.path.dirname(args.json_out), exist_ok=True)
    with open(args.json_out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)

    print(f"[ic] 유니버스 {args.universe} · 조인 {len(rows)}행 · 지평 {horizons} · 날짜 {len(by_date)}개 "
          f"({min(by_date)}~{max(by_date)})")
    for f in (FEATURES if args.universe == "sns" else []):
        line = [f"{f:<24}"]
        for h in horizons:
            r = out["result"][f][f"fwd{h}"]
            if r["mean_ic"] is None:
                line.append(f"fwd{h} n=0")
            else:
                line.append(f"fwd{h} IC {r['mean_ic']:+.4f} (t {r['t']:+.2f}, "
                            f"날짜 {r['n_dates']}, +{r['pos_dates_pct']:.0f}%)")
        print("  ".join(line))
    print(f"[ic] 저장: {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

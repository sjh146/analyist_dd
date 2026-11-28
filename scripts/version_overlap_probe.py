#!/usr/bin/env python3
"""version_overlap_probe.py — 배포 피드의 **판본(ml_predictions.model_version) 교차** 실측 (읽기 전용).

WHY (CG541, 2026-11-28)
  전방 성적표(forward_scorecard)가 "h1 22날짜 = champ 13d + v1.0 9d" 처럼 **판본이 섞인 pooled**
  수치를 낸다. 두 판본의 점수가 충분히 다르면 결합(rank-mean)이 단일 모델을 넘을 수 있고,
  반대로 ρ 가 매우 높으면 앙상블 축을 영구히 닫는다 — 어느 쪽이든 다음 레버 판단이 닫힌다.

WHAT
  ① 판본별 커버리지: 행수 · 날짜수 · 구간(첫/끝)
  ② 판본 **동일 날짜 교집합** 날짜 수(0 이면 결합 측정 자체가 불가 — 그 사실을 1급 출력으로 남긴다)
  ③ 교집합 날짜의 날짜별 Spearman ρ(동점 평균순위) 중앙값 · top-k 겹침 (k=10)
  ④ 판본별 전방 **돈 지표**: 체결성 필터(상한가/급등/거래대금) + 수수료 왕복 0.21%p +
     널 기준선(그날 체결가능 풀평균) — scripts/forward_scorecard.py 의 함수를 재사용한다
     (수수료·필터 정의의 단일 진실원).

한계
  · 판본 구간이 시간상 겹치지 않으면 ④ 의 판본 간 비교는 **시점 교란**이다(국면 차이와 모델 차이가
    분리되지 않는다). 그 경우 널(pool) 기준선 차이를 함께 보고해 국면 효과를 분리한다.
  · 예측은 발행 시점 스냅샷이다(이후 재학습·백필로 소급 수정되지 않음 — 그게 장점).

사용(컨테이너): docker exec stock_xgboost_ml sh -c 'cd /app && PYTHONPATH=/app python -u scripts/version_overlap_probe.py'
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forward_scorecard import (  # noqa: E402  (수수료·필터·돈지표 단일 진실원)
    FEE_BUY,
    FEE_SELL,
    LIMIT_UP_PCT,
    TAX_SELL,
    _dedupe_latest,
    _pg_connect,
    fillable_ok,
    money_stats,
)


def _avg_ranks(vals):
    """동점은 평균순위. returns ranks aligned to input order."""
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(a, b):
    if len(a) < 3:
        return None
    ra, rb = _avg_ranks(a), _avg_ranks(b)
    n = len(ra)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    da = sum((x - ma) ** 2 for x in ra) ** 0.5
    db = sum((y - mb) ** 2 for y in rb) ** 0.5
    if da <= 0 or db <= 0:
        return None
    return num / (da * db)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/app/reports/overnight/cg541_version_overlap.json")
    ap.add_argument("--topk", type=int, default=10)
    ap.add_argument("--min-value", type=float, default=1e9)
    ap.add_argument("--max-day-chg", type=float, default=25.0)
    ap.add_argument("--limit-up-pct", type=float, default=LIMIT_UP_PCT)
    ap.add_argument("--fee-buy", type=float, default=FEE_BUY)
    ap.add_argument("--fee-sell", type=float, default=FEE_SELL)
    ap.add_argument("--tax-sell", type=float, default=TAX_SELL)
    args = ap.parse_args()

    fee_rt = args.fee_buy + args.fee_sell + args.tax_sell

    conn = _pg_connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT stock_code, prediction_date, model_version, confidence, created_at "
        "FROM ml_predictions WHERE confidence IS NOT NULL ORDER BY 1,2,5"
    )
    raw = cur.fetchall()
    ver_dates = defaultdict(set)
    ver_rows = defaultdict(int)
    for code, d, ver, conf, _ts in raw:
        ver_dates[ver].add(str(d)[:10])
        ver_rows[ver] += 1
    preds = _dedupe_latest(raw)
    ver_counts = defaultdict(int)
    for _, _, ver, _ in preds:
        ver_counts[ver] += 1

    stocks = sorted({p[0] for p in preds})
    cur.execute(
        "SELECT stock_code, trade_date, close_price, trading_value FROM market_data "
        "WHERE close_price IS NOT NULL AND stock_code = ANY(%s) ORDER BY stock_code, trade_date",
        (stocks,),
    )
    closes = defaultdict(dict)
    tvals = defaultdict(dict)
    seq = defaultdict(list)
    for code, dt, px, tv in cur.fetchall():
        d = str(dt)[:10]
        closes[str(code)][d] = float(px)
        tvals[str(code)][d] = float(tv) if tv is not None else None
        seq[str(code)].append(d)
    conn.close()

    versions = sorted(ver_dates)
    cover = {
        v: {
            "rows_raw": ver_rows[v],
            "rows_deduped": ver_counts.get(v, 0),
            "n_dates": len(ver_dates[v]),
            "first": min(ver_dates[v]) if ver_dates[v] else None,
            "last": max(ver_dates[v]) if ver_dates[v] else None,
        }
        for v in versions
    }

    pairs = []
    for i in range(len(versions)):
        for j in range(i + 1, len(versions)):
            a, b = versions[i], versions[j]
            both = sorted(ver_dates[a] & ver_dates[b])
            pairs.append({"a": a, "b": b, "n_common_dates": len(both), "common_dates": both[:20]})

    by_date_ver = defaultdict(lambda: defaultdict(dict))
    for code, d, ver, conf, _ts in raw:
        by_date_ver[str(d)[:10]][ver][str(code).zfill(6)] = float(conf)
    overlap_stats = []
    for pr in pairs:
        a, b = pr["a"], pr["b"]
        rhos, overlaps = [], []
        for d in pr["common_dates"]:
            sa, sb = by_date_ver[d][a], by_date_ver[d][b]
            common = sorted(set(sa) & set(sb))
            if len(common) < 10:
                continue
            va = [sa[c] for c in common]
            vb = [sb[c] for c in common]
            rho = spearman(va, vb)
            if rho is not None:
                rhos.append(rho)
            ta = {c for c, _ in sorted(sa.items(), key=lambda kv: -kv[1])[: args.topk]}
            tb = {c for c, _ in sorted(sb.items(), key=lambda kv: -kv[1])[: args.topk]}
            overlaps.append(len(ta & tb))
        pr.update({
            "n_rho_dates": len(rhos),
            "rho_median": round(statistics.median(rhos), 4) if rhos else None,
            "rho_values": [round(x, 4) for x in rhos],
            f"top{args.topk}_overlap_median": statistics.median(overlaps) if overlaps else None,
            f"top{args.topk}_overlap_values": overlaps,
        })
        overlap_stats.append(pr)

    maxh = 5
    money = {}
    for h in (1, maxh):
        by_ver = defaultdict(lambda: defaultdict(list))
        skipped = 0
        for code, pdate, ver, conf in preds:
            days = seq.get(code)
            if not days:
                skipped += 1
                continue
            try:
                i = days.index(pdate)
            except ValueError:
                skipped += 1
                continue
            if i + h >= len(days):
                skipped += 1
                continue
            c0 = closes[code].get(days[i])
            c1 = closes[code].get(days[i + h])
            if not c0 or c1 is None:
                skipped += 1
                continue
            r = c1 / c0 - 1.0
            ok = fillable_ok(code, days, closes, tvals, i,
                             max_day_chg=args.max_day_chg, limit_up_pct=args.limit_up_pct,
                             min_value=args.min_value)
            by_ver[ver][pdate].append((conf, r, ok))
        money[f"h{h}"] = {
            ver: money_stats(d, args.topk, fee_rt) for ver, d in sorted(by_ver.items())
        }
        money[f"h{h}"]["_skipped"] = skipped

    n_overlap = max((p["n_common_dates"] for p in pairs), default=0)
    verdict_code = "NO_OVERLAP" if n_overlap == 0 else "HAS_OVERLAP"
    payload = {
        "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "probe": "CG541 version overlap",
        "predictions_rows_raw": len(raw),
        "predictions_rows_deduped": len(preds),
        "model_versions": dict(ver_counts),
        "coverage": cover,
        "pairs": overlap_stats,
        "verdict_code": verdict_code,
        "fee_roundtrip_pct": round(fee_rt * 100.0, 4),
        "money_filter": {
            "exclude_limit_up": f"day_chg >= {args.limit_up_pct}%",
            "max_day_chg_pct": args.max_day_chg,
            "min_value_krw": args.min_value,
        },
        "money": money,
        "note": ("NO_OVERLAP = 판본들이 시간상 겹치지 않는다 → 같은 날짜 결합(앙상블) 측정 불가. "
                 "판본별 money 비교는 시점 교란을 포함한다(pool_net_mean_pct 를 함께 읽어 국면 차이를 분리)."),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    try:
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, "w") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print("saved:", args.out)
    except Exception as exc:  # noqa: BLE001
        print("save failed:", exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

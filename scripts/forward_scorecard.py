#!/usr/bin/env python3
"""배포 경로 **전방(forward) 성적표** — ml_predictions × 실현 선행수익.

WHY (2026-10-02, CG68)
- 지금까지 배포 경로의 OOS 는 전부 '창' 기반 champion_robust_eval 인데, 학습구간이 항상
  최신까지라 **남는 창이 학습구간 이전**뿐이었다(CG45·CG58·CG61·CG62 실측) → 전방 검증이 없다.
- 실거래 경로는 매일 `ml_predictions`(stock_code·prediction_date·model_version·confidence·
  predicted_direction) 를 남긴다(2026-09-22~ , 약 4,340종목/일). 이것을 실현 선행수익과
  조인하면 **진짜 전방 표본**이 된다(되돌릴 수 없는 변경 없음 — 읽기 전용 측정).

정의
- 라벨: r_h = close(t+h)/close(t) - 1  (h 거래일, t = prediction_date). h=1(챔피언 학습 라벨)과
  h=5(트레이더 보유기간) 를 함께 낸다.
- pooled AUC = confidence vs (r_h > 0) 전량. 날짜별 횡단면 AUC = 날짜마다 계산 후 평균.
- top-k 실현수익 = 날짜별 confidence 상위 k 종목의 평균 r_h (수수료 전).

WHY (2026-10-08, CG137) — `money` 블록 신설: 최우선 규칙(2026-10-04) 정합
- 종전 창에는 **체결성 필터가 없는** top10_ret_mean / all_ret_mean 만 있었다 → 이 스택의 최우선
  규칙(「체결성 필터 없이 계산한 기대값은 보고하지 말라」·수수료 왕복 0.21%p 차감)과 어긋난다
  (실측 근거: 살 수 없는 상한가 종목이 +5.23%/세션을 만든다 → 필터 후 −0.67%).
- 그래서 창마다 `money` 블록을 추가한다:
    체결성 = 상한가/급등 제외(|당일등락| >= max_day_chg 25% 또는 >= limit_up 29.9%) +
             거래대금 하한(min_value 1e9 원) — `fillable_topk_expectancy.py` 와 같은 정의,
    순기대 = r_h − 수수료 왕복(FEE_BUY+FEE_SELL+TAX_SELL = 0.21%p) — 상수는 `fillable_expectancy`
             에서 import(단일 진실원),
    널 기준선 = 그 날 **체결 가능 후보 전체의 평균**(= 무작위 k 바스켓 기대값, 베타 제거용),
    판정량 = Δ = top-k 순기대 − 풀평균 순기대 (%p/세션) + t + 앞/뒤 절반.
- ⚠ 수수료는 Δ 에서 상쇄된다(top·pool 에 같은 값을 빼므로) — 수수료가 걸리는 곳은 **절대 수준**
  (topk_net_mean 이 양수인가)이다. Δ 만 보고 "수수료 반영했다"고 말하지 말 것.
- ⚠ `rows`(필터 판정 가능 행) / `kept`(통과) / `no_filter_data`(당일등락·거래대금 자료 없음) 를
  함께 보고한다 — kept/rows 가 작으면 그 수치는 표본 부족이다(13행/세션이면 어떤 Δ 도 잡음).

사용(컨테이너): docker exec stock_xgboost_ml python /app/scripts/forward_scorecard.py
자체점검: python3 scripts/_forward_scorecard_money_test.py
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
from collections import defaultdict

import psycopg2

try:  # 수수료 단일 진실원(scripts/fillable_expectancy.py). 없으면 같은 값으로 폴백.
    from fillable_expectancy import FEE_BUY, FEE_SELL, TAX_SELL, LIMIT_UP_PCT
except Exception:  # noqa: BLE001
    FEE_BUY = 0.00015
    FEE_SELL = 0.00015
    TAX_SELL = 0.0018
    LIMIT_UP_PCT = 29.9


def _pg_connect():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def _auc(pairs):
    """pairs = [(score, y)] → rank-based AUC. 한 클래스뿐이면 None."""
    pos = [t for t in pairs if t[1] == 1]
    neg = [t for t in pairs if t[1] == 0]
    if not pos or not neg:
        return None
    merged = sorted(pairs, key=lambda x: x[0])
    ranks = {}
    i = 0
    r = 1
    while i < len(merged):
        j = i
        while j + 1 < len(merged) and merged[j + 1][0] == merged[i][0]:
            j += 1
        avg = (r + (r + (j - i))) / 2.0
        for k in range(i, j + 1):
            ranks[id(merged[k])] = avg
        r += (j - i) + 1
        i = j + 1
    s_pos = sum(ranks[id(p)] for p in pos)
    n1, n0 = len(pos), len(neg)
    return (s_pos - n1 * (n1 + 1) / 2.0) / (n1 * n0)


def _dedupe_latest(rows):
    """rows = [(stock, date, version, confidence, created_at)] → [(stock, date, version, confidence)].

    같은 (종목, 날짜)에 두 버전이 남을 수 있다(승격/롤백이 같은 날 겹친 경우). 그대로 세면
    그날 표본이 두 번 들어가므로 created_at 최신 행만 채택한다(입력은 created_at 오름차순).
    """
    latest = {}
    for a, b, c, d, e in rows:
        latest[(str(a), str(b)[:10])] = (c, float(d))
    out = [(k[0], k[1], v[0], v[1]) for k, v in latest.items()]
    out.sort(key=lambda x: (x[0], x[1]))
    return out


def fillable_ok(code, days, closes, tvals, i, max_day_chg=25.0,
                limit_up_pct=LIMIT_UP_PCT, min_value=1e9):
    """체결 가능성 판정. True=체결가능 / False=체결불가(상한가·급등·저거래대금) / None=판정자료 없음.

    `fillable_topk_expectancy.py` 의 필터와 같은 의미다:
    당일등락 >= limit_up(29.9%) 또는 >= max_day_chg(25%) → 살 수 없다(상한가 부근),
    거래대금 < min_value(10억) → 사실상 체결 불가. 판정에 필요한 값(전일종가·거래대금)이
    없으면 None 으로 두고 **통과시키지 않는다**(보수적 — 필터 없이 계산한 기대값은 보고 금지).
    """
    if i - 1 < 0:
        return None
    c0 = closes.get(code, {}).get(days[i])
    cprev = closes.get(code, {}).get(days[i - 1])
    tv = tvals.get(code, {}).get(days[i])
    if c0 is None or cprev is None or tv is None:
        return None
    dc = (c0 / cprev - 1.0) * 100.0
    if dc >= limit_up_pct or dc >= max_day_chg:
        return False
    return tv >= min_value


def money_stats(by_date, k, fee_rt):
    """체결 가능 후보만으로 top-k vs 풀평균(널 기준선) 순기대 Δ 를 낸다.

    by_date = {date: [(conf, r, fillable_bool_or_None), ...]}
      → {"n_dates","rows","kept","no_filter_data","kept_ratio",
         "topk_net_mean_pct","pool_net_mean_pct","delta_mean_pct","delta_sd_pct","delta_t",
         "split_half_pct","topk_pos_dates","date_deltas_pct"}

    순기대 단위는 %p/세션(수수료 왕복 차감). Δ 는 top-k − 풀평균 = 베타 제거 초과분이다.
    t 는 세션별 Δ 의 1표본 t. sd <= 1e-9(제로분산)면 t=None(부동소수 잔차로 t 가 1e16 로 튀는 것 방지).
    """
    rows = 0
    kept = 0
    nodata = 0
    deltas = []
    top_means = []
    pool_means = []
    pos_dates = 0
    for d in sorted(by_date):
        lst = by_date[d]
        rows += len(lst)
        nodata += sum(1 for x in lst if x[2] is None)
        fil = [x for x in lst if x[2] is True]
        kept += len(fil)
        if not fil:
            continue
        fil.sort(key=lambda x: -x[0])
        top = [r for _, r, _ in fil[:k]]
        pool = [r for _, r, _ in fil]
        t_net = sum(top) / len(top) - fee_rt
        p_net = sum(pool) / len(pool) - fee_rt
        top_means.append(t_net)
        pool_means.append(p_net)
        deltas.append(t_net - p_net)
        if t_net - p_net > 0:
            pos_dates += 1
    n = len(deltas)
    mean = (sum(deltas) / n) if n else None
    sd = statistics.stdev(deltas) if n > 1 else None
    t = None
    if n > 1 and sd is not None and sd > 1e-9:
        t = mean / (sd / math.sqrt(n))
    half = n // 2
    split = None
    if half >= 1:
        a = sum(deltas[:half]) / half
        b = deltas[half:]
        split = [a * 100.0, (sum(b) / len(b)) * 100.0 if b else None]
    return {
        "k": k,
        "fee_roundtrip_pct": round(fee_rt * 100.0, 4),
        "n_dates": n,
        "rows": rows,
        "kept": kept,
        "no_filter_data": nodata,
        "kept_ratio": round(kept / rows, 4) if rows else None,
        "topk_net_mean_pct": round(sum(top_means) / len(top_means) * 100.0, 4) if top_means else None,
        "pool_net_mean_pct": round(sum(pool_means) / len(pool_means) * 100.0, 4) if pool_means else None,
        "delta_mean_pct": round(mean * 100.0, 4) if mean is not None else None,
        "delta_sd_pct": round(sd * 100.0, 4) if sd is not None else None,
        "delta_t": round(t, 4) if t is not None else None,
        "split_half_pct": [round(x, 4) if x is not None else None for x in split] if split else None,
        "topk_pos_dates": pos_dates,
        "date_deltas_pct": [round(x * 100.0, 4) for x in deltas],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/app/reports/overnight/forward_scorecard.json")
    ap.add_argument("--topk", type=int, default=10)
    ap.add_argument("--min-value", type=float, default=1e9,
                    help="거래대금 하한(원) — 체결성 필터")
    ap.add_argument("--max-day-chg", type=float, default=25.0,
                    help="당일등락 상한(%%) — 이 이상 오른 종목은 살 수 없다")
    ap.add_argument("--limit-up-pct", type=float, default=LIMIT_UP_PCT)
    ap.add_argument("--fee-buy", type=float, default=FEE_BUY)
    ap.add_argument("--fee-sell", type=float, default=FEE_SELL)
    ap.add_argument("--tax-sell", type=float, default=TAX_SELL)
    args = ap.parse_args()

    fee_rt = args.fee_buy + args.fee_sell + args.tax_sell

    conn = _pg_connect()
    cur = conn.cursor()
    cur.execute("SELECT stock_code, prediction_date, model_version, confidence, created_at "
                "FROM ml_predictions WHERE confidence IS NOT NULL ORDER BY 1,2,5")
    preds = _dedupe_latest(cur.fetchall())
    # 모델 버전별 표본 수 — 승격/롤백 전후를 나눠 볼 수 있게 함께 낸다(model_version 귀속).
    ver_counts: dict = defaultdict(int)
    for _, _, ver, _ in preds:
        ver_counts[ver] += 1

    stocks = sorted({p[0] for p in preds})
    cur.execute("SELECT stock_code, trade_date, close_price, trading_value FROM market_data "
                "WHERE close_price IS NOT NULL ORDER BY stock_code, trade_date")
    closes: dict[str, dict] = defaultdict(dict)
    tvals: dict[str, dict] = defaultdict(dict)
    seq: dict[str, list] = defaultdict(list)
    for code, dt, px, tv in cur.fetchall():
        d = str(dt)[:10]
        closes[str(code)][d] = float(px)
        tvals[str(code)][d] = float(tv) if tv is not None else None
        seq[str(code)].append(d)
    conn.close()

    maxh = 5
    res = {}
    for h in (1, maxh):
        pairs = []
        per_date = defaultdict(list)
        # top-k
        by_date_top = defaultdict(list)
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
            y = 1 if r > 0 else 0
            pairs.append((conf, y))
            per_date[pdate].append((conf, y))
            ok = fillable_ok(code, days, closes, tvals, i,
                             max_day_chg=args.max_day_chg, limit_up_pct=args.limit_up_pct,
                             min_value=args.min_value)
            by_date_top[pdate].append((conf, r, ok))
        daily = [a for a in (_auc(v) for v in per_date.values()) if a is not None]
        top_ret = []
        for d, lst in by_date_top.items():
            lst.sort(key=lambda x: -x[0])
            top = [r for _, r, _ in lst[:args.topk]]
            if top:
                top_ret.append(sum(top) / len(top))
        all_ret = [r for lst in by_date_top.values() for _, r, _ in lst]
        res[f"h{h}"] = {
            "n_pairs": len(pairs),
            "n_dates": len(per_date),
            "skipped": skipped,
            "pooled_auc": _auc(pairs),
            "daily_auc_mean": (sum(daily) / len(daily)) if daily else None,
            "daily_auc_list": [round(x, 4) for x in daily],
            "base_rate_up": (sum(y for _, y in pairs) / len(pairs)) if pairs else None,
            f"top{args.topk}_ret_mean": (sum(top_ret) / len(top_ret)) if top_ret else None,
            "all_ret_mean": (sum(all_ret) / len(all_ret)) if all_ret else None,
            # ↓ CG137: 체결성 필터 + 수수료 + 널 기준선(최우선 규칙 정합)
            "money": money_stats(by_date_top, args.topk, fee_rt),
            "money_filter": {
                "exclude_limit_up": f"day_chg >= {args.limit_up_pct}%",
                "max_day_chg_pct": args.max_day_chg,
                "min_value_krw": args.min_value,
                "note": "당일등락·거래대금 자료가 없는 행은 통과시키지 않는다(no_filter_data) — "
                        "필터 없이 계산한 기대값은 보고 금지(최우선 규칙 3).",
            },
        }
    payload = {"generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
               "predictions_rows": len(preds),
               "model_versions": dict(ver_counts),
               "fee_roundtrip_pct": round(fee_rt * 100.0, 4),
               "result": res}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    try:
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, "w") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print("saved:", args.out)
    except Exception as e:  # noqa: BLE001
        print("save failed:", e)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

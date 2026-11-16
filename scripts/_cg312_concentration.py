"""CG312 — top-k 순기대의 **집중도 분해** (소수 세션·소수 종목 의존인가).

WHY(CG311 실측 2026-11-17): q0.05 후보의 top3 순기대가 close_h(5일)에서 +6.07%p/세션
(t 3.66)로 나왔지만 **앞 절반 +10.75 / 뒤 절반 +1.39** 로 감쇠했다. 평균이 소수 세션·
소수 종목의 큰 수익에 끌려 올라간 것인지(그렇다면 노이즈) 아닌지(넓게 퍼진 신호)를
가르지 않으면 승격 후보로 올릴 수 없다. 이 스크립트는 같은 예측·같은 필터로

  ① 세션 기여 상위 1·3·5개를 제거한 뒤 평균 (제거 후에도 양(+)인가)
  ② 종목별 기여도 집중 (상위 3종목이 총이익의 몇 %)
  ③ top-k 픽의 종목 재등장 빈도 (한 종목이 반복 등장하는가)

를 낸다. 판정 규칙: '상위 5세션 제거 후에도 순기대 > 0' 이면 넓은 신호, 아니면 집중 신호.

Usage (in container, cwd /app):
  python -u scripts/_cg312_concentration.py --arm-jsonl reports/overnight/cg306_preds.jsonl \
      --k 3 --horizon 5 --exit close_h --json-out reports/overnight/cg312_conc.json
"""
import argparse
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fillable_topk_expectancy as F  # noqa: E402


def st(vals):
    if not vals:
        return {"n": 0}
    n = len(vals)
    m = sum(vals) / n
    sd = (sum((v - m) ** 2 for v in vals) / (n - 1)) ** 0.5 if n > 1 else 0.0
    return {"n": n, "mean": round(m, 4), "sd": round(sd, 4),
            "t": round(m / (sd / n ** 0.5), 2) if sd else 0.0,
            "pos_pct": round(100.0 * sum(1 for v in vals if v > 0) / n, 1),
            "min": round(min(vals), 4), "max": round(max(vals), 4)}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm-jsonl", required=True)
    ap.add_argument("--arm-tag", default=None)
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--exit", default="close_h")
    ap.add_argument("--max-day-chg", type=float, default=25.0)
    ap.add_argument("--min-value", type=float, default=1e9)
    ap.add_argument("--json-out", default=None)
    a = ap.parse_args(argv)

    rows, bad = F.load_rows(a.arm_jsonl, a.arm_tag)
    codes = sorted({r["code"] for r in rows})
    dates = sorted({r["date"] for r in rows})
    conn = F.db_connect()
    series = F.price_series(conn, codes, dates[0], "2027-12-31")
    rows, drop = F.enrich(rows, series, a.horizon, a.exit)
    fa = argparse.Namespace(exclude_limit_up=True, max_day_chg=a.max_day_chg,
                            min_value=a.min_value, min_price=None, max_price=None)
    rows, filt = F.apply_filters(rows, fa, True)
    # 수수료 상수는 **비율**(0.0007 등)로 정의돼 있다 → %p 로 환산해 gross_pct 와 같은 눈금으로 맞춘다.
    rt = 100.0 * float(F.FEE_BUY + F.FEE_SELL + F.TAX_SELL)

    by_sess = defaultdict(list)
    for r in rows:
        by_sess[(r["fold"], r["date"])].append(r)

    per_sess, code_contrib, code_hits = {}, defaultdict(float), Counter()
    for key, rs in by_sess.items():
        pick = sorted(rs, key=lambda x: -x["score"])[:a.k]
        if len(pick) < a.k:
            continue
        net = sum(p["gross_pct"] for p in pick) / len(pick) - rt
        per_sess[key] = net
        for p in pick:
            code_hits[p["code"]] += 1
            code_contrib[p["code"]] += (p["gross_pct"] - rt) / a.k

    ser = list(per_sess.values())
    base = st(ser)
    total = sum(ser)
    ranked = sorted(per_sess.items(), key=lambda kv: -kv[1])

    out = {
        "script": "_cg312_concentration.py",
        "arm_jsonl": a.arm_jsonl, "k": a.k, "exit": a.exit, "horizon": a.horizon,
        "fee_roundtrip_pct": rt, "rows_total": len(rows), "rows_dropped_price": drop,
        "rows_filtered_out": filt, "sessions": len(per_sess),
        "base": base,
        "drop_best_n": {},
        "top_codes": [],
        "concentration": {
            "top3_sessions_share_pct": round(100.0 * sum(v for _, v in ranked[:3]) / total, 1) if total else None,
            "top5_sessions_share_pct": round(100.0 * sum(v for _, v in ranked[:5]) / total, 1) if total else None,
        },
    }
    for n in (1, 3, 5):
        kept = [v for _, v in ranked[n:]]
        out["drop_best_n"][f"drop_top{n}"] = st(kept)
    # 종목 기여: 상위 3종목만 남겼을 때 / 제외했을 때
    cc = sorted(code_contrib.items(), key=lambda kv: -kv[1])
    out["top_codes"] = [{"code": c, "contrib_sum_pct": round(v, 3), "hits": code_hits[c]}
                        for c, v in cc[:8]]
    top3 = {c for c, _ in cc[:3]}
    out["drop_top3_codes"] = st([sum(p["gross_pct"] for p in sorted(rs, key=lambda x: -x["score"])[:a.k]
                                    if p["code"] not in top3) / max(1, len([1 for p in sorted(rs, key=lambda x: -x["score"])[:a.k] if p["code"] not in top3]))
                                - rt for rs in by_sess.values() if len(rs) >= a.k])
    out["top3_codes_total_contrib_pct"] = round(sum(v for _, v in cc[:3]), 3)
    out["grand_total_contrib_pct"] = round(total, 3)
    out["distinct_codes_in_topk"] = len(code_hits)

    print(json.dumps(out, ensure_ascii=False, indent=1)[:4000])
    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        print("[ok]", a.json_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

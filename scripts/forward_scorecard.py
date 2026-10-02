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

사용(컨테이너): docker exec stock_xgboost_ml python /app/scripts/forward_scorecard.py
"""
from __future__ import annotations

import argparse
import json
import os
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/app/reports/overnight/forward_scorecard.json")
    ap.add_argument("--topk", type=int, default=10)
    args = ap.parse_args()

    conn = _pg_connect()
    cur = conn.cursor()
    cur.execute("SELECT stock_code, prediction_date, model_version, confidence "
                "FROM ml_predictions WHERE confidence IS NOT NULL ORDER BY 1,2")
    preds = [(str(a), str(b)[:10], str(c), float(d)) for a, b, c, d in cur.fetchall()]

    stocks = sorted({p[0] for p in preds})
    cur.execute("SELECT stock_code, trade_date, close_price FROM market_data "
                "WHERE close_price IS NOT NULL ORDER BY stock_code, trade_date")
    closes: dict[str, dict] = defaultdict(dict)
    seq: dict[str, list] = defaultdict(list)
    for code, dt, px in cur.fetchall():
        d = str(dt)[:10]
        closes[str(code)][d] = float(px)
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
            by_date_top[pdate].append((conf, r))
        daily = [a for a in (_auc(v) for v in per_date.values()) if a is not None]
        top_ret = []
        for d, lst in by_date_top.items():
            lst.sort(key=lambda x: -x[0])
            top = [r for _, r in lst[:args.topk]]
            if top:
                top_ret.append(sum(top) / len(top))
        all_ret = [r for lst in by_date_top.values() for _, r in lst]
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
        }
    payload = {"generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
               "predictions_rows": len(preds),
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

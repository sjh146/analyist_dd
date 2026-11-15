"""체결가능 풀평균 널 기준선의 정합성 진단 (CG294).

질문: fillable_topk_expectancy 가 산출한 '풀 평균 −1.48%p/세션(h5, stride5, 20세션)'이
실제 시장과 맞는가? 맞지 않으면 모든 돈 실험의 Δ(vs pool) 판정이 편향된다.

검사 3종:
 A. 같은 날짜·같은 코드의 동일가중 h5 수익률을 market_data 에서 직접 계산 → 도구 값과 비교
 B. 일별 수익률 분포에서 |r|>25% 이상치 수 · -50%/-33%/-25% 근방(무수정 주가 흔적) 빈도
 C. krx_trading 에 지수(KOSPI) 행이 있으면 같은 창의 지수 h5 수익률과 비교

사용: docker exec stock_xgboost_ml python /app/scripts/_pool_baseline_check.py \
        --preds /app/reports/overnight/cg292_q30_preds.jsonl --tag cg292_q30 --stride 5 --horizon 5
"""
import argparse
import json
import os
import statistics as st
from collections import defaultdict

import psycopg2


def conn():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT", "5434")),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preds", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--stride", type=int, default=5)
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--json-out", default=None)
    a = ap.parse_args()

    # 1) 채점된 날짜·코드 집합
    dates, codes = set(), set()
    with open(a.preds, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("exp") != a.tag or r.get("y_pred") is None:
                continue
            dates.add(str(r["date"]))
            codes.add(str(r["code"]).zfill(6))
    dates = sorted(dates)
    c = conn()
    cur = c.cursor()

    # 2) market_data 시계열 (코드별)
    cur.execute(
        "SELECT stock_code, trade_date, close_price FROM market_data "
        "WHERE stock_code = ANY(%s::text[]) AND trade_date BETWEEN %s AND %s "
        "ORDER BY stock_code, trade_date",
        [list(codes), dates[0], dates[-1]],
    )
    series = defaultdict(list)
    for sc, d, px in cur.fetchall():
        if px is None:
            continue
        series[str(sc).zfill(6)].append((str(d), float(px)))
    print(f"universe codes={len(codes)} dates={len(dates)} series={len(series)}")

    # B. 일별 수익률 이상치
    big, ratio_hits, n_obs = [], [], 0
    for sc, rows in series.items():
        for (d0, p0), (d1, p1) in zip(rows, rows[1:]):
            if p0 <= 0:
                continue
            r = p1 / p0 - 1.0
            n_obs += 1
            if abs(r) > 0.25:
                big.append((sc, d1, round(r, 4)))
            for ratio in (0.5, 1 / 3, 0.25, 0.2):
                if abs(r - (ratio - 1)) < 0.02:
                    ratio_hits.append((sc, d1, round(r, 4)))
    print(f"daily obs={n_obs} |r|>25%: {len(big)} ({len(big)/max(n_obs,1)*100:.2f}%)")
    print(f"  split-like (≈-50/-67/-75/-80%): {len(ratio_hits)}")
    for x in ratio_hits[:10]:
        print("   ", x)

    # A. 같은 날짜·같은 코드의 동일가중 h5 수익률(도구와 독립 계산)
    sess = dates[:: a.stride]
    per_sess = {}
    for ds in sess:
        rets = []
        for sc, rows in series.items():
            idx = {d: i for i, (d, _) in enumerate(rows)}
            i0 = idx.get(ds)
            if i0 is None or i0 + a.horizon >= len(rows):
                continue
            p0, p1 = rows[i0][1], rows[i0 + a.horizon][1]
            if p0 > 0:
                rets.append(p1 / p0 - 1.0)
        if rets:
            per_sess[ds] = (len(rets), st.mean(rets) * 100.0, st.median(rets) * 100.0)
    means = [v[1] for v in per_sess.values()]
    medians = [v[2] for v in per_sess.values()]
    print(f"eq-weight h{a.horizon} over {len(means)} sessions: mean {st.mean(means):+.3f}% "
          f"median {st.median(means):+.3f}% · per-session median of medians {st.median(medians):+.3f}%")
    print(f"  worst {min(means):+.2f}% best {max(means):+.2f}% · pos sessions {sum(1 for m in means if m > 0)}/{len(means)}")
    for ds in sess[:5]:
        if ds in per_sess:
            n, m, md = per_sess[ds]
            print(f"   {ds}: n={n} mean {m:+.2f}% median {md:+.2f}%")

    # C. 지수 비교(krx_trading 에 지수 행이 있는 경우)
    idx_rows = []
    try:
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='krx_trading'")
        cols = [r[0] for r in cur.fetchall()]
        print("krx_trading cols:", cols)
        code_col = "stock_code" if "stock_code" in cols else ("code" if "code" in cols else None)
        close_col = "close_price" if "close_price" in cols else ("close" if "close" in cols else None)
        if code_col and close_col:
            cur.execute(
                f"SELECT {code_col}, trade_date, {close_col} FROM krx_trading "
                f"WHERE trade_date BETWEEN %s AND %s AND {code_col}::text ILIKE '%%KOSPI%%' "
                f"OR {code_col}::text IN ('0001','KOSPI','1001') AND trade_date BETWEEN %s AND %s "
                f"ORDER BY trade_date", [dates[0], dates[-1], dates[0], dates[-1]])
            idx_rows = cur.fetchall()
            print(f"index rows={len(idx_rows)}")
    except Exception as e:  # noqa: BLE001
        print("index check skipped:", e)

    out = {
        "n_dates": len(dates), "n_codes": len(codes), "n_series": len(series),
        "daily_obs": n_obs, "big_moves_25pct": len(big), "split_like": len(ratio_hits),
        "split_like_examples": ratio_hits[:10],
        "eqw_h_sessions": len(means),
        "eqw_mean_pct": round(st.mean(means), 4) if means else None,
        "eqw_median_pct": round(st.median(means), 4) if means else None,
        "eqw_pos_sessions": sum(1 for m in means if m > 0) if means else 0,
        "eqw_worst_pct": round(min(means), 4) if means else None,
        "eqw_best_pct": round(max(means), 4) if means else None,
        "per_session": {k: [v[0], round(v[1], 4), round(v[2], 4)] for k, v in per_sess.items()},
        "big_move_examples": big[:15],
    }
    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        print("wrote", a.json_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

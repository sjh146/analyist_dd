#!/usr/bin/env python3
"""champion_paired_expectancy.py — 짝 비교(AUC) 를 '돈 지표' 로 환산한다 (읽기 전용).

WHY
  CG374 는 같은 창(2026-10-02~11-19)에서 배포 챔피언 vs 재학습 후보를 시드 3개로 짝 비교해
  ΔAUC +0.0278(t 5.47) 을 얻었다. 그러나 SKILL 최우선 규칙: AUC 개선만으로는 개선이 아니다.
  이 도구는 같은 예측 덤프(reports/overnight/*.jsonl)를 그대로 사용해 **체결 가능 필터 + 수수료
  0.21%p 왕복 차감** 후 세션 단위 순기대(%p/세션)를 계산하고, arm 간 짝 Δ 를 낸다.

입력 덤프 형식 (champion_robust_eval --dump-preds)
  {"exp": ..., "fold": 1, "date": "YYYY-MM-DD", "code": "024720",
   "y_true": 0, "y_pred": 0.37, "fwd_ret": -0.049516}

체결 가능 필터 (fillable_expectancy.py 와 같은 취지)
  · 당일등락 >= --max-day-chg (기본 +25%) 제외 — 종가에 살 수 없는 상한가 부근
  · 거래대금 < --min-value (기본 10억) 제외 — 유동성 없음
  · --no-filter 로 필터 해제(대조용, 보고엔 쓰지 말 것)
  당일등락·거래대금은 market_data 에서 조인해 계산한다(덤프에 없음).

수수료: 매수 0.015% + 매도 0.015% + 거래세 0.18% = 0.21%p 왕복.

사용
  set -a && . ./.env && set +a
  export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434
  python3 scripts/champion_paired_expectancy.py \
    --arms champion=reports/overnight/cg374_champion,retrain=reports/overnight/cg374_retrain_cg346 \
    --seeds 0,1,2 --topk 3,5 --out data/reports/cg374_paired_expectancy.json
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys
from collections import defaultdict

import numpy as np
import pandas as pd
import psycopg2

FEE_ROUND_TRIP_PCT = 0.21  # %p


def load_dump(pattern: str) -> pd.DataFrame:
    files = sorted(glob.glob(pattern + "*.jsonl"))
    if not files:
        raise SystemExit(f"덤프 없음: {pattern}*.jsonl")
    rows = []
    for f in files:
        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rows.append(json.loads(line))
    df = pd.DataFrame(rows)
    need = {"date", "code", "y_pred", "fwd_ret"}
    if not need.issubset(df.columns):
        raise SystemExit(f"덤프 필드 부족: {need - set(df.columns)}")
    return df


def load_market(conn, codes, start, end) -> pd.DataFrame:
    q = """
        SELECT stock_code, trade_date, close_price, trading_value
        FROM market_data
        WHERE stock_code = ANY(%s::text[]) AND trade_date BETWEEN %s AND %s
        ORDER BY stock_code, trade_date
    """
    df = pd.read_sql(q, conn, params=(list(codes), start, end))
    if df.empty:
        return df
    df["close_price"] = df["close_price"].astype(float)
    df["trading_value"] = df["trading_value"].astype(float)
    df["prev_close"] = df.groupby("stock_code")["close_price"].shift(1)
    df["day_chg_pct"] = (df["close_price"] / df["prev_close"] - 1.0) * 100.0
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
    return df[["stock_code", "trade_date", "day_chg_pct", "trading_value", "close_price"]]


def session_stats(net_by_date: dict[str, list[float]]) -> dict:
    dates = sorted(net_by_date)
    per_session = [float(np.mean(net_by_date[d])) for d in dates]
    n_sess = len(per_session)
    n_tr = sum(len(v) for v in net_by_date.values())
    if n_sess == 0:
        return {"n_sessions": 0, "n_trades": 0}
    arr = np.array(per_session)
    half = n_sess // 2
    first = float(arr[:half].mean()) if half else None
    second = float(arr[half:].mean()) if n_sess - half else None
    se = float(arr.std(ddof=1) / math.sqrt(n_sess)) if n_sess > 1 else None
    return {
        "n_sessions": n_sess,
        "n_trades": n_tr,
        "net_pct_per_session": round(float(arr.mean()), 4),
        "median": round(float(np.median(arr)), 4),
        "sd": round(float(arr.std(ddof=1)), 4) if n_sess > 1 else None,
        "se": round(se, 4) if se is not None else None,
        "t": round(float(arr.mean()) / se, 3) if se else None,
        "pos_sessions_frac": round(float((arr > 0).mean()), 3),
        "first_half": round(first, 4) if first is not None else None,
        "second_half": round(second, 4) if second is not None else None,
        "worst_session": round(float(arr.min()), 4),
        "best_session": round(float(arr.max()), 4),
        "n_dates": n_sess,
    }


def per_session_series(df: pd.DataFrame, topk: int) -> dict[str, list[float]]:
    out: dict[str, list[float]] = defaultdict(list)
    for d, g in df.groupby("date"):
        top = g.nlargest(topk, "y_pred")
        for r in top.itertuples():
            out[d].append(float(r.net_pct))
    return dict(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", required=True,
                    help="name=path_prefix(덤프 경로 접두), 콤마 구분. 첫 arm 이 challenger")
    ap.add_argument("--seeds", default="0,1,2")
    ap.add_argument("--topk", default="3,5")
    ap.add_argument("--max-day-chg", type=float, default=25.0)
    ap.add_argument("--min-value", type=float, default=1e9)
    ap.add_argument("--no-filter", action="store_true")
    ap.add_argument("--out", default="data/reports/paired_expectancy.json")
    args = ap.parse_args()

    arms = []
    for part in args.arms.split(","):
        name, _, path = part.partition("=")
        arms.append((name.strip(), path.strip()))
    seeds = [s.strip() for s in args.seeds.split(",") if s.strip()]
    ks = [int(k) for k in args.topk.split(",") if k.strip()]

    frames = {name: load_dump(path) for name, path in arms}
    for name, df in frames.items():
        df["net_pct_raw"] = df["fwd_ret"].astype(float) * 100.0 - FEE_ROUND_TRIP_PCT

    all_codes = sorted({c for df in frames.values() for c in df["code"].unique()})
    start = min(df["date"].min() for df in frames.values())
    end = max(df["date"].max() for df in frames.values())

    conn = psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=os.environ.get("POSTGRES_PORT", "5434"),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )
    mk = load_market(conn, all_codes, start, end)
    conn.close()

    result = {
        "fee_round_trip_pct": FEE_ROUND_TRIP_PCT,
        "filter": {
            "enabled": not args.no_filter,
            "max_day_chg_pct": args.max_day_chg,
            "min_value_krw": args.min_value,
        },
        "window": {"start": start, "end": end},
        "note": "fwd_ret 는 덤프에 저장된 h일 전방수익률(진입=발굴일 종가 가정). "
                "체결가·슬리피지 근사이며 실주문 성과가 아니다.",
        "arms": {},
    }

    for name, df in frames.items():
        d = df.merge(mk, left_on=["code", "date"], right_on=["stock_code", "trade_date"],
                     how="left")
        n_missing = int(d["close_price"].isna().sum())
        if args.no_filter:
            kept = d
            n_drop_chg = n_drop_val = 0
        else:
            ok_chg = (d["day_chg_pct"].isna()) | (d["day_chg_pct"] < args.max_day_chg)
            ok_val = (d["trading_value"].isna()) | (d["trading_value"] >= args.min_value)
            kept = d[ok_chg & ok_val]
            n_drop_chg = int((~ok_chg).sum())
            n_drop_val = int((~ok_val).sum())
        kept = kept.copy()
        kept["net_pct"] = kept["net_pct_raw"]
        arm_out = {
            "rows_total": int(len(d)),
            "rows_kept": int(len(kept)),
            "dropped_day_chg": n_drop_chg,
            "dropped_min_value": n_drop_val,
            "market_join_missing": n_missing,
            "topk": {},
        }
        pool = {d: float(g["net_pct"].mean()) for d, g in kept.groupby("date")}
        arm_out["pool_mean_net"] = round(float(np.mean(list(pool.values()))), 4) if pool else None
        arm_out["pool_sd"] = (round(float(np.std(list(pool.values()), ddof=1)), 4)
                              if len(pool) > 1 else None)
        for k in ks:
            stats = session_stats(per_session_series(kept, k))
            if pool:
                top = {d: float(g.nlargest(k, "y_pred")["net_pct"].mean())
                       for d, g in kept.groupby("date")}
                edge = np.array([top[d] - pool[d] for d in pool])
                se_e = float(edge.std(ddof=1) / math.sqrt(len(edge))) if len(edge) > 1 else None
                stats["select_edge_pct"] = round(float(edge.mean()), 4)
                stats["select_edge_se"] = round(se_e, 4) if se_e else None
                stats["select_edge_t"] = (round(float(edge.mean()) / se_e, 3)
                                          if se_e else None)
                stats["null_baseline_note"] = "null = 그날 체결가능 풀평균 순수익(%p)"
                stats["_edge_series"] = {d: float(top[d] - pool[d]) for d in pool}
            arm_out["topk"][str(k)] = stats
        result["arms"][name] = arm_out

    # 짝 Δ: 같은 topk · 같은 날짜(양 arm 모두 존재하는 날짜만)
    if len(arms) == 2:
        (cname, _), (bname, _) = arms
        result["paired"] = {}
        for k in ks:
            per_seed = {}
            for s in seeds:
                parts = {}
                for name, df in frames.items():
                    d = df[df["exp"].astype(str).str.endswith(f"_s{s}")] if "exp" in df else df
                    if d.empty:
                        d = df
                    d = d.merge(mk, left_on=["code", "date"],
                                right_on=["stock_code", "trade_date"], how="left")
                    if not args.no_filter:
                        ok_chg = (d["day_chg_pct"].isna()) | (d["day_chg_pct"] < args.max_day_chg)
                        ok_val = (d["trading_value"].isna()) | (d["trading_value"] >= args.min_value)
                        d = d[ok_chg & ok_val]
                    d = d.copy()
                    d["net_pct"] = d["net_pct_raw"]
                    parts[name] = {dd: float(np.mean(v))
                                   for dd, v in per_session_series(d, k).items()}
                common = sorted(set(parts[cname]) & set(parts[bname]))
                if not common:
                    per_seed[s] = {"n": 0}
                    continue
                delta = np.array([parts[cname][dd] - parts[bname][dd] for dd in common])
                se = float(delta.std(ddof=1) / math.sqrt(len(delta))) if len(delta) > 1 else None
                per_seed[s] = {
                    "n_dates": len(common),
                    "challenger_net": round(float(np.mean([parts[cname][dd] for dd in common])), 4),
                    "base_net": round(float(np.mean([parts[bname][dd] for dd in common])), 4),
                    "delta": round(float(delta.mean()), 4),
                    "se": round(se, 4) if se else None,
                    "t": round(float(delta.mean()) / se, 3) if se else None,
                    "pos_dates_frac": round(float((delta > 0).mean()), 3),
                }
            deltas = [v["delta"] for v in per_seed.values() if "delta" in v]
            summary = {}
            if deltas:
                arr = np.array(deltas)
                summary = {
                    "n_seeds": len(arr),
                    "delta_mean": round(float(arr.mean()), 4),
                    "delta_std": round(float(arr.std(ddof=1)), 4) if len(arr) > 1 else None,
                    "pos_seeds_frac": round(float((arr > 0).mean()), 3),
                }
            result["paired"][str(k)] = {"challenger": cname, "base": bname,
                                        "per_seed": per_seed, "summary": summary}
            # 선택 엣지(풀 대비)의 짝 Δ — 시장 방향을 걷어낸 비교
            ea = result["arms"][cname]["topk"][str(k)].get("_edge_series")
            eb = result["arms"][bname]["topk"][str(k)].get("_edge_series")
            if ea and eb:
                common = sorted(set(ea) & set(eb))
                if len(common) > 1:
                    d = np.array([ea[x] - eb[x] for x in common])
                    se_d = float(d.std(ddof=1) / math.sqrt(len(d)))
                    result["paired"][str(k)]["edge_delta"] = {
                        "mean": round(float(d.mean()), 4),
                        "se": round(se_d, 4),
                        "t": round(float(d.mean()) / se_d, 3) if se_d else None,
                        "n_dates": len(common),
                        "pos_dates_frac": round(float((d > 0).mean()), 3),
                    }

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

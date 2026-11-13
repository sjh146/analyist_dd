#!/usr/bin/env python3
"""score_smooth_expectancy.py — 예측 점수 트레일링 평균이 '체결가능 순기대'를 올리는가 (CG266).

왜 AUC 가 아니라 돈인가
  CG265 실측: 점수 순위 lag1 상관 0.739(지속) 이지만 top-3 선택 겹침은 0.3214 —
  매일 상위 꼬리의 68%가 교체된다. 즉 AUC(그날 횡단면 순위)는 안정적인데
  실제 선택은 매일 갈아치워진다. 트레일링 평균은 그 꼬리 잡음을 줄이는 후처리다.
  판정은 세션 순기대(체결성 필터 + 수수료 왕복 0.21%p)로만 한다. AUC 는 보고만.

무엇을 하는가
  1) --preds JSONL(exp, fold, date, code, y_pred) 로드 → (date, code, score)
  2) 종목별 트레일링 N일 평균(과거만 사용, 현재일 포함) → smooth 점수
  3) market_data 일봉으로 체결 가능성(상한가·거래대금) 필터 + 청산 수익률 계산
  4) 같은 날짜·같은 필터에서 두 arm(원점수 / 스무딩)의 top-k 세션 순기대를 각각 계산
     → 세션별 짝 Δ, t, 앞/뒤 절반 부호, top-k 겹침 비교
  5) 사전 등록 문턱으로 판정: Δ ≥ +0.1%p/세션 · t ≥ 2 · Δ 앞뒤 절반 both_positive · 겹침 상승

주의(보고에 함께 쓴다)
  · 진입가 = 발굴일 종가(가정), 청산 = 다음 거래일 시가(기본). 슬리피지·호가갭 없음.
  · 스무딩은 '발행 시점에 이미 있는 과거 점수'만 쓴다 → 미래 정보 누수 없음(self-check ①).
  · 발행 계약·유니버스 변경 아님. 이 스크립트는 측정 도구다.

사용
  docker exec stock_xgboost_ml python -u /app/scripts/score_smooth_expectancy.py \
      --preds /app/scripts/_preds_CG21.jsonl --smooth-days 3 --topk 3 \
      --out /app/reports/overnight/cg266_smooth.json
  docker exec stock_xgboost_ml python -u /app/scripts/score_smooth_expectancy.py --self-check
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

FEE_BUY = 0.00015
FEE_SELL = 0.00015
TAX_SELL = 0.0018
RT_PCT = (FEE_BUY + FEE_SELL + TAX_SELL) * 100.0   # 0.21 %p
LIMIT_UP_PCT = 29.9


# ---------------------------------------------------------------- 입력
def load_preds(path: str, exp: str | None, seed: int | None = None) -> pd.DataFrame:
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if exp and str(r.get("exp", "")) != exp:
                continue
            if seed is not None and r.get("seed") is not None and int(r["seed"]) != seed:
                continue
            rows.append({"date": str(r["date"])[:10], "code": str(r["code"]).zfill(6),
                         "score": float(r["y_pred"]),
                         "y_true": None if r.get("y_true") is None else int(r["y_true"])})
    if not rows:
        raise SystemExit("preds 가 비어 있다")
    df = pd.DataFrame(rows)
    # 같은 (date, code) 중복(폴드·시드 겹침)은 평균으로 합친다.
    df = (df.groupby(["date", "code"], as_index=False)
            .agg(score=("score", "mean"),
                 y_true=("y_true", lambda s: s.dropna().iloc[0] if s.notna().any() else np.nan)))
    return df.sort_values(["code", "date"]).reset_index(drop=True)


def add_trailing(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """종목별 트레일링 n개 관측 평균(현재일 포함, 과거만)."""
    df = df.sort_values(["code", "date"]).copy()
    g = df.groupby("code", sort=False)["score"]
    df["smooth"] = g.transform(lambda s: s.rolling(n, min_periods=1).mean())
    df["n_obs_win"] = g.transform(lambda s: s.rolling(n, min_periods=1).count())
    return df


# ---------------------------------------------------------------- 가격·체결성
def db_connect():
    import psycopg2
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
        connect_timeout=10,
    )


def attach_prices(df: pd.DataFrame, conn) -> pd.DataFrame:
    codes = sorted(df["code"].unique().tolist())
    dmin = (pd.to_datetime(df["date"]).min() - pd.Timedelta(days=10)).date()
    dmax = (pd.to_datetime(df["date"]).max() + pd.Timedelta(days=10)).date()
    q = ("SELECT stock_code, trade_date, open_price, close_price, trading_value "
         "FROM market_data WHERE stock_code = ANY(%s::text[]) "
         "AND trade_date BETWEEN %s AND %s")
    px = pd.read_sql(q, conn, params=[codes, dmin, dmax])
    px["stock_code"] = px["stock_code"].astype(str).str.zfill(6)
    px = px.sort_values(["stock_code", "trade_date"])

    close_m, tv_m, dchg_m = {}, {}, {}
    nxt_o, nxt_c, t2_o = {}, {}, {}
    for code, g in px.groupby("stock_code", sort=False):
        d = list(g["trade_date"])
        o = list(g["open_price"]); cl = list(g["close_price"]); tv = list(g["trading_value"])
        for j in range(len(d)):
            close_m[(code, d[j])] = cl[j]
            tv_m[(code, d[j])] = tv[j]
            if j > 0 and cl[j - 1]:
                dchg_m[(code, d[j])] = (cl[j] / cl[j - 1] - 1.0) * 100.0
        for j in range(len(d) - 1):
            nxt_o[(code, d[j])] = o[j + 1]
            nxt_c[(code, d[j])] = cl[j + 1]
        for j in range(len(d) - 2):
            t2_o[(code, d[j])] = o[j + 2]

    dd = pd.to_datetime(df["date"]).dt.date
    out = df.copy()
    out["close"] = [close_m.get((c, d), np.nan) for c, d in zip(out["code"], dd)]
    out["trading_value"] = [tv_m.get((c, d), np.nan) for c, d in zip(out["code"], dd)]
    out["day_change_pct"] = [dchg_m.get((c, d), np.nan) for c, d in zip(out["code"], dd)]
    for name, m in (("ret_next_open_pct", nxt_o), ("ret_next_close_pct", nxt_c),
                    ("ret_t2_open_pct", t2_o)):
        out[name] = [np.nan if (cl is None or cl != cl or cl == 0) or m.get((c, d)) is None
                     else (m[(c, d)] / cl - 1.0) * 100.0
                     for c, d, cl in zip(out["code"], dd, out["close"])]
    return out


def apply_fillable(df: pd.DataFrame, a) -> tuple[pd.DataFrame, dict]:
    n0 = len(df)
    m = df["close"].notna() & (df["close"] > 0)
    if a.exclude_limit_up:
        m &= ~(df["day_change_pct"] >= a.limit_up_pct)
    if a.max_day_chg is not None:
        m &= ~(df["day_change_pct"] >= a.max_day_chg)
    if a.min_price is not None:
        m &= df["close"] >= a.min_price
    if a.min_value is not None:
        m &= df["trading_value"] >= a.min_value
    kept = df[m].copy()
    return kept, {"n_in": int(n0), "n_out": int(len(df) - len(kept)),
                  "price_join_frac": round(float(df["close"].notna().mean()), 4)}


# ---------------------------------------------------------------- 통계
def session_stats(vals: list[float]) -> dict:
    v = [x for x in vals if x is not None and not (isinstance(x, float) and math.isnan(x))]
    n = len(v)
    if n == 0:
        return {"n_sessions": 0}
    mean = sum(v) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in v) / (n - 1)) if n > 1 else 0.0
    t = mean / (sd / math.sqrt(n)) if sd > 0 else 0.0
    sv = sorted(v)
    return {"n_sessions": n, "avg_pct": round(mean, 4), "median_pct": round(sv[n // 2], 4),
            "sd_pct": round(sd, 4), "t_stat": round(t, 2),
            "pos_sessions_pct": round(100.0 * sum(1 for x in v if x > 0) / n, 1),
            "worst_session_pct": round(min(v), 4), "best_session_pct": round(max(v), 4)}


def simulate(df: pd.DataFrame, score_col: str, exit_col: str, topk: int,
             rt: float) -> dict[str, float]:
    net = df[exit_col] - rt
    work = df.assign(_net=net)
    per_date: dict[str, float] = {}
    for d, g in work.groupby("date", sort=True):
        g = g.dropna(subset=[score_col, "_net"])
        if g.empty:
            continue
        pick = g.nlargest(topk, score_col)
        per_date[str(d)] = float(pick["_net"].mean())
    return per_date


def session_pool(df: pd.DataFrame, exit_col: str, rt: float) -> dict[str, float]:
    """세션별 체결가능 후보 '풀평균' net (널 기준선 = 무작위 k 기대, CG96 방식)."""
    work = df.assign(_net=df[exit_col] - rt).dropna(subset=["_net"])
    return {str(d): float(g["_net"].mean()) for d, g in work.groupby("date")}


def excess(series: dict[str, float], pool: dict[str, float]) -> dict[str, float]:
    """세션 값 − 같은 세션 풀평균 (베타 제거 초과)."""
    return {d: v - pool[d] for d, v in series.items() if d in pool}


def split_half(series: dict[str, float], dates: list[str]) -> dict:
    vals = [series.get(d) for d in dates]
    vals = [v for v in vals if v is not None]
    n = len(vals)
    if n < 2:
        return {}
    h = n // 2
    return {"front_avg_pct": round(sum(vals[:h]) / h, 4),
            "back_avg_pct": round(sum(vals[h:]) / (n - h), 4),
            "n_front": h, "n_back": n - h}


def paired_delta(a: dict[str, float], b: dict[str, float]) -> dict:
    """b - a (스무딩 − 원점수) 세션 짝 Δ."""
    common = sorted(set(a) & set(b))
    d = [b[x] - a[x] for x in common]
    if len(d) < 2:
        return {"n_pairs": len(d)}
    mean = sum(d) / len(d)
    sd = math.sqrt(sum((x - mean) ** 2 for x in d) / (len(d) - 1))
    t = mean / (sd / math.sqrt(len(d))) if sd > 0 else 0.0
    h = len(d) // 2
    front, back = sum(d[:h]) / max(h, 1), sum(d[h:]) / max(len(d) - h, 1)
    both_pos = front > 0 and back > 0
    return {"n_pairs": len(d), "delta_mean_pct": round(mean, 4), "delta_sd_pct": round(sd, 4),
            "delta_t": round(t, 2),
            "delta_pos_frac": round(sum(1 for x in d if x > 0) / len(d), 3),
            "delta_front_pct": round(front, 4), "delta_back_pct": round(back, 4),
            "both_positive": bool(both_pos)}


def topk_overlap(df: pd.DataFrame, score_col: str, k: int) -> float:
    sel: dict[str, set] = {}
    for d, g in df.groupby("date"):
        g = g.dropna(subset=[score_col])
        if len(g):
            sel[str(d)] = set(g.nlargest(k, score_col)["code"])
    dates = sorted(sel)
    ov = [len(sel[a] & sel[b]) / float(k) for a, b in zip(dates, dates[1:]) if sel[a] and sel[b]]
    return round(float(np.mean(ov)), 4) if ov else float("nan")


def pooled_auc(df: pd.DataFrame, score_col: str) -> float | None:
    """보고용(판정 아님): 날짜별 AUC 평균."""
    aucs = []
    for _, g in df.groupby("date"):
        g = g.dropna(subset=[score_col, "y_true"])
        y = g["y_true"].to_numpy()
        if len(np.unique(y)) < 2:
            continue
        r = pd.Series(g[score_col].to_numpy()).rank().to_numpy()
        pos = y == 1
        n1, n0 = int(pos.sum()), int((~pos).sum())
        aucs.append((r[pos].sum() - n1 * (n1 + 1) / 2.0) / float(n1 * n0))
    return round(float(np.mean(aucs)), 4) if aucs else None


# ---------------------------------------------------------------- self-check
def self_check() -> int:
    ok = True

    def chk(name, cond, detail=""):
        nonlocal ok
        print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
        ok = ok and bool(cond)

    # ① 미래 누수 없음: t+1 점수를 바꿔도 t 의 smooth 는 불변
    df = pd.DataFrame({"date": [f"2026-01-0{i+1}" for i in range(5)],
                       "code": "000001", "score": [1.0, 2.0, 3.0, 4.0, 5.0]})
    s1 = add_trailing(df, 3)["smooth"].tolist()
    df2 = df.copy()
    df2.loc[4, "score"] = 999.0
    s2 = add_trailing(df2, 3)["smooth"].tolist()
    chk("① 과거만 사용(미래 점수 변경 → 과거 smooth 불변)", s1[:4] == s2[:4], f"{s1[:4]} vs {s2[:4]}")

    # ② 트레일링 평균 값
    chk("② 트레일링 3일 평균 값", s1 == [1.0, 1.5, 2.0, 3.0, 4.0], str(s1))

    # ③ top-k 겹침: 점수가 고정이면 1.0
    fixed = pd.DataFrame({"date": ["d1", "d1", "d2", "d2"], "code": ["A", "B", "A", "B"],
                          "score": [0.9, 0.1, 0.9, 0.1]})
    chk("③ top-k 겹침(동일 점수 = 1.0)", abs(topk_overlap(fixed, "score", 1) - 1.0) < 1e-9)

    # ④ 짝 Δ 부호·t
    a = {"d1": 0.0, "d2": 0.0, "d3": 0.0, "d4": 0.0}
    b = {"d1": 0.2, "d2": 0.2, "d3": 0.2, "d4": 0.2}
    pd_ = paired_delta(a, b)
    chk("④ 짝 Δ = +0.2 · both_positive", abs(pd_["delta_mean_pct"] - 0.2) < 1e-9 and pd_["both_positive"])

    # ⑤ 세션 시뮬레이션: 높은 점수만 선택하는가
    sim = pd.DataFrame({"date": ["d1"] * 3, "code": ["A", "B", "C"], "score": [0.1, 0.9, 0.5],
                        "ret": [10.0, 2.0, 5.0]})
    got = simulate(sim, "score", "ret", 1, 0.0)
    chk("⑤ top-1 선택", abs(got["d1"] - 2.0) < 1e-9, str(got))
    print("self-check:", "ALL PASS" if ok else "FAILED")
    return 0 if ok else 1


# ---------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="예측 점수 트레일링 평균 → 체결가능 순기대 (CG266)")
    ap.add_argument("--preds", help="wf_label_sweep --dump-preds JSONL")
    ap.add_argument("--exp", default=None, help="특정 exp 만(기본: 최다 행)")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--smooth-days", type=int, default=3)
    ap.add_argument("--topk", type=int, default=3)
    ap.add_argument("--exit", choices=["next_open", "next_close", "t2_open"], default="next_open")
    ap.add_argument("--exclude-limit-up", action="store_true", default=True)
    ap.add_argument("--allow-limit-up", dest="exclude_limit_up", action="store_false")
    ap.add_argument("--limit-up-pct", type=float, default=LIMIT_UP_PCT)
    ap.add_argument("--max-day-chg", type=float, default=None)
    ap.add_argument("--min-price", type=float, default=None)
    ap.add_argument("--min-value", type=float, default=3e9, help="거래대금 하한(원), 기본 30억")
    ap.add_argument("--out", default=None)
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args(argv)

    if a.self_check:
        return self_check()
    if not a.preds:
        ap.error("--preds 필요")

    exp = a.exp
    if exp is None:
        cnt: dict[str, int] = defaultdict(int)
        with open(a.preds) as f:
            for line in f:
                line = line.strip()
                if line:
                    cnt[str(json.loads(line).get("exp", ""))] += 1
        exp = max(cnt, key=cnt.get) if cnt else None
    df = load_preds(a.preds, exp, a.seed)
    df = add_trailing(df, a.smooth_days)

    conn = db_connect()
    try:
        df = attach_prices(df, conn)
    finally:
        conn.close()

    kept, filt = apply_fillable(df, a)
    exit_col = f"ret_{a.exit}_pct"
    if exit_col not in kept.columns:
        exit_col = {"next_open": "ret_next_open_pct"}.get(a.exit, exit_col)
    kept = kept[kept[exit_col].notna()]

    dates = sorted(kept["date"].unique())
    raw = simulate(kept, "score", exit_col, a.topk, RT_PCT)
    smo = simulate(kept, "smooth", exit_col, a.topk, RT_PCT)
    pool = session_pool(kept, exit_col, RT_PCT)
    st_raw, st_smo = session_stats(list(raw.values())), session_stats(list(smo.values()))
    dlt = paired_delta(raw, smo)
    sh_raw = split_half(raw, dates)
    sh_smo = split_half(smo, dates)
    ex_raw, ex_smo = excess(raw, pool), excess(smo, pool)
    null_block = {
        "pool_mean": session_stats(list(pool.values())),
        "raw_excess": session_stats(list(ex_raw.values())),
        "smooth_excess": session_stats(list(ex_smo.values())),
        "raw_minus_pool": paired_delta(pool, raw),      # top-k − 풀평균 (베타 제거 엣지)
        "smooth_minus_pool": paired_delta(pool, smo),
        "paired_delta_on_excess": paired_delta(ex_raw, ex_smo),
    }
    ov = {"raw_topk": topk_overlap(kept, "score", a.topk),
          "smooth_topk": topk_overlap(kept, "smooth", a.topk)}
    auc = {"raw": pooled_auc(kept, "score"), "smooth": pooled_auc(kept, "smooth")}

    gate = {
        "delta_ge_0.1pct": (dlt.get("delta_mean_pct") or -9e9) >= 0.1,
        "t_ge_2": (dlt.get("delta_t") or -9e9) >= 2.0,
        "both_positive": bool(dlt.get("both_positive")),
        "overlap_up": (ov["smooth_topk"] or 0) > (ov["raw_topk"] or 1),
    }
    enough = (dlt.get("n_pairs", 0) >= 30) and (st_raw.get("n_sessions", 0) >= 30)
    if not enough:
        verdict = "측정불가(표본 부족)"
    elif all(gate.values()):
        verdict = "신호(돈 경로)"
    else:
        verdict = "노이즈"

    out = {
        "id": "CG266", "exp": exp, "preds": a.preds, "rows_raw": int(len(df)),
        "rows_used": int(len(kept)), "n_dates": len(dates),
        "n_codes": int(kept["code"].nunique()),
        "date_span": [dates[0], dates[-1]] if dates else None,
        "smooth_days": a.smooth_days, "topk": a.topk, "exit": a.exit,
        "round_trip_pct": round(RT_PCT, 4), "fillable_filter": filt,
        "filters": {"exclude_limit_up": a.exclude_limit_up, "limit_up_pct": a.limit_up_pct,
                    "max_day_chg": a.max_day_chg, "min_price": a.min_price,
                    "min_value": a.min_value},
        "window_full_frac": round(float((kept["n_obs_win"] >= a.smooth_days).mean()), 4),
        "raw": st_raw, "smooth": st_smo, "paired_delta": dlt,
        "null_baseline": null_block,
        "split_half": {"raw": sh_raw, "smooth": sh_smo},
        "topk_overlap": ov, "auc_report_only": auc,
        "gate": gate, "verdict": verdict,
    }
    txt = json.dumps(out, ensure_ascii=False, indent=1)
    print(txt)
    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        with open(a.out, "w") as f:
            f.write(txt + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

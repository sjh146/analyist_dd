#!/usr/bin/env python3
"""factor_money_screen.py — 고전 팩터(가치·퀄리티·모멘텀·저변동·멀티팩터)의
**랭킹 정보**를 패널 유니버스·패널 기간에서 분 단위로 채점한다 (읽기 전용, DB 조회만).

왜 (2026-10-05):
  모델 측 축(AUC·변환·HP·목적함수·가중·선별·라벨)과 '모델 랭킹의 돈 방향 정보' 축이 모두
  실측으로 닫혔다(CG114: 하위 꼬리 반전도 유니버스 시드 7 에서 부호 반전 → 노이즈).
  남은 질문은 하나다 — **정보가 데이터에 없는가, 아니면 모델이 못 뽑는가**.
  고전 팩터는 그 대조군이다: 같은 유니버스·같은 기간·같은 라벨(h5 선행수익)에서 팩터 랭킹의
  횡단면 IC 와 분위 스프레드를 재면, 모델의 IC(CG113: 배포 챔피언 +0.0165 t0.94 · q0.05 +0.0560 t4.53)
  와 직접 비교된다.

주의(보고 규율):
  - 여기서 나오는 값은 **IC·분위 스프레드(정보)이며 순기대(%p/세션)가 아니다.** 체결성 필터·
    수수료를 적용하지 않았다(가격만 있고 거래대금·상한가 플래그가 패널에 없다) → **승격 근거 아님**.
    순기대가 필요하면 덤프 기반 `fillable_topk_expectancy.py` / `rank_ic_money.py` 를 쓴다.
  - 팩터는 **경제적 부호를 미리 고정**한다(가치=낮은 배수, 퀄리티=높은 ROE/ROA/F, 모멘텀=과거 상승,
    저변동=낮은 실현변동). 부호를 데이터를 보고 뒤집지 않는다(사후부호 = p-해킹).
  - as-of: 재무는 `financial_ratio_features` 의 (stock_code, trade_date, rcept_dt) 중
    rcept_dt <= trade_date 행만 쓴다(실측 위반 0). 모멘텀·저변동은 패널 가격의 과거만 쓴다.
"""
import argparse
import json
import math
import os
import statistics
import sys

import numpy as np
import pandas as pd
import psycopg2

FIN_COLS = ["value_per", "value_pbr", "value_psr", "quality_roe", "quality_roa", "quality_f_score"]
# 경제적 부호: +1 = 값이 클수록 매수 우위, -1 = 값이 작을수록 우위
SIGN = {
    "value_score": {"value_per": -1, "value_pbr": -1, "value_psr": -1},
    "quality_score": {"quality_roe": +1, "quality_roa": +1, "quality_f_score": +1},
}


def load_panel(path):
    z = np.load(path, allow_pickle=True)
    df = pd.DataFrame({"code": [str(c) for c in z["codes"]],
                       "date": pd.to_datetime(z["dates"]),
                       "px": np.asarray(z["price"], dtype=float)})
    df = df.sort_values(["code", "date"]).reset_index(drop=True)
    return df


def add_price_factors(df, horizon):
    g = df.groupby("code")["px"]
    df["fwd"] = g.shift(-horizon) / df["px"] - 1.0            # 라벨(앞으로 h일)
    df["mom_60_5"] = g.shift(5) / g.shift(60) - 1.0           # 과거 60→5일 수익(과거만)
    ret = df.groupby("code")["px"].pct_change()
    df["ret"] = ret
    df["lowvol_raw"] = -df.groupby("code")["ret"].transform(
        lambda s: s.rolling(20, min_periods=15).std())        # 과거 20일 변동성(과거만)
    return df


def load_fin(codes, dmin, dmax):
    conn = psycopg2.connect(host=os.environ.get("POSTGRES_HOST", "postgres"),
                            port=int(os.environ.get("POSTGRES_PORT", "5432")),
                            user=os.environ["POSTGRES_USER"],
                            password=os.environ["POSTGRES_PASSWORD"],
                            dbname=os.environ["POSTGRES_DB"])
    cur = conn.cursor()
    cur.execute(
        f"select stock_code, trade_date, rcept_dt, {', '.join(FIN_COLS)} "
        f"from financial_ratio_features "
        f"where stock_code = any(%s) and trade_date between %s and %s",
        (list(set(codes)), dmin, dmax))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    f = pd.DataFrame(rows, columns=["code", "date", "rcept_dt"] + FIN_COLS)
    f["code"] = f["code"].astype(str)
    f["date"] = pd.to_datetime(f["date"])
    f["rcept_dt"] = pd.to_datetime(f["rcept_dt"], errors="coerce")
    # as-of: 미래 접수분 제거 후 (code,date) 1행
    bad = int((f["rcept_dt"] > f["date"]).sum())
    f = f[f["rcept_dt"].isna() | (f["rcept_dt"] <= f["date"])]
    f = f.sort_values(["code", "date", "rcept_dt"]).drop_duplicates(["code", "date"], keep="last")
    return f, bad


def xs_z(df, cols):
    """날짜별 횡단면 z (1/99% winsor 후)."""
    out = {}
    for c in cols:
        def _f(s):
            v = pd.to_numeric(s, errors="coerce")
            m = v.notna()
            if m.sum() < 10:
                return pd.Series(np.nan, index=s.index)
            lo, hi = v[m].quantile(0.01), v[m].quantile(0.99)
            v = v.clip(lo, hi)
            sd = v[m].std(ddof=0)
            if not sd or not np.isfinite(sd):
                return pd.Series(np.nan, index=s.index)
            return (v - v[m].mean()) / sd
        out[c] = df.groupby("date")[c].transform(_f)
    return out


def load_market(codes, dmin, dmax):
    """체결성 필터용 market_data 조인(거래대금·종가)."""
    conn = psycopg2.connect(host=os.environ.get("POSTGRES_HOST", "postgres"),
                            port=int(os.environ.get("POSTGRES_PORT", "5432")),
                            user=os.environ["POSTGRES_USER"],
                            password=os.environ["POSTGRES_PASSWORD"],
                            dbname=os.environ["POSTGRES_DB"])
    cur = conn.cursor()
    cur.execute(
        "select stock_code, trade_date, trading_value, close_price from market_data "
        "where stock_code = any(%s) and trade_date between %s and %s",
        (list(set(codes)), dmin, dmax))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    f = pd.DataFrame(rows, columns=["code", "date", "trading_value", "mkt_close"])
    f["code"] = f["code"].astype(str)
    f["date"] = pd.to_datetime(f["date"])
    return f.drop_duplicates(["code", "date"], keep="last")


# 체결성 필터 상수 — scripts/fillable_expectancy.py 와 같은 값(수수료는 Δ 에서 상쇄되므로 미적용)
MAX_DAY_CHG = 25.0     # 당일등락 % 상한(이 이상 오른 뒤 종가매수 = 체결 불가/추격)
MIN_VALUE = 1e9        # 거래대금 하한(원) = 10억
MIN_PRICE = 1000.0     # 종가 하한(원)


def topk_vs_pool(df, col, ks=(3, 5, 10, 20), side="top"):
    """세션별 top-k(=bottom-k) 바스켓 평균 − 세션 풀평균 (Δ%p/세션, 널 기준선 = 풀평균).

    수수료 왕복 0.21%p 는 양쪽에 동일하게 붙으므로 **차이에서 상쇄**된다 → 미적용.
    """
    res = {str(k): [] for k in ks}
    pools = []
    for _, g in df.groupby("date"):
        g = g.dropna(subset=[col, "fwd"])
        if len(g) < 20 or g[col].nunique() < 5:
            continue
        pool = float(g["fwd"].mean())
        pools.append(pool * 100.0)
        for k in ks:
            if len(g) < k:
                continue
            sel = g.nlargest(k, col) if side == "top" else g.nsmallest(k, col)
            res[str(k)].append((float(sel["fwd"].mean()) - pool) * 100.0)

    def stat(a):
        if len(a) < 3:
            return None
        a = np.asarray(a, dtype=float)
        sd = a.std(ddof=1)
        return {"n_sessions": int(len(a)), "mean_delta_pct": round(float(a.mean()), 4),
                "sd": round(float(sd), 4),
                "t": (round(float(a.mean() / (sd / np.sqrt(len(a)))), 2) if sd > 0 else None),
                "pos_share": round(float((a > 0).mean()), 3),
                "first_half": round(float(a[:len(a) // 2].mean()), 4),
                "second_half": round(float(a[len(a) // 2:].mean()), 4)}

    return {"k": {k: stat(v) for k, v in res.items()}, "pool_mean_pct": stat(pools)}


def ic_and_spread(df, col, horizon):
    d = df[["date", col, "fwd"]].dropna()
    ics, spreads, top_rets, bot_rets = [], [], [], []
    for _, g in d.groupby("date"):
        if len(g) < 20 or g[col].nunique() < 5:
            continue
        ics.append(float(g[col].corr(g["fwd"], method="spearman")))
        n = len(g)
        k = max(3, n // 10)
        s = g.sort_values(col)
        bot = s["fwd"].iloc[:k].mean()
        top = s["fwd"].iloc[-k:].mean()
        top_rets.append(top * 100.0)
        bot_rets.append(bot * 100.0)
        spreads.append((top - bot) * 100.0)   # %p/세션 (h일 보유)
    def stat(a):
        if len(a) < 3:
            return None
        a = np.asarray(a, dtype=float)
        sd = a.std(ddof=1)
        return {"n": int(len(a)), "mean": round(float(a.mean()), 4),
                "sd": round(float(sd), 4),
                "t": (round(float(a.mean() / (sd / np.sqrt(len(a)))), 2) if sd > 0 else None),
                "pos_share": round(float((a > 0).mean()), 3),
                "first_half": round(float(a[:len(a) // 2].mean()), 4),
                "second_half": round(float(a[len(a) // 2:].mean()), 4)}
    return {"ic": stat(ics), "decile_spread_pct": stat(spreads),
            "top_decile_mean_pct": stat(top_rets), "bottom_decile_mean_pct": stat(bot_rets)}


def add_factor_scores(df):
    """횡단면 z + 경제적 부호 고정 합성 점수를 붙인다(팩터 정의 단일 진실원).

    df 는 FIN_COLS·mom_60_5·lowvol_raw 컬럼을 이미 가져야 한다. 패널 경로(main)와
    덤프 짝 경로(run_paired_block)가 **같은 함수**를 쓰게 해 정의 이탈을 막는다.
    """
    zz = xs_z(df, FIN_COLS + ["mom_60_5", "lowvol_raw"])
    for c in FIN_COLS:
        df["z_" + c] = zz[c]
    df["z_mom_60_5"] = zz["mom_60_5"]
    df["z_lowvol_raw"] = zz["lowvol_raw"]

    def combo(names, signs):
        acc, cnt = None, None
        for n, sg in zip(names, signs):
            v = sg * df["z_" + n]
            ok = v.notna()
            acc = v if acc is None else acc.add(v.where(ok), fill_value=np.nan)
            cnt = ok.astype(float) if cnt is None else cnt + ok.astype(float)
        return (acc / cnt).where(cnt > 0)

    df["value_score"] = combo(list(SIGN["value_score"]), list(SIGN["value_score"].values()))
    df["quality_score"] = combo(list(SIGN["quality_score"]), list(SIGN["quality_score"].values()))
    df["momentum_score"] = df["z_mom_60_5"]
    df["lowvol_score"] = df["z_lowvol_raw"]
    parts = ["value_score", "quality_score", "momentum_score", "lowvol_score"]
    df["multifactor"] = df[parts].mean(axis=1, skipna=True)
    return df, parts


# ── 덤프 짝 경로 (CG117): 모델 IC vs 팩터 IC 를 **같은 (code,date) 행**에서 ──────
def load_dump_rows(path):
    """champion_robust_eval --dump-all jsonl → (code,date,y_pred,fwd_ret) DataFrame.

    같은 (code,date) 안에서 모델·팩터를 채점하려면 **행 도메인이 하나**여야 한다
    (교차패널 비교 금지 — 실측 U1/CG5: 코드 교집합 3종목에서 부호가 뒤집혔다).
    """
    rows = []
    with open(path, encoding="utf-8") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            try:
                r = json.loads(ln)
            except json.JSONDecodeError:
                continue
            c = r.get("code") or r.get("stock_code")
            d = r.get("date") or r.get("prediction_date")
            yp, fr = r.get("y_pred"), r.get("fwd_ret")
            if c is None or d is None or yp is None or fr is None:
                continue
            rows.append((str(c), str(d)[:10], float(yp), float(fr)))
    return pd.DataFrame(rows, columns=["code", "date", "y_pred", "fwd_ret"])


def _agg_ic(ics):
    a = np.asarray([v for v in ics.values() if v is not None], dtype=float)
    if len(a) < 2:
        return None
    sd = a.std(ddof=1)
    return {"n": int(len(a)), "mean": round(float(a.mean()), 4), "sd": round(float(sd), 4),
            "t": (round(float(a.mean() / (sd / np.sqrt(len(a)))), 2) if sd > 1e-9 else None),
            "pos_share": round(float((a > 0).mean()), 3),
            "first_half": round(float(a[:len(a) // 2].mean()), 4),
            "second_half": round(float(a[len(a) // 2:].mean()), 4)}


def _sign_test_p(k_pos, n):
    """부호검정 양측 정확 p (동점 제외)."""
    if n <= 0:
        return None
    from math import comb
    tail = sum(comb(n, i) for i in range(0, min(k_pos, n - k_pos) + 1)) / (2.0 ** n)
    return round(min(1.0, 2.0 * tail), 4)


def paired_ic_stats(model_ic, factor_ic):
    """공통 세션의 짝 ΔIC = IC(model) − IC(factor) 통계 (동점 별도 계수)."""
    common = sorted(set(model_ic) & set(factor_ic))
    pairs = [(model_ic[d], factor_ic[d]) for d in common
             if model_ic[d] is not None and factor_ic[d] is not None]
    if len(pairs) < 2:
        return {"n_sessions": len(pairs), "error": "공통 세션 부족(>=2 필요)"}
    diffs = [a - b for a, b in pairs]
    m = statistics.mean(diffs)
    sd = statistics.stdev(diffs)
    # 제로/극소 분산(모든 세션 Δ 동일) 가드: 부동소수점 잔차로 t 가 1e16 처럼 튀는 것을 막는다
    se = sd / math.sqrt(len(diffs)) if sd > 1e-9 else None
    nz = [x for x in diffs if x != 0]
    k_pos = sum(1 for x in nz if x > 0)
    return {"n_sessions": len(diffs), "n_tied": len(diffs) - len(nz),
            "mean_delta_ic": round(m, 4), "sd": round(sd, 4),
            "t": (round(m / se, 2) if se else None),
            "pos_session_share": round(sum(1 for x in diffs if x > 0) / len(diffs), 3),
            "sign_pos": k_pos, "sign_n": len(nz), "sign_p": _sign_test_p(k_pos, len(nz)),
            "first_half_delta": round(statistics.mean(diffs[:len(diffs) // 2]), 4),
            "second_half_delta": round(statistics.mean(diffs[len(diffs) // 2:]), 4)}


def run_paired_block(a):
    """덤프 유니버스에서 모델 예측 vs 멀티팩터의 세션별 IC 를 **짝**으로 채점한다."""
    dump = load_dump_rows(a.arm_jsonl)
    if dump.empty:
        return {"error": "덤프 비어 있음(y_pred/fwd_ret 있는 행 0)"}
    # 덤프 date 는 문자열, DB 쪽은 datetime64 → 병합 키 타입을 맞춘다
    dump["date"] = pd.to_datetime(dump["date"])
    codes = sorted(dump["code"].unique())
    dmin = dump["date"].min().date().isoformat()
    dmax = dump["date"].max().date().isoformat()
    look = (pd.Timestamp(dmin) - pd.Timedelta(days=130)).date().isoformat()   # 60거래일 여유
    mk = load_market(codes, look, dmax)
    if mk.empty:
        return {"error": "market_data 조회 결과 없음"}
    px = mk.rename(columns={"mkt_close": "px"}).sort_values(["code", "date"]).reset_index(drop=True)
    # market_data 는 numeric 을 Decimal 로 돌려준다 → 팩터 산술 전에 float 로 강제한다
    # (Decimal − float 는 TypeError. 기존 --fillable 경로는 비교·pct_change 만 해서 무사했다.)
    px["px"] = pd.to_numeric(px["px"], errors="coerce").astype(float)
    px["trading_value"] = pd.to_numeric(px["trading_value"], errors="coerce")
    px = add_price_factors(px, a.horizon)
    px["day_chg"] = px.groupby("code")["px"].pct_change()

    fin, asof_bad = load_fin(codes, dmin, dmax)
    d = dump.merge(px[["code", "date", "trading_value", "px", "day_chg",
                       "mom_60_5", "lowvol_raw"]], on=["code", "date"], how="left")
    d = d.merge(fin[["code", "date"] + FIN_COLS], on=["code", "date"], how="left")
    n_dump, n_matched = len(dump), int(d["px"].notna().sum())

    d, parts = add_factor_scores(d)

    filt = None
    keep = d
    if a.fillable:
        m = (d["day_chg"].notna() & (d["day_chg"] < MAX_DAY_CHG / 100.0)
             & (d["trading_value"] >= MIN_VALUE) & (d["px"] >= MIN_PRICE))
        keep = d[m].copy()
        filt = {"max_day_chg_pct": MAX_DAY_CHG, "min_value": MIN_VALUE, "min_price": MIN_PRICE,
                "rows_total": int(len(d)), "rows_kept": int(len(keep)),
                "kept_share": round(float(len(keep) / max(len(d), 1)), 4)}

    md = keep.dropna(subset=["y_pred", "fwd_ret", "multifactor"])
    mic, fic = {}, {}
    min_n = max(3, int(a.paired_min_n))
    for dt, g in md.groupby("date"):
        if len(g) < min_n or g["multifactor"].nunique() < 5 or g["y_pred"].nunique() < 5:
            continue
        mic[dt] = float(g["y_pred"].corr(g["fwd_ret"], method="spearman"))
        fic[dt] = float(g["multifactor"].corr(g["fwd_ret"], method="spearman"))

    panel_overlap = None
    try:
        pz = np.load(a.panel, allow_pickle=True)
        pcs = {str(c) for c in pz["codes"]}
        panel_overlap = len(pcs & set(codes))
    except (OSError, KeyError, ValueError):
        pass

    stats = paired_ic_stats(mic, fic)
    min_d = float(getattr(a, "min_delta_ic", None) or 0.01)
    min_t = float(a.min_t)
    ok = (stats.get("t") is not None and stats.get("mean_delta_ic") is not None
          and stats["mean_delta_ic"] >= min_d and stats["t"] >= min_t)
    verdict = "모델 우위 있음" if ok else "모델 우위 없음"
    return {
        "arm": a.arm_tag or os.path.basename(a.arm_jsonl), "arm_jsonl": a.arm_jsonl,
        "fillable": bool(a.fillable), "filter": filt,
        "factor_coverage": {c: round(float(d[c].notna().mean()), 4) for c in parts + ["multifactor"]},
        "universe": {"dump_codes": len(codes), "matched_codes": int(d.loc[d['px'].notna(), 'code'].nunique()),
                     "panel_code_overlap": panel_overlap, "dates": int(dump['date'].nunique())},
        "rows": {"dump": n_dump, "matched": n_matched, "scored": int(len(md)),
                 "sessions_used": stats.get("n_sessions")},
        "asof": {"financial_ratio_features_rcept_gt_date": asof_bad},
        "ic_model": _agg_ic(mic), "ic_factor": _agg_ic(fic), "paired": stats,
        "prereg": ("ΔIC = IC(모델) − IC(멀티팩터) ≥ +%.2f AND t ≥ %.1f → '모델 우위 있음'"
                   % (min_d, min_t)),
        "verdict": verdict,
        "note": ("같은 (code,date) 행·같은 실현수익(덤프 fwd_ret) 위의 세션별 Spearman IC 짝 비교. "
                 "IC 는 순기대가 아니다(승격 근거 아님). 팩터 점수는 덤프 유니버스 자체에서 계산한다"
                 "(교차패널 비교 아님) — panel_code_overlap 은 참고값."),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default="app/models/wf/panel_prod200.npz")
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--out", default="reports/overnight/factor_money_screen.json")
    ap.add_argument("--fillable", action="store_true",
                    help="체결성 필터(당일등락<25%%·거래대금>=10억·가격>=1000원) 적용 후 "
                         "top-k(3·5·10·20) vs 세션 풀평균(널 기준선) Δ 를 함께 낸다")
    ap.add_argument("--arm-jsonl",
                    help="모델 예측 덤프(champion_robust_eval --dump-all jsonl). 주면 같은 "
                         "(code,date) 행에서 모델 IC vs 팩터 IC 를 **짝**으로 채점한다(CG117)")
    ap.add_argument("--arm-tag", help="덤프 라벨(기본 파일명)")
    ap.add_argument("--paired-min-n", type=int, default=20,
                    help="세션별 IC 를 계산할 최소 종목 수(기본 20)")
    ap.add_argument("--min-delta-ic", type=float, default=0.01,
                    help="paired 판정 ΔIC 문턱(기본 +0.01)")
    ap.add_argument("--min-t", type=float, default=2.0, help="paired 판정 t 문턱(기본 2.0)")
    a = ap.parse_args()

    df = load_panel(a.panel)
    df = add_price_factors(df, a.horizon)
    fin, asof_bad = load_fin(df["code"].unique(), df["date"].min().date(), df["date"].max().date())
    df = df.merge(fin, on=["code", "date"], how="left")

    df, parts = add_factor_scores(df)

    out = {"panel": {"file": os.path.basename(a.panel),
                     "rows": int(len(df)), "codes": int(df["code"].nunique()),
                     "from": str(df["date"].min().date()), "to": str(df["date"].max().date()),
                     "horizon": a.horizon},
           "asof": {"financial_ratio_features_rcept_gt_date": asof_bad},
           "note": ("IC·분위 스프레드(정보)만 — 체결성 필터·수수료 미적용, 순기대 아님(승격 근거 아님). "
                    "팩터 부호는 사전 고정(가치=낮은 배수, 퀄리티=높은 ROE/ROA/F, 모멘텀=과거60→5, 저변동=낮은 변동)."),
           "factors": {}}
    for c in parts + ["multifactor"]:
        out["factors"][c] = {"coverage": round(float(df[c].notna().mean()), 4),
                             **ic_and_spread(df, c, a.horizon)}

    if a.fillable:
        mk = load_market(df["code"].unique(), df["date"].min().date(), df["date"].max().date())
        df = df.merge(mk, on=["code", "date"], how="left")
        df["day_chg"] = df.groupby("code")["px"].pct_change()
        keep = ((df["day_chg"] < MAX_DAY_CHG / 100.0) & (df["trading_value"] >= MIN_VALUE)
                & (df["px"] >= MIN_PRICE) & df["day_chg"].notna())
        fdf = df[keep].copy()
        out["fillable"] = {
            "filter": {"max_day_chg_pct": MAX_DAY_CHG, "min_value": MIN_VALUE,
                       "min_price": MIN_PRICE,
                       "rows_total": int(len(df)), "rows_kept": int(len(fdf)),
                       "kept_share": round(float(len(fdf) / max(len(df), 1)), 4)},
            "note": ("널 기준선 = 세션 풀평균(무작위 k 기대) · 수수료 왕복 0.21%p 는 Δ 에서 상쇄 · "
                     "k 는 절대 개수 · 승격 판정은 사전등록 t≥2 & Δ≥+0.1%p (CG116)"),
            "factors": {}}
        for c in parts + ["multifactor"]:
            tw = topk_vs_pool(fdf, c, ks=(3, 5, 10, 20), side="top")
            bw = topk_vs_pool(fdf, c, ks=(3, 5, 10, 20), side="bottom")
            out["fillable"]["factors"][c] = {"top": tw, "bottom": bw}

    if a.arm_jsonl:
        out["paired_vs_factor"] = run_paired_block(a)

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)

    print(f"panel {out['panel']['file']} · {out['panel']['rows']}행 · {out['panel']['codes']}종목 · "
          f"{out['panel']['from']}~{out['panel']['to']} · h{a.horizon} · as-of 위반 {asof_bad}")
    print(f"{'factor':16s} {'cov':>6s} {'IC':>8s} {'t':>6s} {'pos':>5s} {'spread%p':>10s} {'t':>6s} {'n':>4s}")
    for c, v in out["factors"].items():
        ic, sp = v["ic"], v["decile_spread_pct"]
        if not ic:
            print(f"{c:16s} {v['coverage']:>6.3f}   (측정 불가)")
            continue
        print(f"{c:16s} {v['coverage']:>6.3f} {ic['mean']:>8.4f} {str(ic['t']):>6s} "
              f"{ic['pos_share']:>5.2f} {sp['mean']:>10.4f} {str(sp['t']):>6s} {sp['n']:>4d}")
    print(f"→ {a.out}")
    if a.fillable:
        fl = out["fillable"]
        print(f"체결성 필터: {fl['filter']['rows_kept']}/{fl['filter']['rows_total']} 행 유지 "
              f"({fl['filter']['kept_share']*100:.1f}%) · 널=세션 풀평균")
        print(f"{'factor':16s} {'k':>3s} {'Δtop vs 풀':>11s} {'t':>6s} {'Δbottom':>9s} {'t':>6s}")
        for c, v in fl["factors"].items():
            for k in ("3", "5", "10", "20"):
                tw, bw = v["top"]["k"].get(k), v["bottom"]["k"].get(k)
                if not tw:
                    continue
                print(f"{c:16s} {k:>3s} {tw['mean_delta_pct']:>11.4f} {str(tw['t']):>6s} "
                      f"{(bw or {}).get('mean_delta_pct', float('nan')):>9.4f} {str((bw or {}).get('t')):>6s}")
    pv = out.get("paired_vs_factor")
    if pv:
        if pv.get("error"):
            print(f"[paired] 측정 불가: {pv['error']}")
        else:
            u, r, pa = pv["universe"], pv["rows"], pv["paired"]
            im, iff = pv["ic_model"] or {}, pv["ic_factor"] or {}
            print(f"[paired] 덤프 {pv['arm']} · 종목 {u['dump_codes']}(매칭 {u['matched_codes']}, "
                  f"panel 교집합 {u['panel_code_overlap']}) · 세션 {u['dates']} · 행 "
                  f"{r['dump']}→채점 {r['scored']}"
                  + (f" · 필터 유지 {pv['filter']['rows_kept']}/{pv['filter']['rows_total']}"
                     if pv.get("filter") else ""))
            print(f"[paired] 모델 IC {im.get('mean')}(t {im.get('t')}) vs 팩터 IC "
                  f"{iff.get('mean')}(t {iff.get('t')}) → ΔIC {pa.get('mean_delta_ic')}"
                  f"(t {pa.get('t')}, 세션 {pa.get('n_sessions')}, 양(+) "
                  f"{pa.get('pos_session_share')}, 동점 {pa.get('n_tied')}, 부호검정 p "
                  f"{pa.get('sign_p')}) = {pv['verdict']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

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
import os
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default="app/models/wf/panel_prod200.npz")
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--out", default="reports/overnight/factor_money_screen.json")
    ap.add_argument("--fillable", action="store_true",
                    help="체결성 필터(당일등락<25%%·거래대금>=10억·가격>=1000원) 적용 후 "
                         "top-k(3·5·10·20) vs 세션 풀평균(널 기준선) Δ 를 함께 낸다")
    a = ap.parse_args()

    df = load_panel(a.panel)
    df = add_price_factors(df, a.horizon)
    fin, asof_bad = load_fin(df["code"].unique(), df["date"].min().date(), df["date"].max().date())
    df = df.merge(fin, on=["code", "date"], how="left")

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
    return 0


if __name__ == "__main__":
    sys.exit(main())

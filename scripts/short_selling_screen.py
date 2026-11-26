#!/usr/bin/env python3
"""공매도(krx_short_selling) 원천의 '정보량' 사전 스크린 — 패널 재빌드 전 go/no-go.

왜: L3 실측에서 패널의 short_selling_ratio 는 비영 0.24%(사실상 죽은 피처)였고,
원천 테이블에는 2026-06 이후 일평균 ~2,600행이 채워져 있다(11-18 이후 붕괴는 KRX-02).
패널 재빌드(수백 분) + 리서처 백필 승인을 요청하기 전에, 그 데이터가 애초에
횡단면 신호를 담고 있는지 10분짜리 읽기 전용 측정으로 먼저 판정한다.

프로토콜(사전 등록):
- 유니버스: panel_prod200_v6o.npz 의 codes
- as-of: 공매도 통계는 익일 공표 → 신호일 t-1 값만 사용(1거래일 지연)
- 라벨: t 종가 → t+5 종가 수익률(시장 베타 제거 위해 날짜내 순위로 채점)
- 지표: 날짜별 Spearman IC(표본 ≥30종목), 비중첩(stride 5) 평균·t·앞뒤 절반 부호
- 판정(사전 등록, 변경 금지):
    |IC| ≥ 0.03 AND |t| ≥ 2 AND n_dates ≥ 40  → '정보있음' — 패널 편입 실험(CG464 후속) 승격
    그 외 → '노이즈' — 공매도 축은 데이터 축 후보에서 제외(백필 승인 요청하지 않는다)
- 수수료: 십분위 스프레드에 왕복 0.21%p 차감(참고용, 판정은 IC)
"""
import json
import os
import sys

import numpy as np
import pandas as pd
import psycopg2

PANEL = os.environ.get("PANEL", "/app/app/models/wf/panel_prod200_v6o.npz")
OUT = os.environ.get("OUT", "/app/reports/overnight/short_selling_screen.json")
FEE = 0.21


def connect():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        user=os.environ.get("POSTGRES_USER"),
        password=os.environ.get("POSTGRES_PASSWORD"),
        dbname=os.environ.get("POSTGRES_DB"),
    )


def load_panel():
    d = np.load(PANEL, allow_pickle=True)
    codes = sorted({str(c) for c in d["codes"]})
    dates = sorted({str(x)[:10] for x in d["dates"]})
    return codes, dates, d["feature_names"].tolist()


def spearman(x, y):
    if len(x) < 3:
        return np.nan
    rx = pd.Series(x).rank().to_numpy()
    ry = pd.Series(y).rank().to_numpy()
    rx = rx - rx.mean()
    ry = ry - ry.mean()
    den = np.sqrt((rx ** 2).sum() * (ry ** 2).sum())
    return float((rx * ry).sum() / den) if den else np.nan


def main():
    codes, panel_dates, feats = load_panel()
    print(f"패널: {os.path.basename(PANEL)} · {len(codes)}종목 · {panel_dates[0]}~{panel_dates[-1]} · 피처 {len(feats)}")
    con = connect()
    lo = (pd.Timestamp(panel_dates[0]) - pd.Timedelta(days=15)).date()
    hi = (pd.Timestamp(panel_dates[-1]) + pd.Timedelta(days=15)).date()
    px = pd.read_sql(
        "select stock_code, trade_date, close_price, trading_value from stock_prices "
        "where trade_date between %(lo)s and %(hi)s and stock_code = any(%(codes)s)",
        con, params={"lo": lo, "hi": hi, "codes": codes},
    )
    sh = pd.read_sql(
        "select stock_code, trade_date, short_volume, short_value, short_ratio, balance_quantity, total_volume "
        "from krx_short_selling where trade_date between %(lo)s and %(hi)s and stock_code = any(%(codes)s)",
        con, params={"lo": lo, "hi": hi, "codes": codes},
    )
    con.close()
    px["trade_date"] = pd.to_datetime(px["trade_date"])
    sh["trade_date"] = pd.to_datetime(sh["trade_date"])
    px = px.dropna(subset=["close_price"]).sort_values(["stock_code", "trade_date"])
    print(f"가격행 {len(px):,} · 공매도행 {len(sh):,} · 공매도 날짜 {sh.trade_date.nunique()}")

    px["fwd5"] = px.groupby("stock_code")["close_price"].shift(-5) / px["close_price"] - 1.0
    sh["sig_ratio"] = sh["short_ratio"].astype(float)
    sh["sig_bal"] = sh["balance_quantity"].astype(float) / sh["total_volume"].replace(0, np.nan).astype(float)

    dates = np.array(sorted(px["trade_date"].unique()))
    nxt_day = {dates[i]: dates[i + 1] for i in range(len(dates) - 1)}
    sh["asof_date"] = sh["trade_date"].map(nxt_day)
    sh = sh.dropna(subset=["asof_date"])

    px_idx = px.rename(columns={"trade_date": "asof_date"}).set_index(["asof_date", "stock_code"])
    sigs = ["sig_ratio", "sig_bal"]
    rows = {s: [] for s in sigs}
    spread = {s: [] for s in sigs}
    for s in sigs:
        m = sh[["asof_date", "stock_code", s]].dropna()
        m = m.join(px_idx[["fwd5"]], on=["asof_date", "stock_code"], how="inner").reset_index(drop=True).dropna()
        for d, g in m.groupby("asof_date"):
            if len(g) < 30:
                continue
            ic = spearman(g[s].to_numpy(), g["fwd5"].to_numpy())
            if np.isnan(ic):
                continue
            rows[s].append((str(pd.Timestamp(d).date()), ic, len(g)))
            if g[s].nunique() >= 10:
                hi_q = g[g[s] >= g[s].quantile(0.8)]["fwd5"].mean()
                lo_q = g[g[s] <= g[s].quantile(0.2)]["fwd5"].mean()
                spread[s].append((100 * (hi_q - lo_q), 100 * (lo_q - hi_q)))

    def summarize(series, stride):
        v = np.array([r[1] for r in series])
        if len(v) < 3:
            return None
        sub = v[::stride]
        n = len(sub)
        mu, sd = float(sub.mean()), float(sub.std(ddof=1))
        t = mu / (sd / np.sqrt(n)) if sd else float("nan")
        mu_ov, sd_ov = float(v.mean()), float(v.std(ddof=1))
        t_ov = mu_ov / (sd_ov / np.sqrt(len(v))) if sd_ov else float("nan")
        half = len(v) // 2
        h1, h2 = float(v[:half].mean()), float(v[half:].mean())
        return {
            "n_dates": int(len(v)),
            "n_dates_nonoverlap": int(n),
            "mean_ic": round(mu, 4),
            "sd": round(sd, 4),
            "t_nonoverlap": round(t, 2),
            "t_overlap": round(t_ov, 2),
            "mean_ic_overlap": round(mu_ov, 4),
            "half1": round(h1, 4),
            "half2": round(h2, 4),
            "sign_agree": bool(np.sign(h1) == np.sign(h2)),
            "pos_rate": round(float((v > 0).mean()), 3),
            "min": round(float(v.min()), 4),
            "max": round(float(v.max()), 4),
        }

    out = {"panel": os.path.basename(PANEL), "asof_lag_days": 1, "horizon": 5,
           "universe": len(codes), "fee_roundtrip_pct": FEE, "signals": {}}
    for s in sigs:
        st = summarize(rows[s], 1)
        s5 = summarize(rows[s], 5)
        sp = np.array([x[1] for x in spread[s]]) if spread[s] else np.zeros(0)
        sp5 = sp[::5] if len(sp) >= 5 else sp
        out["signals"][s] = {
            "daily": st, "nonoverlap": s5,
            "spread_bottom_minus_top_pct": (round(float(sp5.mean()) - FEE, 3) if len(sp5) else None),
            "spread_t": (round(float(sp5.mean()) / (sp5.std(ddof=1) / np.sqrt(len(sp5))), 2) if len(sp5) > 2 and sp5.std(ddof=1) else None),
            "pre_reg_threshold": "|IC|>=0.03 & |t|>=2 & n_dates>=40",
        }
        verdict = "정보있음" if (s5 and st and abs(st["mean_ic"]) >= 0.03 and abs(s5["t_nonoverlap"]) >= 2 and st["n_dates"] >= 40) else "노이즈"
        out["signals"][s]["verdict"] = verdict
        print(f"{s}: daily meanIC {st and st['mean_ic']} (t(overlap) {st and st['t_nonoverlap']}) · "
              f"비중첩 n={s5 and s5['n_dates_nonoverlap']} meanIC {s5 and s5['mean_ic']} t {s5 and s5['t_nonoverlap']} "
              f"· halves {s5 and (s5['half1'], s5['half2'])} · 스프레드 {out['signals'][s]['spread_bottom_minus_top_pct']}%p "
              f"→ {verdict}")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("저장:", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())

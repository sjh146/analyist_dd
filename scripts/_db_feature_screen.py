#!/usr/bin/env python3
"""_db_feature_screen.py — DB 피처 계열(supply_market·financial_ratio) 사전 스크린.

왜: 패널을 다시 굽는 비용(1~2시간)을 쓰기 전에, 그 계열이 **단변량 edge 를 갖는지**와
누수 계약(시장레벨·종목상수·as-of)을 먼저 본다. E V1(이벤트 17종)에서 이 스크린이
"top30 선별에 0개 진입"을 5분 만에 예고했고, 그대로 맞았다.

판정규칙 3의 게이트를 그대로 계산한다:
 - 단일 피처 AUC > 0.75 → 누수 후보
 - 종목별 유니크값 1개 비율 > 0.5 → 종목상수(종목 식별 증폭 위험)
 - 같은 날짜 종목간 값이 1인 비율 ≥ 0.9 → 시장레벨(횡단면 모델 부적합)
 - 재무: rcept_dt > trade_date 행이 있으면 as-of 위반(그 피처는 사용 금지)
라벨은 패널 price 로 직접 만든 forward-5일 수익률 부호(스윕과 다른 계열 — 스윕의
라벨은 날짜내 분위라 이 스크린은 '절대방향' 기준임. 한계는 보고서에 명시할 것).
"""
import json
import os
import sys

import numpy as np
import pandas as pd
import psycopg2

PANEL = "/app/app/models/wf/panel_420_asofpatch.npz"
# 표마다 키 날짜 컬럼이 다르다(기본 trade_date). stock_sentiment 는 analysis_date 를 쓴다.
DATE_COLS = {"stock_sentiment": "analysis_date"}
TABLES = {
    # ── 뉴스 감성 그룹 (2026-09-28 추가) ─────────────────────────────────────
    # 왜: 리서처가 뉴스 파이프라인(stock_sentiment·news_analysis)을 계속 채우고 있는데
    # 이 그룹만 DB 스크린을 한 번도 통과하지 않았다. 조정 축(피처·HP·유니버스·라벨)이
    # 17사이클 전부 +0.02 미달인 상황에서 '이미 적재된 시간가변 데이터에 edge 가 있는가'는
    # 다음 레버를 정하는 데 가장 결정적인 질문이다.
    "stock_sentiment": [
        "avg_sentiment", "sentiment_count", "positive_count", "negative_count",
        "neutral_count", "avg_authenticity",
    ],
    "supply_market_features": [
        "institution_net_buy", "institution_net_buy_5d", "foreign_net_buy",
        "foreign_net_buy_5d", "foreign_ownership_pct", "institution_ownership_pct",
        "retail_ownership_pct", "short_interest_ratio", "short_selling_ratio",
        "days_to_cover", "momentum_3_12m", "momentum_ni", "momentum_op",
        "krx_advance_decline_ratio", "krx_total_trading_value", "market_breadth",
        "market_impact_score", "relative_strength", "bb_position",
    ],
    # ── CG273(2026-11-14): 스크린 범위 확장 — 3테이블 44컬럼 → 8테이블 89컬럼 ──────
    # 왜: CG270/271/272 가 'DB 원천 축 종결'을 2라벨×2패널로 확정했지만 그 스크린은
    # supply_market_features·financial_ratio_features·stock_sentiment **3테이블 44컬럼**만 봤다.
    # DB 에는 패널에 한 번도 들어간 적 없는 종목 단위 테이블이 더 있다(아래 5개).
    # 시장레벨 테이블(us_market_data·macro_features·krx_derivatives·krx_program_trading·
    # futures_options·foreign_investor_futures)은 횡단면 모델 투입 금지(누수·구조 불일치)라 제외한다.
    # financial_statements 는 report_date 키라 (code,date) 정확조인이 성립하지 않아 제외 —
    # 공시지연 as-of 조인이 필요하고, 파생값은 financial_ratio_features 에 이미 있다.
    "event_features": [
        "event_capital_increase_5d", "event_cb_bw_5d", "event_contract_5d",
        "event_delisting_5d", "event_disaster_5d", "event_exec_change_5d",
        "event_litigation_5d", "event_mna_5d", "event_new_product_5d",
        "event_partnership_5d", "event_patent_5d", "event_realized_5d",
        "event_recall_5d", "event_regulation_5d", "event_stake_change_5d",
        "event_treasury_5d", "disclosure_count_5d",
    ],
    "sns_post_features": [
        "sentiment_score", "attention_score", "momentum_score", "author_quality_score",
        "post_count", "bot_filtered_count", "kalman_sentiment", "kalman_attention",
        "kalman_momentum", "kalman_activity",
    ],
    "krx_short_selling": ["short_volume", "short_value", "short_ratio", "balance_quantity",
                          "total_volume"],
    "foreign_institutional": ["foreign_net_buy", "institution_net_buy", "individual_net_buy",
                              "foreign_net_buy_qty", "institution_net_buy_qty",
                              "individual_net_buy_qty"],
    "ownership": ["foreign_ownership_pct", "institution_ownership_pct", "foreign_held_qty",
                  "listed_shares"],
    "financial_ratio_features": [
        "value_per", "value_pbr", "value_psr", "value_pcr", "value_ncav",
        "value_ev_ebit", "value_pfcr", "quality_cp_to_assets", "quality_op_to_equity",
        "quality_roe", "quality_roa", "quality_f_score", "quality_asset_growth",
        "quality_debt_ratio_change", "quality_op_growth", "quality_earnings_volatility",
        "roe", "per_current", "pbr_current",
    ],
}


def fwd_label(dates, codes, price, horizon=5, kind="abs", q=None):
    """선행 h일 수익률 라벨.

    kind="abs"   : ret>0 → 1, 아니면 0 (절대방향 — 원래 스크린의 라벨)
    kind="quantile": 그날 분위 꼬리 — ret>직전날 quantile(1−q) → 1, ret<quantile(q) → 0,
                     가운데는 NaN. **스윕/학습 라벨(wf_wave.make_labels kind="quantile")과 같은 정의**다.
    왜 필요한가(실측 2026-11-14 CG270): 스크린이 '절대방향' 라벨로 채점하는데 실제 학습·판정 라벨은
    날짜내 분위 꼬리(q0.3)다. 두 라벨은 서로 다른 과제라, 어떤 피처가 절대방향엔 무정보여도
    분위 과제엔 정보를 가질 수 있다 → 같은 44개 컬럼을 실제 라벨로 다시 채점해야 비교가 성립한다.
    """
    df = pd.DataFrame({"date": pd.to_datetime(dates), "code": codes, "px": price})
    df = df.sort_values(["code", "date"])
    f = df.groupby("code")["px"].shift(-horizon) / df["px"] - 1.0
    ret = f.to_numpy(dtype=float)
    if kind == "quantile":
        s = pd.Series(ret)
        day = df["date"].to_numpy()
        hi = s.groupby(day).transform(lambda x: x.quantile(1.0 - float(q)))
        lo = s.groupby(day).transform(lambda x: x.quantile(float(q)))
        lab = np.where(np.isnan(ret), np.nan,
                       np.where(ret > hi.to_numpy(), 1.0,
                                np.where(ret < lo.to_numpy(), 0.0, np.nan)))
        return lab, ret
    lab = np.where(np.isnan(ret), np.nan, (ret > 0).astype(float))
    return lab, ret


def auc_rank(x, y):
    m = ~(np.isnan(x) | np.isnan(y))
    if m.sum() < 100 or len(np.unique(y[m])) < 2:
        return None
    r = pd.Series(x[m]).rank().to_numpy()
    pos = y[m] == 1
    npos, nneg = int(pos.sum()), int((~pos).sum())
    if not npos or not nneg:
        return None
    return float((r[pos].sum() - npos * (npos + 1) / 2) / (npos * nneg))


def main():
    import argparse
    ap = argparse.ArgumentParser(description="DB 원천 컬럼 단변량 스크린(패널 재빌드 전 사전 게이트)")
    ap.add_argument("--label", choices=("abs", "quantile"), default="abs",
                    help="abs=절대방향(기존) · quantile=학습/스윕과 같은 날짜내 분위 꼬리 라벨")
    ap.add_argument("--q", type=float, default=0.30, help="--label quantile 의 꼬리 분위(기본 0.30)")
    ap.add_argument("--panel", default=PANEL, help="기준 패널 npz(기본 panel_420_asofpatch)")
    args = ap.parse_args()
    panel = args.panel
    z = np.load(panel, allow_pickle=True)
    dates = pd.to_datetime(z["dates"])
    codes = [str(c) for c in z["codes"]]
    lab, ret = fwd_label(dates, codes, z["price"], kind=args.label,
                         q=args.q if args.label == "quantile" else None)
    key = pd.DataFrame({"code": codes, "date": dates})

    conn = psycopg2.connect(host=os.environ.get("POSTGRES_HOST", "postgres"),
                            port=os.environ.get("POSTGRES_PORT", "5432"),
                            user=os.environ["POSTGRES_USER"],
                            password=os.environ["POSTGRES_PASSWORD"],
                            dbname=os.environ["POSTGRES_DB"])
    out = {"panel": {"file": os.path.basename(panel), "rows": int(len(key)),
                     "codes": len(set(codes)),
                     "from": str(dates.min().date()), "to": str(dates.max().date())},
           "label": {"kind": args.label, "q": (args.q if args.label == "quantile" else None),
                     "horizon": 5, "n_labeled": int(np.sum(~np.isnan(lab))),
                     "pos_rate": (round(float(np.nanmean(lab)), 4) if np.sum(~np.isnan(lab)) else None)},
           "features": {}, "asof": {}}
    cur = conn.cursor()
    for table, cols in TABLES.items():
        dcol = DATE_COLS.get(table, "trade_date")
        sel = ", ".join(cols)
        extra = ", rcept_dt" if table == "financial_ratio_features" else ""
        cur.execute(
            f"select stock_code, {dcol}, {sel}{extra} from {table} "
            f"where stock_code = any(%s) and {dcol} between %s and %s",
            (list(set(codes)), dates.min().date(), dates.max().date()))
        rows = cur.fetchall()
        names = ["code", "date"] + cols + (["rcept_dt"] if extra else [])
        f = pd.DataFrame(rows, columns=names)
        f["date"] = pd.to_datetime(f["date"])
        m = key.merge(f, on=["code", "date"], how="left")
        # 같은 날짜 횡단면 유니크(시장레벨 판정)
        uniq_by_date = m.groupby("date")[cols].nunique(dropna=True)
        obs_by_date = m.groupby("date")[cols].count()
        judged = obs_by_date >= 2
        mkt_rate = {}
        for c in cols:
            den = int(judged[c].sum())
            if den == 0:
                mkt_rate[c] = 0.0
                continue
            both = judged[c] & (uniq_by_date[c] <= 1)
            mkt_rate[c] = float(both.sum() / den)
        for c in cols:
            x = pd.to_numeric(m[c], errors="coerce").to_numpy(dtype=float)
            nun = m.groupby("code")[c].nunique(dropna=True)
            ics = []
            _tmp = pd.DataFrame({"date": m["date"], "x": x, "fwd": ret}).dropna()
            for _, g in _tmp.groupby("date"):
                if len(g) >= 10 and g["x"].nunique() > 1:
                    ics.append(float(g["x"].corr(g["fwd"], method="spearman")))
            ic_mean = round(float(np.mean(ics)), 4) if ics else None
            ic_t = (round(float(np.mean(ics) / (np.std(ics, ddof=1) / np.sqrt(len(ics)))), 2)
                    if len(ics) > 3 and np.std(ics, ddof=1) > 0 else None)
            out["features"][f"{table}.{c}"] = {
                "coverage": round(float(np.mean(~np.isnan(x))), 4),
                "stock_const_ratio": round(float(np.mean(nun.to_numpy() <= 1)), 4),
                "market_level_rate": round(mkt_rate[c], 4),
                "single_feature_auc": (None if auc_rank(x, lab) is None
                                       else round(auc_rank(x, lab), 4)),
                "xs_ic_mean": ic_mean,
                "xs_ic_t": ic_t,
                "n_ic_dates": len(ics),
            }
        if extra:
            rd = pd.to_datetime(m["rcept_dt"], errors="coerce")
            bad = int((rd > m["date"]).sum())
            out["asof"][table] = {
                "matched_rows": int(m["rcept_dt"].notna().sum()),
                "rcept_dt_gt_trade_date": bad,
                "verdict": "위반 있음(사용 금지)" if bad else "위반 0 — as-of 준수",
            }
    cur.close()
    conn.close()

    feats = {k: v for k, v in out["features"].items() if v["single_feature_auc"] is not None}
    aucs = [v["single_feature_auc"] for v in feats.values()]
    _ic = {k: v for k, v in out["features"].items() if v.get("xs_ic_mean") is not None}
    _ic_rank = sorted(_ic.items(), key=lambda kv: -abs(kv[1]["xs_ic_mean"]))
    out["summary"] = {
        "n_features": len(out["features"]), "n_scored": len(feats),
        "auc_min": min(aucs) if aucs else None, "auc_max": max(aucs) if aucs else None,
        "auc_mean": round(float(np.mean(aucs)), 4) if aucs else None,
        "auc_ge_0_55": sorted([k for k, v in feats.items() if v["single_feature_auc"] >= 0.55],
                              key=lambda k: -feats[k]["single_feature_auc"]),
        "ic_top10": [{"feature": k, "xs_ic_mean": v["xs_ic_mean"], "xs_ic_t": v["xs_ic_t"],
                      "n_dates": v["n_ic_dates"]} for k, v in _ic_rank[:10]],
        "ic_abs_t_ge_2": sorted([k for k, v in _ic.items()
                                 if v.get("xs_ic_t") is not None and abs(v["xs_ic_t"]) >= 2.0],
                                key=lambda k: -abs(_ic[k]["xs_ic_mean"])),
        "leak_auc_gt_075": sorted([k for k, v in feats.items() if v["single_feature_auc"] > 0.75]),
        "market_level_ge_090": sorted([k for k, v in out["features"].items()
                                       if v["market_level_rate"] >= 0.90]),
        "stock_const_gt_050": sorted([k for k, v in out["features"].items()
                                      if v["stock_const_ratio"] > 0.50]),
        "dead_coverage_lt_005": sorted([k for k, v in out["features"].items()
                                        if v["coverage"] < 0.05]),
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    sys.exit(main())

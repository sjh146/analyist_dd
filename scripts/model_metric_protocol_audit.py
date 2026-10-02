#!/usr/bin/env python3
"""model_metric_protocol_audit.py — 승격 게이트 지표 대조 감사 (추론만, 학습 금지).

목표: "단일 분할 val AUC(ensemble_auc)" 대 "다중 폴드 크로스섹션 AUC(robust_auc)" 중
어느 쪽이 실제 close 경로 거래 성과(세션당 순기대값)와 상관하는지, 기존 모델 가중치로
추론만 해서 실측한다.

세 측정(각 모델을 같은 유니버스·같은 피처행렬에서 채점):
  (a) 라이브 신호   : 배포 추론 경로(EnsembleModel.predict = 균등 평균)로 같은 KOSDAQ
                      유니버스에서 스코어를 뽑아, 문턱 0.55 초과 종목 수 / 최대 스코어를 낸다
                     (scripts/_swing_ensemble_weight_probe.py 의 채점 로직 재사용).
  (b) 다중 폴드 크로스섹션 AUC : scripts/champion_robust_eval.py 의 프로토콜(연속 시간창,
                     h=5 시장상대 중앙값 라벨, 크로스섹션 AUC, purge=h, 학습구간 겹침 창 제외)을
                     그대로 쓰되, 피처 빌드를 한 번만 하고 모든 모델을 같은 행렬에서 채점한다.
  (c) 성과 지표    : scripts/fillable_expectancy.py 의 계산(발굴일 종가 매수→다음 시가 매도,
                     수수료 왕복 0.21%p, 체결성 필터 당일등락<+25%, 세션당 top-K 동일비중)을
                     재사용하되, trades.csv 의 score 를 각 모델 스코어로 치환해 '모델별 선택'을
                     적용한다.

사용(컨테이너 안, cwd=/app, PYTHONPATH=/app):
  python /app/scripts/model_metric_protocol_audit.py \
      --trades /app/data/reports/close_gate_probe/trades.csv \
      --out /app/reports/model_metric_protocol_audit.json \
      --topk 3 --folds 5 --dates-per-fold 10 --stocks 80 --horizon 5 --label-kind rel

모델 목록은 --models JSON 으로 재정의할 수 있다(기본 8개: 챔피언·후보·음성대조군).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from collections import defaultdict

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import swing_screener as ss  # noqa: E402
import champion_robust_eval as cre  # noqa: E402
import fillable_expectancy as fe  # noqa: E402
from app.feature_engine.feature_pipeline import FeaturePipeline  # noqa: E402
from app.models.ensemble_model import EnsembleModel  # noqa: E402

CONF = 0.55
ROUND_TRIP = (fe.FEE_BUY + fe.FEE_SELL + fe.TAX_SELL) * 100.0   # 0.21 %p

DEFAULT_MODELS = [
    ("champion", "/app/app/models/champion"),
    ("champion_cand", "/app/app/models/champion_cand"),
    ("challenger_cg9", "/app/app/models/challenger_cg9"),
    ("cand_cg52", "/app/app/models/cand_cg52"),
    ("cand_cg51", "/app/app/models/cand_cg51"),
    ("cand_cg57_rec", "/app/app/models/cand_cg57_rec"),
    ("scratch_w90", "/app/app/models/scratch_w90"),
    ("scratch_ctl_20260520", "/app/app/models/scratch_ctl_20260520"),
]


def load_model(label, d):
    ens = EnsembleModel(model_dir=d)
    ens.load(d)
    with open(os.path.join(d, "feature_names.json")) as f:
        names = json.load(f)
    return label, d, ens, names


def read_train_window(d):
    """training-result-*.json 의 data_start/data_end (champion_robust_eval 과 동형)."""
    return cre._meta_range(d)


def read_single_split_auc(d):
    """auc.txt(단일 분할 ensemble_auc) — 게이트가 쓰는 지표의 실측값."""
    p = os.path.join(d, "auc.txt")
    if os.path.exists(p):
        try:
            return float(open(p).read().strip())
        except Exception:
            return None
    return None


# ── 공통: (code, date) → 피처 빌드 + 날짜별 크로스섹션 랭크 ─────────────────────────
def build_features_for(rows, pipeline, verbose=True):
    """rows: [(code, date), ...]. 반환: {date: {code: feats}} (rank 주입 완료)."""
    by_date = defaultdict(list)
    for code, date in rows:
        by_date[date].append(code)
    out = {}
    t0 = time.time()
    n = 0
    for date, codes in sorted(by_date.items()):
        feats = {}
        for code in codes:
            try:
                f = pipeline.build_features(code, date)
                if f and f.get("feature_count", 0) >= 10:
                    feats[code] = f
            except Exception:
                continue
            n += 1
        if feats:
            pipeline.compute_cross_sectional_ranks(feats)
        out[date] = feats
        if verbose and len(out) % 25 == 0:
            print(f"    [build] {len(out)}일 완료 {time.time() - t0:.0f}s", flush=True)
    return out


def score_models(models, features_by_date):
    """각 모델로 (date, code) → 확률. 반환: {label: {(code, date): prob}}."""
    scores = {}
    for label, d, ens, names in models:
        m = {}
        for date, feats in features_by_date.items():
            codes = list(feats.keys())
            if not codes:
                continue
            X = np.array([[float(feats[c].get(k, 0.0)) for k in names]
                          for c in codes], dtype=np.float32)
            X = np.nan_to_num(X)
            probs = np.asarray(ens.predict(X), dtype=float).ravel()
            for c, p in zip(codes, probs):
                m[(c, date)] = float(p)
        scores[label] = m
    return scores


# ── (a) 라이브 신호 ───────────────────────────────────────────────────────────────
def part_a(models, pipeline):
    t0 = time.time()
    pg = ss.get_pg_conn()
    stocks = ss.get_kosdaq_stocks(pg)
    print(f"[a] 유니버스 KOSDAQ {len(stocks)} 종목", flush=True)
    rows = [(code, str(latest)) for (code, _n, _s, latest) in stocks]
    feats_by_date = build_features_for(rows, pipeline)
    n_codes = sum(len(v) for v in feats_by_date.values())
    print(f"[a] 피처 {n_codes}종목·일 빌드 완료 {time.time() - t0:.0f}s", flush=True)
    scores = score_models(models, feats_by_date)
    pg.close()

    result = {}
    for label, _d, _ens, _names in models:
        m = scores[label]
        vals = np.array(list(m.values()), dtype=float)
        result[label] = {
            "n": int(len(vals)),
            "deployed_max": float(vals.max()) if len(vals) else None,
            "deployed_gt_0.55": int((vals > CONF).sum()),
            "deployed_gt_0.50": int((vals > 0.5).sum()),
            "deployed_median": float(np.median(vals)) if len(vals) else None,
            "deployed_mean": float(vals.mean()) if len(vals) else None,
        }
        print(f"[a] {label}: n={len(vals)} max={result[label]['deployed_max']:.4f} "
              f">0.55={result[label]['deployed_gt_0.55']} median={result[label]['deployed_median']:.4f}",
              flush=True)
    return result


# ── (b) 다중 폴드 크로스섹션 AUC (champion_robust_eval 프로토콜 재사용) ────────────
def part_b(models, pipeline, conn, args):
    t0 = time.time()
    span = args.dates_per_fold * 2 * args.folds
    dates_desc = cre.trading_dates(conn, max(span + 5 * args.horizon, 200))
    if not dates_desc:
        print("[b] 거래일 없음", flush=True)
        return {}
    dates_asc = sorted(dates_desc)
    chunk = max(1, len(dates_asc) // args.folds)
    windows = [dates_asc[i * chunk:(i + 1) * chunk] for i in range(args.folds)]
    windows = [w for w in windows if len(w) > args.horizon + 1]

    # 유니버스(유동성 상위) — champion_robust_eval 과 동형
    univ_start = dates_asc[max(0, len(dates_asc) - 120)]
    univ_end = dates_asc[-1]
    with conn.cursor() as cur:
        cur.execute(cre.UNIVERSE_SQL, {"start": univ_start, "end": univ_end,
                                       "limit": args.stocks})
        universe = [r[0] for r in cur.fetchall()]
    print(f"[b] 유니버스 {len(universe)}종목 (유동성 상위) · 거래일 {len(dates_asc)}개 "
          f"({dates_asc[0]}~{dates_asc[-1]})", flush=True)

    # 채점할 날짜의 합집합(폴드별 표본 날짜)
    sample_dates = set()
    for w in windows:
        usable = w[:-args.horizon] if len(w) > args.horizon else []
        if not usable:
            continue
        step = max(1, len(usable) // args.dates_per_fold)
        for d in usable[::step][:args.dates_per_fold]:
            sample_dates.add(d)
    rows = [(c, d) for c in universe for d in sorted(sample_dates)]
    print(f"[b] 채점 날짜 {len(sample_dates)}개 × 종목 {len(universe)}개 — 피처 빌드 시작",
          flush=True)
    feats_by_date = build_features_for(rows, pipeline)
    scores = score_models(models, feats_by_date)
    prices = cre.load_prices(conn, universe, dates_asc[0], dates_asc[-1])

    result = {}
    for label, d, _ens, _names in models:
        m = scores[label]
        train_start, train_end = read_train_window(d)
        kept, excluded = cre._split_oos(windows, train_start, train_end)
        fold_means = []
        per_date_aucs = []
        pooled_y, pooled_p = [], []
        fold_detail = []
        for fi, w in enumerate(kept):
            usable = w[:-args.horizon] if len(w) > args.horizon else []
            if not usable:
                continue
            step = max(1, len(usable) // args.dates_per_fold)
            w_aucs = []
            for date in usable[::step][:args.dates_per_fold]:
                feats = feats_by_date.get(date, {})
                codes, probs, rets = [], [], []
                for code in universe:
                    plist = prices.get(code)
                    if not plist:
                        continue
                    r = cre.forward_return(plist, date, args.horizon)
                    if r is None:
                        continue
                    if (code, date) not in m:
                        continue
                    probs.append(m[(code, date)])
                    rets.append(r)
                    codes.append(code)
                if len(rets) < 10:
                    continue
                y = cre._make_labels(rets, args.label_kind)
                if len(set(y)) < 2:
                    continue
                a = cre.auc(y, probs)
                if a == a:
                    w_aucs.append(a)
                    per_date_aucs.append(a)
                    pooled_y.extend(y)
                    pooled_p.extend(probs)
            if w_aucs:
                fold_means.append(statistics.mean(w_aucs))
                fold_detail.append({"window": [usable[0], usable[-1]],
                                    "auc_mean": round(statistics.mean(w_aucs), 4),
                                    "n_dates": len(w_aucs)})
        robust = round(statistics.mean(fold_means), 4) if fold_means else None
        result[label] = {
            "robust_auc": robust,
            "folds": [round(x, 4) for x in fold_means],
            "fold_detail": fold_detail,
            "auc_pooled": round(cre.auc(pooled_y, pooled_p), 4) if pooled_y else None,
            "train_start": train_start,
            "train_end": train_end,
            "windows_excluded": [[(w[0] if w else None), (w[-1] if w else None)]
                                 for w in excluded],
        }
        print(f"[b] {label}: robust_auc={robust} folds={[round(x,3) for x in fold_means]} "
              f"train={train_start}~{train_end} excl={len(excluded)}", flush=True)
    return result


# ── (c) close 경로 순기대값 (fillable_expectancy 계산 재사용, 모델별 top-K) ────────
def part_c(models, pipeline, conn, args):
    t0 = time.time()
    trades_path = args.trades
    if not os.path.exists(trades_path):
        print(f"[c] trades 없음: {trades_path}", flush=True)
        return {}
    t = fe.load_trades(trades_path)          # code/date 표준화 + score 열 포함
    rows = list(zip(t["code"].tolist(), t["date"].tolist()))
    print(f"[c] 후보 {len(rows)}행·{t['date'].nunique()}세션 — 피처 빌드 시작", flush=True)
    feats_by_date = build_features_for(rows, pipeline)
    scores = score_models(models, feats_by_date)

    result = {}
    exit_col = fe.EXIT_COLS["next_open"]
    for label, _d, _ens, _names in models:
        m = scores[label]
        tt = t.copy()
        # trades.csv 의 기존 score(close_screener)를 모델 스코어로 치환한다(모델별 선택).
        tt["score"] = [m.get((c, dd), np.nan) for c, dd in zip(tt["code"], tt["date"])]
        tt = tt[tt["score"].notna()].copy()
        # fillable_expectancy 의 체결성 필터(당일등락 < +25%)
        tt = tt[tt["day_change_pct"] < args.max_day_chg]
        per_session, dates, _detail = fe.simulate(tt, args.topk, exit_col, ROUND_TRIP)
        st = fe.session_stats(per_session, dates)
        result[label] = {
            "n_rows_scored": int(len(tt)),
            "n_sessions": st.get("n_sessions", 0),
            "avg_pct": st.get("avg_pct"),
            "median_pct": st.get("median_pct"),
            "pos_sessions_pct": st.get("pos_sessions_pct"),
            "t_stat": st.get("t_stat"),
            "worst_session_pct": st.get("worst_session_pct"),
        }
        print(f"[c] {label}: n={result[label]['n_rows_scored']} "
              f"세션={result[label]['n_sessions']} 순기대={result[label]['avg_pct']}% "
              f"t={result[label]['t_stat']}", flush=True)
    return result


# ── 상관 ─────────────────────────────────────────────────────────────────────────
def spearman(x, y):
    def rank(a):
        order = sorted(range(len(a)), key=lambda i: a[i])
        r = [0.0] * len(a)
        for i, idx in enumerate(order):
            r[idx] = float(i)
        return r
    rx, ry = rank(x), rank(y)
    return float(np.corrcoef(rx, ry)[0, 1])


def correlate(a_metrics, b_metrics, c_metrics):
    labels = sorted(c_metrics.keys())
    out = {"n": len(labels), "labels": labels}
    c_avg = [c_metrics[l]["avg_pct"] for l in labels]
    c_t = [c_metrics[l]["t_stat"] for l in labels]

    # (a) 지표 후보들
    a_cands = {
        "deployed_max": [a_metrics[l]["deployed_max"] for l in labels],
        "deployed_gt_0.55": [a_metrics[l]["deployed_gt_0.55"] for l in labels],
        "deployed_median": [a_metrics[l]["deployed_median"] for l in labels],
    }
    # (b) 지표
    b_cands = {
        "robust_auc": [b_metrics[l]["robust_auc"] for l in labels],
        "auc_pooled": [b_metrics[l]["auc_pooled"] for l in labels],
    }
    # 단일 분할(게이트 지표)도 대조
    s_cands = {"single_split_auc": [read_single_split_auc(DEFAULT_MODELS_DIR[l])
                                    for l in labels]}

    def valid(vals):
        return all(v is not None and v == v for v in vals)

    corr = {"vs_avg_pct": {}, "vs_t_stat": {}}
    for name, vals in list(a_cands.items()) + list(b_cands.items()) + list(s_cands.items()):
        if not valid(vals):
            corr["vs_avg_pct"][name] = None
            corr["vs_t_stat"][name] = None
            continue
        corr["vs_avg_pct"][name] = spearman(vals, c_avg) if len(vals) >= 3 else None
        corr["vs_t_stat"][name] = spearman(vals, c_t) if len(vals) >= 3 else None
    return corr


DEFAULT_MODELS_DIR = dict(DEFAULT_MODELS)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="승격 게이트 지표 대조 감사 (추론만)")
    ap.add_argument("--trades", default="/app/data/reports/close_gate_probe/trades.csv")
    ap.add_argument("--out", default="/app/reports/model_metric_protocol_audit.json")
    ap.add_argument("--models", default=None, help="JSON [[label, dir], ...] (기본 8개)")
    ap.add_argument("--topk", type=int, default=3)
    ap.add_argument("--max-day-chg", type=float, default=25.0)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--dates-per-fold", type=int, default=10)
    ap.add_argument("--stocks", type=int, default=80)
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--label-kind", choices=("rel", "abs"), default="rel")
    ap.add_argument("--skip", default="", help="콤마 구분: a,b,c 중 건너뛸 파트")
    args = ap.parse_args(argv)

    if args.models:
        model_spec = [(str(x), str(y)) for x, y in json.loads(args.models)]
    else:
        model_spec = list(DEFAULT_MODELS)
    global DEFAULT_MODELS_DIR
    DEFAULT_MODELS_DIR = dict(model_spec)

    models = [load_model(l, d) for l, d in model_spec]
    for label, d, _ens, names in models:
        print(f"[load] {label}: 피처 {len(names)}개 · 단일분할 auc.txt={read_single_split_auc(d)} "
              f"· train={read_train_window(d)}", flush=True)

    conn = ss.get_pg_conn()
    pipeline = FeaturePipeline(pg_conn=conn)
    skip = {x for x in args.skip.split(",") if x}
    out = {
        "config": {"topk": args.topk, "max_day_chg": args.max_day_chg,
                   "folds": args.folds, "dates_per_fold": args.dates_per_fold,
                   "stocks": args.stocks, "horizon": args.horizon,
                   "label_kind": args.label_kind,
                   "models": [{"label": l, "dir": d} for l, d in model_spec]},
        "single_split_auc": {l: read_single_split_auc(d) for l, d in model_spec},
        "train_windows": {l: read_train_window(d) for l, d in model_spec},
    }

    # 피처 캐시는 (code,date) 재방문 시 rank 주입이 서로 다른 크로스섹션과 섞이는 것을
    # 막기 위해 파트마다 비운다(build_features 는 rank 주입 전 객체를 캐시하고,
    # compute_cross_sectional_ranks 가 그 객체를 제자리에서 변형한다).
    if "a" not in skip:
        pipeline._cache.clear()
        out["a_live_signal"] = part_a(models, pipeline)
    if "b" not in skip:
        pipeline._cache.clear()
        out["b_robust_auc"] = part_b(models, pipeline, conn, args)
    if "c" not in skip:
        pipeline._cache.clear()
        out["c_net_expectancy"] = part_c(models, pipeline, conn, args)

    if all(k in out for k in ("a_live_signal", "b_robust_auc", "c_net_expectancy")):
        out["correlation"] = correlate(out["a_live_signal"], out["b_robust_auc"],
                                       out["c_net_expectancy"])
        print("\n[correlation] (스피어만 순위상관, n=%d)" % out["correlation"]["n"],
              flush=True)
        print(json.dumps(out["correlation"], ensure_ascii=False, indent=2), flush=True)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n[기록] {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

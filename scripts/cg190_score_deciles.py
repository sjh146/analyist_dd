#!/usr/bin/env python3
"""cg190_score_deciles.py — 모델 점수의 **세션 내 10분위** 순기대(체결 가능·수수료 차감) 단조성.

무엇을 재나 (CG190, 2026-11-11 엔지니어 자율):
  같은 표본(close_gate_probe trades.csv = 발굴일 종가 매수 후보 2,251행·113세션)에서
  모델 점수를 세션 안에서 10분위로 나눠 **분위별 순기대**를 낸다. top-k(CG185)는 k 를 바꿔도
  전부 음수였으므로, 남은 질문은 "점수가 실제로 순서 정보를 갖는가"다:
    - D1→D10 평균의 스피어만 ρ ≥ +0.5 이고 D10−D1 ≥ +0.3%p → 순서 정보 있음
    - ρ < +0.3 또는 D10−D1 ≤ 0 → 랭킹 무효(레버를 라벨·목적함수 축으로 옮긴다)

계산 규칙(최우선 규칙 3):
  · 체결성 필터: 당일등락 < +25% (fillable_expectancy 와 동일)
  · 수수료 왕복 0.21%p 차감(fe.ROUND_TRIP 과 동일 상수)
  · 청산: 익일 시가(ret_next_open_pct)
  · 분위 멤버는 동일비중 → 각 분위 세션평균은 top-k 세션평균과 같은 단위(%p/세션)

왜 세션평균을 단위로 쓰나: 세션 공통분(시장 베타)을 상쇄하려면 세션별 짝 Δ(D10−D1)를 봐야 한다
(CG172 교훈). 그래서 분위별 평균·t 와 함께 **세션별 짝 Δ**를 따로 낸다.

사용(컨테이너 안, cwd=/app):
  python -u scripts/cg190_score_deciles.py --out /app/reports/overnight/cg190_score_deciles.json \
      --models '[["cand","/app/app/models/champion_cand_cg183"],["champ","/app/app/models/champion"]]'
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import fillable_expectancy as fe          # noqa: E402
import model_metric_protocol_audit as mpa  # noqa: E402
from _paths import proj, proj_dir


def spearman(xs, ys):
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        for pos, idx in enumerate(order):
            r[idx] = float(pos)
        return r
    if len(xs) < 3:
        return None
    return round(float(np.corrcoef(rank(xs), rank(ys))[0, 1]), 4)


def decile_stats(t, label, deciles, exit_col, rt, min_rows_session):
    """세션 내 분위별 순기대 + 단조성. 반환 dict(요약) — 판정은 이 dict 만 쓴다."""
    per_sess = {d: {} for d in range(1, deciles + 1)}
    n_rows_by = {d: 0 for d in range(1, deciles + 1)}
    pool_sess = {}
    skipped = []
    for date, g in t.groupby("date", sort=True):
        g = g[[exit_col, "score"]].dropna()
        if len(g) < min_rows_session:
            skipped.append(str(date))
            continue
        g = g.sort_values("score", ascending=False)
        rets = g[exit_col].to_numpy(dtype=float) - rt
        idx = np.arange(len(g))
        # 상위가 큰 분위: 0-based 순위 → 분위 10 이 최상위
        grp = np.floor(idx * deciles / len(g)).astype(int)          # 0..deciles-1 (0=최상위)
        for gi in range(deciles):
            m = grp == gi
            if not m.any():
                continue
            d = deciles - gi                                        # 10=최상위
            per_sess[d][str(date)] = float(rets[m].mean())
            n_rows_by[d] += int(m.sum())
        pool_sess[str(date)] = float(rets.mean())

    out = {"n_sessions": 0, "skipped_sessions": skipped[:5], "n_skipped": len(skipped),
           "deciles": [], "n_rows": int(n_rows_by_total(n_rows_by))}
    dates_all = sorted(pool_sess)
    out["n_sessions"] = len(dates_all)
    if not dates_all:
        out["error"] = f"유효 세션이 없다(세션당 최소 {min_rows_session}행 필요)"
        return out

    means = []
    for d in range(1, deciles + 1):
        vals = [per_sess[d][dt] for dt in dates_all if dt in per_sess[d]]
        st = fe.session_stats(vals) if vals else {}
        means.append(st.get("avg_pct"))
        out["deciles"].append({
            "decile": d, "mean_pct": st.get("avg_pct"), "t_stat": st.get("t_stat"),
            "pos_sessions_pct": st.get("pos_sessions_pct"), "median_pct": st.get("median_pct"),
            "n_sessions": st.get("n_sessions", 0), "n_rows": n_rows_by[d],
            "worst_session_pct": st.get("worst_session_pct"),
        })
    pool = fe.session_stats([pool_sess[dt] for dt in dates_all])
    out["pool_mean_pct"] = pool.get("avg_pct")
    out["pool_t"] = pool.get("t_stat")

    paired = [per_sess[deciles][dt] - per_sess[1][dt] for dt in dates_all
              if dt in per_sess[deciles] and dt in per_sess[1]]
    pst = fe.session_stats(paired)
    half = len(paired) // 2
    out["paired_d10_d1"] = {
        "mean_pct": pst.get("avg_pct"), "t_stat": pst.get("t_stat"),
        "n_sessions": pst.get("n_sessions", 0),
        "pos": sum(1 for v in paired if v > 0), "neg": sum(1 for v in paired if v < 0),
        "front_pct": round(statistics.fmean(paired[:half]), 4) if half else None,
        "back_pct": round(statistics.fmean(paired[half:]), 4) if (len(paired) - half) else None,
    }
    if None not in means:
        out["spearman_rho"] = spearman(list(range(1, deciles + 1)), means)
        out["d10_minus_d1_pct"] = round(means[-1] - means[0], 4)
        out["monotonic"] = bool(out["spearman_rho"] is not None and out["spearman_rho"] >= 0.5
                                and out["d10_minus_d1_pct"] >= 0.3)
    out["label"] = label
    return out


def _f(v, nd=4):
    return f"{v:+.{nd}f}" if isinstance(v, (int, float)) else "n/a"


def n_rows_by_total(d):
    return sum(d.values())


def slice_stats(t, col, exit_col, rt):
    """열 값(또는 버킷)별 세션평균 순기대 + 나머지 대비 세션별 짝 Δ.

    왜: CG190 1차 결과는 '분위 전체가 음수'였다 → 문제는 랭킹이 아니라 **후보 풀(진입 조건)** 일 수
    있다. 풀 자체를 조건별로 쪼개 순기대를 재면 '어떤 진입 조건이 돈이 되는가'를 모델 없이 측정한다.
    """
    per_val = {}
    for val, g in t.groupby(col, sort=True):
        vv = {d: float(np.mean(g.loc[g["date"] == d, exit_col].to_numpy(dtype=float)) - rt)
              for d in g["date"].unique()}
        per_val[str(val)] = vv
    out = {}
    dates = sorted(set().union(*[set(v) for v in per_val.values()])) if per_val else []
    for val, vv in per_val.items():
        vals = [vv[d] for d in dates if d in vv]
        st = fe.session_stats(vals) if vals else {}
        rest = []
        for d in dates:
            others = [per_val[v2][d] for v2 in per_val if v2 != val and d in per_val[v2]]
            if d in vv and others:
                rest.append(vv[d] - statistics.fmean(others))
        pst = fe.session_stats(rest) if rest else {}
        out[val] = {"n_sessions": st.get("n_sessions", 0), "mean_pct": st.get("avg_pct"),
                    "t_stat": st.get("t_stat"), "pos_sessions_pct": st.get("pos_sessions_pct"),
                    "paired_vs_rest": {"mean_pct": pst.get("avg_pct"), "t_stat": pst.get("t_stat"),
                                       "pos": sum(1 for x in rest if x > 0),
                                       "neg": sum(1 for x in rest if x < 0)}}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="점수 10분위 순기대 단조성(CG190)")
    ap.add_argument("--trades", default="/app/data/reports/close_gate_probe/trades.csv")
    ap.add_argument("--out", default="/app/reports/overnight/cg190_score_deciles.json")
    ap.add_argument("--models", default=None, help='JSON [["cand","/app/app/models/champion_cand_cg183"]]')
    ap.add_argument("--deciles", type=int, default=10)
    ap.add_argument("--max-day-chg", type=float, default=25.0)
    ap.add_argument("--min-rows-session", type=int, default=10,
                    help="이보다 적은 행의 세션은 분위가 퇴화하므로 제외(제외 목록을 남긴다). "
                         "표본은 세션당 17~20행이라 20 으로 두면 세션이 대량 탈락한다(실측).")
    ap.add_argument("--exit", choices=tuple(fe.EXIT_COLS), default="next_open")
    ap.add_argument("--enrich", action="store_true",
                    help="trades.csv 에 없는 청산 열(next_close/t2_open)을 DB 일봉으로 채운다"
                         " — '익일 시가 갭'이 음수를 만드는지 분리할 때 쓴다")
    ap.add_argument("--max-sessions", type=int, default=0, help="스모크 테스트용 세션 수 제한(0=전체)")
    ap.add_argument("--split-by", default="",
                    help="콤마 구분 열 이름 — 값별 순기대를 낸다(예: r1_ok,heat_ok). 모델과 무관한 풀 측정")
    ap.add_argument("--buckets", default="",
                    help="col:edge1,edge2,... 형식(예: day_change_pct:-99,0,3,7,15,99) — 구간별 순기대")
    # 패널 캐시 서명용 격자 — 피처는 격자와 무관하므로 CG185 와 같은 값(topk=3)을 써서 캐시를 재사용한다.
    #   (서명에 topk 가 들어가는 것은 캐시 네임스페이스일 뿐, 분위 계산에는 영향이 없다.)
    ap.add_argument("--cache-topk", type=int, default=3)
    args = ap.parse_args(argv)

    model_spec = ([(str(x), str(y)) for x, y in json.loads(args.models)] if args.models
                  else [("cand", "/app/app/models/champion_cand_cg183"),
                        ("champ", "/app/app/models/champion")])

    candidates = [args.trades,
                  "/app/app/reports/close_gate_probe_trades.csv",
                  proj("data/reports/close_gate_probe/trades.csv")]
    trades_path = next((p for p in candidates if p and os.path.exists(p)), None)
    if trades_path is None:
        print(f"[c190] trades 없음 — 시도: {candidates}", flush=True)
        return 2

    t = fe.load_trades(trades_path)
    if args.max_sessions:
        keep = sorted(t["date"].unique())[-args.max_sessions:]
        t = t[t["date"].isin(keep)]
    if args.enrich:
        import swing_screener as ss
        _c = ss.get_pg_conn()
        t = fe.enrich_from_db(t, _c)          # close·trading_value·ret_next_close/t2_open 채움
        _c.close()
        print(f"[c190] DB 보강 완료 — 청산 열 {fe.EXIT_COLS[args.exit]} 채움", flush=True)
    rows = list(zip(t["code"].tolist(), t["date"].tolist()))
    print(f"[c190] 표본 {len(rows)}행·{t['date'].nunique()}세션 ({t['date'].min()}~{t['date'].max()}) "
          f"· trades={trades_path}", flush=True)

    models = [mpa.load_model(l, d) for l, d in model_spec]
    for label, d, _ens, names in models:
        print(f"[load] {label}: 피처 {len(names)}개 · train={mpa.read_train_window(d)}", flush=True)

    import swing_screener as ss
    from app.feature_engine.feature_pipeline import FeaturePipeline
    conn = ss.get_pg_conn()
    pipeline = FeaturePipeline(pg_conn=conn)
    # 캐시 서명: 격자 값은 위 주석대로 CG185 와 동일하게 맞춘다(피처는 격자와 무관).
    ns = argparse.Namespace(folds=5, dates_per_fold=10, stocks=80, horizon=5,
                            label_kind="rel", topk=args.cache_topk)
    mpa.set_cache_signature(ns, conn)
    print("[c190] 피처 빌드 시작", flush=True)
    feats = mpa.build_features_for(rows, pipeline)
    scores = mpa.score_models(models, feats)
    exit_col = fe.EXIT_COLS[args.exit]
    rt = mpa.ROUND_TRIP

    out = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "config": {"trades": trades_path, "deciles": args.deciles,
                      "max_day_chg_pct": args.max_day_chg, "round_trip_pct": round(rt, 4),
                      "exit": args.exit, "min_rows_session": args.min_rows_session,
                      "models": [{"label": l, "dir": d} for l, d in model_spec],
                      "sample_rows": len(rows), "sample_sessions": int(t["date"].nunique())},
           "models": {}}

    # ── 풀 수준 측정(모델 무관): 조건별 순기대 ────────────────────────────────
    m0 = scores[models[0][0]]
    pool = t.copy()
    pool["score"] = [m0.get((c, dd), np.nan) for c, dd in zip(pool["code"], pool["date"])]
    pool = pool[pool["score"].notna() & (pool["day_change_pct"] < args.max_day_chg)]
    pool_sess = {d: float(np.mean(g[exit_col].to_numpy(dtype=float)) - rt)
                 for d, g in pool.groupby("date") if g[exit_col].notna().any()}
    pst = fe.session_stats(list(pool_sess.values()))
    out["pool"] = {"n_rows": int(len(pool)), "n_sessions": pst.get("n_sessions", 0),
                   "mean_pct": pst.get("avg_pct"), "t_stat": pst.get("t_stat"),
                   "pos_sessions_pct": pst.get("pos_sessions_pct")}
    print(f"[c190] 풀({args.exit}) {out['pool']['n_rows']}행·{out['pool']['n_sessions']}세션 "
          f"순기대 {out['pool']['mean_pct']}%p t={out['pool']['t_stat']}", flush=True)
    if args.split_by:
        out["slices"] = {}
        for col in [c.strip() for c in args.split_by.split(",") if c.strip()]:
            if col not in pool.columns:
                print(f"[c190] split-by '{col}' 열 없음 — 건너뜀", flush=True)
                continue
            out["slices"][col] = slice_stats(pool.dropna(subset=[col]), col, exit_col, rt)
            for val, s in out["slices"][col].items():
                pv = s["paired_vs_rest"]
                print(f"   [{col}={val}] {_f(s['mean_pct'])}%p t={s['t_stat']} "
                      f"양 {s['pos_sessions_pct']}% (n={s['n_sessions']}) · 나머지 대비 "
                      f"{_f(pv['mean_pct'])}%p t={pv['t_stat']} ({pv['pos']}+/{pv['neg']}−)", flush=True)
    if args.buckets:
        col, _, edges_s = args.buckets.partition(":")
        edges = [float(x) for x in edges_s.split(",") if x.strip()]
        if col in pool.columns and len(edges) >= 2:
            bcol = f"{col}_bucket"
            pool = pool.copy()
            pool[bcol] = pd.cut(pool[col].astype(float), bins=edges,
                                labels=[f"{edges[i]}~{edges[i+1]}" for i in range(len(edges) - 1)])
            out.setdefault("slices", {})[bcol] = slice_stats(pool.dropna(subset=[bcol]), bcol, exit_col, rt)
            for val, s in out["slices"][bcol].items():
                pv = s["paired_vs_rest"]
                print(f"   [{bcol}={val}] {_f(s['mean_pct'])}%p t={s['t_stat']} "
                      f"양 {s['pos_sessions_pct']}% (n={s['n_sessions']}) · 나머지 대비 "
                      f"{_f(pv['mean_pct'])}%p t={pv['t_stat']}", flush=True)
        else:
            print(f"[c190] buckets '{col}' 열 없음 또는 edge 부족 — 건너뜀", flush=True)

    for label, _d, _ens, _names in models:
        m = scores[label]
        tt = t.copy()
        tt["score"] = [m.get((c, dd), np.nan) for c, dd in zip(tt["code"], tt["date"])]
        n_scored = int(tt["score"].notna().sum())
        tt = tt[tt["score"].notna()]
        tt = tt[tt["day_change_pct"] < args.max_day_chg]          # 체결성 필터(필수)
        st = decile_stats(tt, label, args.deciles, exit_col, rt, args.min_rows_session)
        st["n_rows_scored"] = n_scored
        st["n_rows_after_filter"] = int(len(tt))
        out["models"][label] = st
        print(f"[c190] {label}: 세션 {st['n_sessions']} · 행 {st['n_rows_after_filter']} · "
              f"ρ {st.get('spearman_rho')} · D10−D1 {st.get('d10_minus_d1_pct')}%p · "
              f"짝Δ t {st.get('paired_d10_d1', {}).get('t_stat')}", flush=True)
        for row in st["deciles"]:
            print(f"   D{row['decile']:>2}: {row['mean_pct']:+.4f}%p t={row['t_stat']} "
                  f"양 {row['pos_sessions_pct']}% (n={row['n_sessions']})", flush=True)
    conn.close()

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n[기록] {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

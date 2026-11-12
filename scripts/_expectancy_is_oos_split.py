#!/usr/bin/env python3
"""승격 돈 게이트 순기대 표본(in-sample vs OOS) 분해 — 측정 전용.

왜 이 측정인가 (2026-11-12 엔지니어 자율, CG210):
  champion_promote --require-expectancy 가 인용하는 순기대는 model_metric_protocol_audit part_c
  의 close_gate_probe trades.csv(2,251행 · 113세션 · 2026-05-26~2026-11-09)에서 나온다.
  그런데 그 세션 구간은 **후보·챔피언 양쪽의 학습구간과 겹친다**:
    · champion 학습 = 2026-06-25~2026-10-01 → 113세션 중 대부분이 in-sample
    · champion_cand 학습 = 2026-08-14~2026-10-28 → 역시 겹침
  즉 게이트가 인용하는 '−0.182%p / +0.0114%p' 는 **학습한 세션의 성적이 섞인 값**이다.
  여기서는 같은 표본을 모델별 train_end 로 잘라 in-sample / OOS 순기대를 따로 낸다.
  판정선 없음(측정 전용). 게이트·기준선·문턱을 바꾸지 않고, 읽기 전용(SELECT)이다.

재현:
  docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 python -u scripts/_expectancy_is_oos_split.py'
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime

import model_metric_protocol_audit as mma
import fillable_expectancy as fe

TRADES = "/app/app/reports/close_gate_probe_trades.csv"
FALLBACK_TRADES = "/app/data/reports/close_gate_probe/trades.csv"


def _latest_training_result(d):
    import glob
    cands = sorted(glob.glob(os.path.join(d, "training-result-*.json")))
    if not cands:
        return {}
    try:
        return json.load(open(cands[-1]))
    except Exception:
        return {}


def _train_boundary(d):
    """모델의 '학습 경계' — 이 날짜까지의 세션은 in-sample 로 본다.

    왜 data_end 가 아니라 fit_end 인가: 프로덕션 재학습은 data_end 까지 데이터를 받아
    fit_end 에서 학습을 끝내고 val_start~data_end 를 검증에 쓴다. 검증 구간도 모델 선택에
    쓰였으므로 게이트 표본에서는 in-sample 과 같은 오염으로 취급한다(보수적).
    실측(2026-11-12): champion_cand 는 data_end=2026-11-12 · fit_end=2026-10-28 이라
    data_end 를 경계로 쓰면 OOS 세션이 0 이 된다.
    """
    tr = _latest_training_result(d)
    for k in ("fit_end", "val_start"):
        if tr.get(k):
            return str(tr[k])[:10], f"training-result.{k}"
    tw = mma.read_train_window(d) or ()
    if isinstance(tw, (tuple, list)) and len(tw) > 1 and tw[1]:
        return str(tw[1])[:10], "training-result.data_end(폴백)"
    return None, "경계 미상"

def _stats(per_session, dates):
    keep = [(d, v) for d, v in zip(dates, per_session) if v is not None and v == v]
    if not keep:
        return {"n_sessions": 0, "avg_pct": None, "t_stat": None}
    ds = [k[0] for k in keep]
    vs = [k[1] for k in keep]
    st = fe.session_stats(vs, ds)
    out = {"n_sessions": st.get("n_sessions", len(vs)),
           "avg_pct": st.get("avg_pct"),
           "median_pct": st.get("median_pct"),
           "pos_sessions_pct": st.get("pos_sessions_pct"),
           "t_stat": st.get("t_stat")}
    half = fe.split_half(vs, ds)
    if half:
        out["halves"] = {"front_pct": half["front"].get("avg_pct"),
                         "back_pct": half["back"].get("avg_pct"),
                         "stable": half.get("stable"),
                         "split_date": half.get("split_date")}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default='[["champion","app/models/champion"],'
                                       '["champion_cand","app/models/champion_cand"]]')
    ap.add_argument("--topk", type=int, default=3)
    ap.add_argument("--max-day-chg", type=float, default=25.0)
    ap.add_argument("--json-out", default="/app/reports/overnight/expectancy_is_oos_split.json")
    args = ap.parse_args(argv)

    trades_path = next((p for p in (TRADES, FALLBACK_TRADES) if os.path.exists(p)), None)
    if trades_path is None:
        print(f"[x] trades.csv 없음 — 시도: {TRADES} / {FALLBACK_TRADES}")
        return 2
    print(f"[i] 표본: {trades_path}")

    spec = [(str(a), str(b)) for a, b in json.loads(args.models)]
    models = [mma.load_model(l, d) for l, d in spec]
    conn = mma.ss.get_pg_conn()
    pipeline = mma.FeaturePipeline(pg_conn=conn)

    t = fe.load_trades(trades_path)
    rows = list(zip(t["code"].tolist(), t["date"].tolist()))
    all_dates = sorted({str(x) for x in t["date"].tolist()})
    print(f"[i] 후보 {len(rows)}행 · {len(all_dates)}세션 ({all_dates[0]}~{all_dates[-1]}) — 피처 빌드")
    feats = mma.build_features_for(rows, pipeline)
    scores = mma.score_models(models, feats)

    exit_col = fe.EXIT_COLS["next_open"]
    out = {"created_at": datetime.now().isoformat(timespec="seconds"),
           "sample": {"path": trades_path, "n_rows": len(rows),
                      "n_sessions": len(all_dates),
                      "date_start": all_dates[0], "date_end": all_dates[-1],
                      "topk": args.topk, "exit": "next_open",
                      "fee_round_trip_pct": round(mma.ROUND_TRIP, 4),
                      "fillable_filter": f"day_change_pct < {args.max_day_chg}"},
           "models": {}}

    for label, d, _ens, names in models:
        tw = mma.read_train_window(d) or {}
        # read_train_window 는 cre._meta_range 를 그대로 돌려준다 → (start, end) 튜플.
        #   실측(2026-11-12 첫 실행): 튜플에 .get() 을 불러 AttributeError 로 죽었다(피처 빌드 9분 소모 후).
        if isinstance(tw, (tuple, list)):
            tw = {"data_start": tw[0] if len(tw) > 0 else None,
                  "data_end": tw[1] if len(tw) > 1 else None}
        train_end, boundary_src = _train_boundary(d)
        m = scores[label]
        tt = t.copy()
        tt["score"] = [m.get((c, dd), float("nan")) for c, dd in zip(tt["code"], tt["date"])]
        tt = tt[tt["score"].notna()].copy()
        tt = tt[tt["day_change_pct"] < args.max_day_chg]
        per_session, dates, _detail = fe.simulate(tt, args.topk, exit_col, mma.ROUND_TRIP)
        dates_s = [str(x) for x in dates]

        in_idx = [i for i, dd in enumerate(dates_s) if train_end and dd <= train_end]
        out_idx = [i for i, dd in enumerate(dates_s) if not train_end or dd > train_end]
        rec = {
            "train_window": tw,
            "train_end": train_end,
            "train_boundary_source": boundary_src,
            "n_feature_used": len(names),
            "all": _stats(per_session, dates_s),
            "in_sample": _stats([per_session[i] for i in in_idx], [dates_s[i] for i in in_idx]),
            "oos": _stats([per_session[i] for i in out_idx], [dates_s[i] for i in out_idx]),
            "n_rows_scored": int(len(tt)),
        }
        out["models"][label] = rec
        print(f"[r] {label}: 학습경계={train_end}({boundary_src}) 전체 {rec['all'].get('avg_pct')}%p "
              f"(세션 {rec['all'].get('n_sessions')}) | in-sample {rec['in_sample'].get('avg_pct')}%p "
              f"(세션 {rec['in_sample'].get('n_sessions')}) | OOS {rec['oos'].get('avg_pct')}%p "
              f"(세션 {rec['oos'].get('n_sessions')}, t={rec['oos'].get('t_stat')})")

    os.makedirs(os.path.dirname(args.json_out) or ".", exist_ok=True)
    with open(args.json_out, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"저장: {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

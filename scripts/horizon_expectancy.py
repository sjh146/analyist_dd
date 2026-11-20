#!/usr/bin/env python3
"""horizon_expectancy — 지평(horizon)별 기대값(돈) 하네스.

WHY (2026-11-20): docs/EXP_horizon_verdict.md 의 A/B 에서 h1 라벨 AUC 0.5568 이 h5 0.5266
보다 +0.0312 (4폴드 중 3폴드 동방향, 폴드 편차 0.0463→0.0075)로 나왔다. 그러나 "AUC 개선은
돈이 아니다" — 이 스크립트는 wf_wave 와 **같은 규칙**으로 학습한 모델의 날짜별 상위 픽을
실제로 사고 팔았을 때의 건당 기대값(%)을 계산해, 지평 승격 논의의 근거를 만든다
(계약: docs/spec_horizon_expectancy.md — 판정 규칙 §1 은 사전 고정, 사후 조정 금지).

실측 근거 (2026-11-20 조회, 조회 명령 그대로):
  # 프로브 폴드 AUC (H1_top60) — services/xgboost-ml/reports/overnight/wf_horizon_probe.json
  #   python3 -c "import json;d=json.load(open('services/xgboost-ml/reports/overnight/wf_horizon_probe.json'));print([(r['exp'],r['fold_mean'],[round(v['mean'],4) for v in r['folds'].values()]) for r in d['results']])"
  #   → H1_top60 fold_mean 0.5568, folds [0.5545, 0.5518, 0.5529, 0.5678]
  # 패널 규모 (컨테이너):
  #   docker exec stock_xgboost_ml python3 -c "import numpy as np;z=np.load('/app/app/models/wf/p_v5recent.npz',allow_pickle=True);print(z['X'].shape,len(z['feature_names']),z['dates'][0],z['dates'][-1],len(set(z['codes'])),z['price'].min(),z['price'].max())"
  #   → X (37805, 221) · features 221 · 2026-02-02 ~ 2026-11-17 · codes 200 · price 137.0~1547000.0

규칙 재사용 (전부 scripts/wf_wave.py — 지평 비교의 타당성은 "규칙 동일"에 달려 있다):
  · 라벨      = wf_wave.make_labels(df, "quantile", h, 0.3)   (L437-518, build_label 규칙:
                close[t+h]/close[t]-1 의 날짜별 분위 0.7 초과=1 / 0.3 미만=0, 중간은 결측)
  · 피처 선택  = wf_wave.subset(names, "top60", Xtr, ytr)     (L202-206, 학습구간 풀링 edge)
  · 폴드 분할   = wf_wave.main() L648-671 의 확장창 규칙을 fold_split() 으로 **동일 재현**
                 (인라인 코드라 import 불가 + 스펙 §4 가 기존 파일 수정을 금지 → 재구현하되
                 purge(경계 h일)·_ref(라벨 참조일) 규칙을 주석으로 1:1 대응시킨다)
  · 모델      = overnight_ml_loop.train_seed — wf_wave L683-690 과 동일 호출:
                 oversample_balance(rng=seed) + split_train_val(0.67) +
                 EnsembleModel(xgboost+lightgbm(+catboost, 설치 시 자동 스킵)) +
                 apply_hyperparams(lr=0.03, depth=4, n_estimators=1500, seed)
                 (wf_wave.BASE["recipe"] — wf_horizon_probe 와 동일)
  · 진입/청산 근사(스펙 §1 명시 사항): 패널 price 는 **종가**뿐 → 진입은 신호일 **다음 거래일
    종가**로 근사(시가 대신). 청산은 진입일로부터 horizon 거래일 뒤 종가(종목 자체 시계열의
    horizon 행 뒤). 즉 트레이드 창은 라벨 창(close[t+h]/close[t])을 하루 뒤로 민 것과 같다.
  · 비용: 건당 (cost_bps + 2×slip_bps) bp 를 수익률(%)에서 차감 (slip 은 편도×2).

판정 규칙(스펙 §1 사전 고정): 채택 후보 = mean_net_pct > +0.10%/건 AND n ≥ 300건 AND
  win_rate ≥ 손익분기 승률(-avg_loss/(avg_win-avg_loss)). 하나라도 미달이면
  "AUC 는 올랐지만 돈은 아니다"로 기록하고 승격하지 않는다(verdict 필드).

자기신고: 스펙 §4 가 DB 쓰기를 금지하므로 scripts/dq_claim.py 의 record_claim(DB) 은 쓰지
  않는다. 대신 산출 JSON 의 self_report 블록이 3분리를 대신한다 —
  source_rows(패널 수신 행) → label_rows/picks_generated(파서 생성) → persisted_rows(JSON 저장).

실행(컨테이너 — 스펙 §3, 실제 실행은 오케스트레이터가 한다):
  docker exec -w /app -e OMP_NUM_THREADS=4 stock_xgboost_ml \
    python -u scripts/horizon_expectancy.py --panel /app/app/models/wf/p_v5recent.npz \
    --horizons 1,2,3,5 --folds 4 --top 5 --cost-bps 21 --slip-bps 20 \
    --json-out /app/reports/overnight/horizon_expectancy.json
"""

import argparse
import json
import logging
import os
import sys
import traceback

import numpy as np
import pandas as pd

# ── 사전 고정 상수 (스펙 §1 — 그리드 탐색 없이 1회 고정값) ─────────────────────────
Q = 0.3              # 라벨 분위 (wf_wave.BASE["q"])
SELECT_STR = "top60"  # 피처 선별폭 (wf_horizon_probe / EXP_horizon_verdict 와 동일)
MIN_ROWS = 100        # 폴드 표본 하한 (wf_wave.main L669 와 동일)
# 패널 재사용 요청 조건 — wf_horizon_probe 실행과 동일 (probe json config 참조)
PANEL_LIMIT = 50
PANEL_DAYS = 420
UNIVERSE = "prod"
OUT_DIR = "/app/app/models/wf/hexp"   # train_seed 의 EnsembleModel(model_dir) (wf_wave 와 같은 관례)


def fold_split(d, folds, horizon):
    """wf_wave.main() 의 확장창 분할을 그대로 재현한다 (wf_wave.py L648-671 1:1 대응).

    ⚠ 규칙이 wf_wave 와 달라지면 지평 비교가 무효가 되므로, 여기서는 **동일 계산**만 한다:
      · dd    = 라벨 있는 행의 날짜 정렬            (wf_wave L652-653)
      · step  = n // (folds+1); fold i: cut=dd[step*i-1], nxt=dd[min(n-1, step*(i+1)-1)]
      · purge = 경계 직전 horizon 일                (wf_wave L660) — 학습 구간 끝 purge
      · _ref  = 라벨 참조일(종목별 shift(-h)) 이 테스트 시작일(dd[step*i]) 이상이면 학습에서
                제거                                  (wf_wave L662-667) — 갭 종목의 라벨 누출 방지
      · te    = (cut, nxt]                           (wf_wave L668)
    d: date(str)·stock_code·_y(비NaN)·_ref 컬럼을 가진 DataFrame (라벨 있는 행만).
    tr/te 는 reset_index(drop=True) — 값·순서는 wf_wave 와 동일(인덱스만 정규화).
    """
    dd = sorted(d["date"].astype(str).unique())
    n = len(dd)
    step = n // (folds + 1)
    out = []
    for i in range(1, folds + 1):
        cut = dd[step * i - 1]
        nxt = dd[min(n - 1, step * (i + 1) - 1)]
        purge = set(dd[max(0, step * i - horizon):step * i])
        tr = d[(d["date"] <= cut) & (~d["date"].isin(purge))].copy()
        _bad = np.greater_equal(np.asarray(tr["_ref"].astype(str).values),
                                np.asarray(dd[step * i]))
        tr = tr[np.logical_not(_bad)].reset_index(drop=True)
        te = d[(d["date"] > cut) & (d["date"] <= nxt)].reset_index(drop=True)
        out.append({"i": i, "cut": cut, "nxt": nxt, "purge": sorted(purge),
                    "first_test": dd[step * i], "train": tr, "test": te})
    return out


def pick_indices(te, probs, top):
    """날짜별 확률 상위 top 종목 선택 — 동률은 코드 오름차순 (결정적).

    스펙 §1: "각 날짜에서 모델 확률 상위 --top N 종목을 뽑는다(동률은 코드 순)".
    te: fold_split 이 내놓은 테스트 행(0..m-1 인덱스). probs: te 와 같은 길이의 확률 배열.
    반환: [(date, [te 내 위치...]), ...] 날짜 오름차순.
    """
    probs = np.asarray(probs, dtype=float)
    out = []
    for date, sub in te.groupby("date", sort=True):
        codes = sub["stock_code"].astype(str).values
        ps = probs[sub.index.values]
        order = sorted(range(len(sub)), key=lambda j: (-ps[j], codes[j]))
        k = min(top, len(sub))
        out.append((str(date), [int(sub.index.values[j]) for j in order[:k]]))
    return out


def simulate_trades(df, picks, horizon, cost_bps, slip_bps):
    """픽 → 건별 순수익(%) 시뮬레이션. look-ahead 금지(진입은 신호일 **다음** 거래일).

    df: 전 패널(date, stock_code, price — price 는 종가).
    picks: [fold, date, stock_code, prob] DataFrame (신호일 = 픽 날짜).
    진입 = 신호일 다음 거래일 **종가**(패널 price 가 종가뿐이므로 시가의 근사 — 스펙 §1 명시).
    청산 = 진입일로부터 horizon 거래일 뒤 종가(종목 자체 시계열의 horizon 행 뒤).
    net_pct = (exit/entry - 1)*100 - (cost_bps + 2*slip_bps)/100  (bp→%: /1e4*100 = /100)
    반환: (trades, skipped). trades 원소: {fold, signal_date, stock_code, prob,
    entry_date, exit_date, entry_price, exit_price, net_pct}.
    """
    key = df[["date", "stock_code", "price"]].copy()
    key["date"] = key["date"].astype(str)
    key["stock_code"] = key["stock_code"].astype(str)
    key = key.sort_values(["stock_code", "date"], kind="mergesort")
    series = {}
    for code, sub in key.groupby("stock_code", sort=True):
        series[code] = (sub["date"].values, sub["price"].values.astype(float))
    cost_pct = (float(cost_bps) + 2.0 * float(slip_bps)) / 100.0
    trades, skipped = [], []
    for r in picks.itertuples(index=False):
        code, date = str(r.stock_code), str(r.date)
        s = series.get(code)
        if s is None:
            skipped.append({"reason": "no_series", "date": date, "stock_code": code})
            continue
        dates, prices = s
        k = int(np.searchsorted(dates, date))
        if k >= len(dates) or dates[k] != date:
            skipped.append({"reason": "signal_row_missing", "date": date,
                            "stock_code": code})
            continue
        k1 = k + 1                       # 진입 = 신호일 다음 거래일 (look-ahead 금지)
        if k1 >= len(dates):
            skipped.append({"reason": "no_next_day", "date": date, "stock_code": code})
            continue
        k2 = k1 + horizon                # 청산 = 진입일로부터 horizon 거래일 뒤
        if k2 >= len(dates):
            skipped.append({"reason": "no_exit_row", "date": date, "stock_code": code})
            continue
        entry_price, exit_price = float(prices[k1]), float(prices[k2])
        if not (entry_price > 0 and exit_price > 0):
            skipped.append({"reason": "bad_price", "date": date, "stock_code": code})
            continue
        net_pct = (exit_price / entry_price - 1.0) * 100.0 - cost_pct
        trades.append({"fold": int(r.fold), "signal_date": date, "stock_code": code,
                       "prob": float(r.prob), "entry_date": str(dates[k1]),
                       "exit_date": str(dates[k2]), "entry_price": entry_price,
                       "exit_price": exit_price, "net_pct": net_pct})
    return trades, skipped


def summarize_trades(nets):
    """건별 net 수익률(%) → 요약 dict. 빈 리스트는 n=0(나머지 None).

    · std = 표본 표준편차(ddof=1, n<2 면 None)
    · t   = mean/(std/√n) — std 가 0 이거나 n<2 면 None
    · max_dd_pct = 누적 net 수익(%) 곡선의 최대 peak-to-trough (≤0, 트레이드 순서 기준)
    · win_rate   = net > 0 인 건의 비율 (엄격 양수만 승리)
    · breakeven_win_rate = -avg_loss/(avg_win-avg_loss) — 관측 승/패 평균으로 손익분기 승률
      (스펙 §1 판정의 "승률 ≥ 손익분기 승률" 기준)
    """
    n = len(nets)
    out = {"n": n, "mean_net_pct": None, "win_rate": None, "std": None,
           "t": None, "max_dd_pct": None, "breakeven_win_rate": None}
    if n == 0:
        return out
    arr = np.asarray(nets, dtype=float)
    mean = float(arr.mean())
    std = float(arr.std(ddof=1)) if n > 1 else None
    t = float(mean / (std / np.sqrt(n))) if (n > 1 and std is not None and std > 0) else None
    wins = arr[arr > 0]
    losses = arr[arr <= 0]
    be = None
    if len(wins) and len(losses):
        aw, al = float(wins.mean()), float(losses.mean())
        be = float(-al / (aw - al)) if (aw - al) != 0 else None
    elif len(wins) and not len(losses):
        be = 0.0                       # 전승이면 손익분기 승률은 0
    cum = np.cumsum(arr)
    dd = cum - np.maximum.accumulate(cum)
    out.update({"n": n, "mean_net_pct": mean, "win_rate": float(np.mean(arr > 0)),
                "std": std, "t": t, "max_dd_pct": float(dd.min()),
                "breakeven_win_rate": be})
    return out


def verdict(summary):
    """스펙 §1 판정 규칙(사전 고정 — 사후 조정 금지):
    채택 후보 = mean_net_pct > +0.10%/건 AND n ≥ 300 AND win_rate ≥ 손익분기 승률.
    하나라도 미달이면 adoptable=False ("AUC 는 올랐지만 돈은 아니다" 기록용)."""
    s = summary
    if s.get("n") == 0:
        return {"adoptable": False, "mean_ok": False, "n_ok": False, "wr_ok": False,
                "note": "표본 0건 — 채택 불가"}
    mean_ok = s["mean_net_pct"] > 0.10
    n_ok = s["n"] >= 300
    be = s.get("breakeven_win_rate")
    wr_ok = (be is not None) and (s["win_rate"] >= be)
    return {"adoptable": bool(mean_ok and n_ok and wr_ok),
            "mean_ok": bool(mean_ok), "n_ok": bool(n_ok), "wr_ok": bool(wr_ok),
            "thresholds": {"mean_net_pct_pct": 0.10, "n": 300,
                           "breakeven_win_rate": be}}


def evaluate(df, names, horizons, folds, top, cost_bps, slip_bps, seed,
             make_labels_fn, select_fn, train_predict_fn, out_dir=None, log=print):
    """지평별 워크포워드 기대값 평가 — 라벨/선별/모델/폴드는 wf_wave 규칙 그대로(주입받은
    함수로 호출), 그 위에 날짜별 상위 픽 → 기대값 시뮬레이션을 얹는다.

    make_labels_fn(df, kind, horizon, q) → y             프로덕션: wf_wave.make_labels
    select_fn(names, select, Xtr, ytr) → (idx, desc)     프로덕션: wf_wave.subset
    train_predict_fn(Xtr, ytr, Xte, yte, sel, out_dir, seed) → probs
                                                         프로덕션: overnight_ml_loop.train_seed
    반환: {"horizons": {h: rec}, "verdict": {h: ...}, "self_report": {...}} (JSON 직렬화 가능).
    이 함수는 numpy/pandas 만 쓴다 — 호스트 단위테스트에서 가짜 주입으로 전체 파이프라인을
    돌릴 수 있다(학습 경로는 컨테이너 전용이라 테스트에서 주입한다).
    """
    base_names = [n for n in names if n in df.columns]
    horizons = sorted(set(int(h) for h in horizons))
    out_horizons, verdicts = {}, {}
    self_report = {"source_rows": int(len(df)), "label_rows": {}, "train_rows": {},
                   "test_rows": {}, "picks_generated": {}, "trades_completed": {},
                   "skipped_by_reason": {}, "persisted_rows": None,
                   "note": "record_claim(DB) 미사용 — 스펙 §4 DB 쓰기 금지. 3분리: "
                           "source_rows(패널 수신) → label/picks(파서 생성) → "
                           "persisted_rows(JSON 저장)"}
    for h in horizons:
        rec = {"horizon": h, "status": "failed", "by_fold": [], "picks_sample": []}
        try:
            # ── 라벨 (wf_wave.main L645 와 동일) ──────────────────────────────
            y = np.asarray(make_labels_fn(df, "quantile", h, Q), dtype=float)
            if len(y) != len(df):
                raise RuntimeError(f"라벨 길이 {len(y)} ≠ 패널 행 {len(df)}")
            d = df.copy()
            d["_y"] = y
            d["_ref"] = df.groupby("stock_code", sort=False)["date"].shift(-h)
            d = d[~pd.isna(d["_y"])]
            self_report["label_rows"][str(h)] = int(len(d))
            picks_rows, fold_meta = [], []
            n_tr = n_te = 0
            for fm in fold_split(d, folds, h):
                tr, te = fm["train"], fm["test"]
                n_tr += len(tr)
                n_te += len(te)
                fmeta = {"fold": fm["i"], "train_rows": int(len(tr)),
                         "test_rows": int(len(te)), "test_from": fm["first_test"],
                         "test_to": fm["nxt"], "purge_dates": fm["purge"]}
                if min(len(tr), len(te)) < MIN_ROWS:
                    fmeta["skipped"] = "small_sample"
                    fold_meta.append(fmeta)
                    log(f"  h{h} fold{fm['i']}: 표본 부족(tr={len(tr)} te={len(te)}), 건너뜀")
                    continue
                # ── 피처/선별/모델 (wf_wave L672-692 와 동일) ──────────────────
                Xtr = np.nan_to_num(tr[base_names].values.astype(np.float32), nan=0.0)
                ytr = tr["_y"].values.astype(int)
                Xte = np.nan_to_num(te[base_names].values.astype(np.float32), nan=0.0)
                yte = te["_y"].values.astype(int)
                cols = np.std(Xtr, axis=0) > 0
                fn = [f for f, m in zip(base_names, cols) if m]
                Xtr, Xte = Xtr[:, cols], Xte[:, cols]
                idx, sel_desc = select_fn(fn, SELECT_STR, Xtr, ytr)
                sel = [fn[j] for j in idx]
                probs = np.asarray(train_predict_fn(
                    Xtr[:, idx], ytr, Xte[:, idx], yte, sel, out_dir, seed), dtype=float)
                if probs.ndim > 1:
                    probs = probs[:, -1]      # 2열이면 클래스1 확률 (wf_wave L690 과 동일)
                fmeta.update({"n_features": int(len(sel)), "sel_desc": str(sel_desc)})
                for date, rows in pick_indices(te, probs, top):
                    for pos in rows:
                        picks_rows.append({"fold": fm["i"], "date": date,
                                           "stock_code": str(te["stock_code"].iloc[pos]),
                                           "prob": float(probs[pos])})
                log(f"  h{h} fold{fm['i']}: train={len(ytr)} test={len(yte)} "
                    f"({fm['first_test']}~{fm['nxt']}) feat={len(sel)} {sel_desc}")
                fold_meta.append(fmeta)
            self_report["train_rows"][str(h)] = int(n_tr)
            self_report["test_rows"][str(h)] = int(n_te)
            self_report["picks_generated"][str(h)] = int(len(picks_rows))
            # ── 기대값 시뮬레이션 (스펙 §1 절차 4) ─────────────────────────────
            picks = pd.DataFrame(picks_rows, columns=["fold", "date", "stock_code", "prob"])
            trades, skipped = simulate_trades(df, picks, h, cost_bps, slip_bps)
            self_report["trades_completed"][str(h)] = int(len(trades))
            by_reason = {}
            for s in skipped:
                by_reason[s["reason"]] = int(by_reason.get(s["reason"], 0)) + 1
            self_report["skipped_by_reason"][str(h)] = by_reason
            if trades:
                trades = sorted(trades, key=lambda t: (t["signal_date"], t["stock_code"]))
            summ = summarize_trades([t["net_pct"] for t in trades])
            for fm in fold_meta:
                if fm.get("skipped"):
                    continue
                fnets = [t["net_pct"] for t in trades if t["fold"] == fm["fold"]]
                fm.update(summarize_trades(fnets))
            rec.update({"status": "ok", **summ, "by_fold": fold_meta,
                        "picks_sample": (trades[:5] + trades[-5:] if trades else [])})
            verdicts[str(h)] = verdict(summ)
            log(f"  RESULT h{h}: n={summ['n']} mean={summ['mean_net_pct']}% "
                f"win={summ['win_rate']} std={summ['std']} t={summ['t']} "
                f"maxDD={summ['max_dd_pct']} be_wr={summ['breakeven_win_rate']} "
                f"adoptable={verdicts[str(h)]['adoptable']}")
        except Exception as e:
            rec["error"] = f"{type(e).__name__}: {e}"
            verdicts[str(h)] = {"adoptable": False, "error": rec["error"]}
            log(f"FAIL h{h}: {e}")
            log(traceback.format_exc())
        out_horizons[str(h)] = rec
    return {"horizons": out_horizons, "verdict": verdicts, "self_report": self_report}


def write_json_atomic(path, payload):
    """원자적 JSON 저장(tmp 쓰기 → os.replace). 재실행은 같은 경로를 덮으므로 멱등.

    mkstemp 를 쓰지 않는다 — 컨테이너 루트가 mkstemp(0600) 로 만들면 호스트가 읽지 못한다
    (실측 교훈). open("w") 는 umask(022) 로 0644 가 되어 호스트에서 읽힌다.
    """
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, "." + os.path.basename(path) + f".tmp{os.getpid()}")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def run(args, horizons):
    """프로덕션 실행 경로 — 무거운 import(wf_wave/extra_experiments)는 여기서만 한다
    (호스트 단위테스트는 evaluate 를 가짜 주입으로 돌리고 이 경로를 타지 않는다)."""
    # 컨테이너 경로 고정(wf_wave L33-34 와 동일) + 호스트 폴백(디버그용)
    for p in ("/app", "/app/scripts"):
        if p not in sys.path:
            sys.path.insert(0, p)
    _here = os.path.dirname(os.path.abspath(__file__))
    if _here not in sys.path:
        sys.path.insert(0, _here)

    import extra_experiments as ex  # noqa: E402
    import train_curated as tc      # noqa: E402
    import wf_wave as w             # noqa: E402

    ml = ex._load_driver()
    tc.select_curated_features = lambda n, a=False: list(n)  # wf_wave.main L633 과 동일 패치
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ml.set_exp_log("horizon_expectancy")
    log = ml.log
    recipe = dict(w.BASE["recipe"])   # wf_wave 의 학습 하이퍼파라미터 (lr0.03/d4/est1500)
    log(f"horizon_expectancy start KST={ml.now_kst().isoformat(timespec='seconds')} "
        f"panel={args.panel} horizons={horizons} folds={args.folds} top={args.top} "
        f"cost={args.cost_bps}bp slip={args.slip_bps}bp seed={args.seed} recipe={recipe}")
    if not os.path.exists(args.panel):
        raise RuntimeError(f"패널 파일 없음: {args.panel}")

    df, names = w.build_panel(args.panel, PANEL_LIMIT, PANEL_DAYS, log=log,
                              end_date=None, universe=UNIVERSE, universe_seed=0)
    all_dates = sorted(df["date"].astype(str).unique())
    log(f"panel rows={len(df)} dates={len(all_dates)} ({all_dates[0]} ~ {all_dates[-1]})")

    os.makedirs(OUT_DIR, exist_ok=True)

    def _train_predict(Xtr, ytr, Xte, yte, sel, o_dir, seed):
        # wf_wave.main L683-690 과 동일 호출 규칙(train_seed → ensemble predict, 클래스1 확률)
        _a, _m_aucs, _cur, ens = ml.train_seed(
            Xtr, None, Xte, ytr, None, yte, sel, o_dir, seed,
            recipe["lr"], recipe["depth"], recipe["n_estimators"], True, None)
        p = np.asarray(ens.predict(Xte), dtype=float)
        return p[:, -1] if p.ndim > 1 else p

    result = evaluate(
        df, names, horizons, args.folds, args.top, args.cost_bps, args.slip_bps,
        args.seed, make_labels_fn=w.make_labels,
        select_fn=lambda fn, s, Xtr, ytr: w.subset(fn, s, Xtr, ytr),
        train_predict_fn=_train_predict, out_dir=OUT_DIR, log=log)

    payload = {"finished_at": ml.now_iso(), "config": vars(args),
               "entry_note": "진입=신호일 다음 거래일 **종가**(패널 price 가 종가뿐 — 시가 근사, 스펙 §1 명시)",
               "exit_note": "청산=진입일로부터 horizon 거래일 뒤 종가(종목 자체 시계열 행 기준)",
               "cost_model": f"cost_bps(왕복) + 2×slip_bps(편도×2) = "
                             f"{args.cost_bps + 2 * args.slip_bps}bp/건 차감",
               **result}
    payload["self_report"]["persisted_rows"] = 1
    try:
        write_json_atomic(args.json_out, payload)
    except Exception as e:
        payload["self_report"]["persisted_rows"] = 0
        log(f"JSON 저장 실패({args.json_out}): {e}")
        raise
    log(f"horizon_expectancy done → {args.json_out} self_report={payload['self_report']}")
    print(json.dumps({h: {k: rec.get(k) for k in ("n", "mean_net_pct", "win_rate",
                                                   "std", "t", "max_dd_pct")}
                      for h, rec in payload["horizons"].items()}, ensure_ascii=False))
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="지평별 기대값(돈) 하네스 — docs/spec_horizon_expectancy.md 계약 구현")
    ap.add_argument("--panel", required=True,
                    help="패널 npz 경로 (예 /app/app/models/wf/p_v5recent.npz)")
    ap.add_argument("--horizons", default="1,2,3,5",
                    help="쉼표 구분 지평 목록(거래일) — 스펙 예: 1,2,3,5")
    ap.add_argument("--folds", type=int, default=4)
    ap.add_argument("--top", type=int, default=5, help="날짜별 확률 상위 픽 종목 수")
    ap.add_argument("--cost-bps", type=int, default=21, help="왕복 수수료·세금 (bp)")
    ap.add_argument("--slip-bps", type=int, default=20, help="편도 슬리피지 (bp, ×2 적용)")
    ap.add_argument("--json-out", default="/app/reports/overnight/horizon_expectancy.json")
    ap.add_argument("--seed", type=int, default=1,
                    help="모델 시드 (wf_horizon_probe 와 동일하게 1)")
    args = ap.parse_args()
    if args.folds < 1 or args.top < 1 or args.cost_bps < 0 or args.slip_bps < 0:
        print("오류: folds/top ≥ 1, cost/slip ≥ 0", file=sys.stderr)
        return 2
    horizons = [int(x.strip()) for x in args.horizons.split(",") if x.strip()]
    if not horizons:
        print("오류: --horizons 가 비었다", file=sys.stderr)
        return 2
    try:
        return run(args, horizons)
    except Exception as e:
        print(f"FATAL {type(e).__name__}: {e}", file=sys.stderr)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())

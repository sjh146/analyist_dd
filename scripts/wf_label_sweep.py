#!/usr/bin/env python3
"""wf_label_sweep — 라벨 설계만 바꿔가며 확장창 walk-forward 를 돌린다.

배경 (왜 라벨인가)
  ─ 스킬 실측: AUC 를 올리는 순서는 **라벨 → 데이터 → 피처**이고, 같은 피처셋에서 라벨만
    바꿔 +0.06 이 나온 사례가 있다(절대 1일방향 0.5133 → 시장상대 0.5270 → 분위+호라이즌).
  ─ 그런데 `wf_wave.py` 의 `make_labels` 에는 ``kind="relative"``(시장상대 이진: 횡단면 중앙값
    초과=1) 가 **구현돼 있는데 한 번도 호출되지 않는다**. main 은 ``"quantile"`` 만 쓴다.
  ─ 즉 "시장상대 라벨"은 확장창 walk-forward 에서 **측정된 적이 없다**. 또 q(분위 비율)도 0.3 고정이다.

이 스크립트는 `wf_wave` 의 패널·폴드·purge·피처선별·학습을 **그대로 재사용**해서(=결과 비교 가능)
라벨 변형만 갈아끼운다. 패널 캐시를 공유하므로 재빌드 비용이 없다.

실행(컨테이너):
  cd /app && OMP_NUM_THREADS=4 python -u scripts/wf_label_sweep.py
  cd /app && OMP_NUM_THREADS=4 python -u scripts/wf_label_sweep.py --folds 5 --seeds 3
  cd /app && OMP_NUM_THREADS=4 python -u scripts/wf_label_sweep.py --wait-for-panel 60
결과: /app/reports/overnight/wf_label_sweep.jsonl + wf_label_sweep_summary.json

판정: **폴드 평균 AUC 의 평균**과 폴드 간 표준편차. 단일 분할/단일 폴드 값은 쓰지 않는다.
"""
import argparse
import json
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

import wf_wave as W  # noqa: E402  (패널 빌드·라벨·선별·폴드 로직 재사용)

ml = W.ml

# 라벨 변형만 바꾼다. select/recipe 는 기준 러너와 동일하게 고정해 라벨 효과만 본다.
CONFIGS = [
    {"id": "LS_quant_q30_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "desc": "기준(현 최고 실험과 동일): 분위0.3 h5 top30"},
    {"id": "LS_rel_h5",       "kind": "relative", "horizon": 5, "q": None, "select": "top30",
     "desc": "시장상대 이진(중앙값 초과=1) h5 top30 — 미측정 레버"},
    {"id": "LS_rel_h5_t40",   "kind": "relative", "horizon": 5, "q": None, "select": "top40",
     "desc": "시장상대 이진 h5 top40"},
    {"id": "LS_rel_h3",       "kind": "relative", "horizon": 3, "q": None, "select": "top30",
     "desc": "시장상대 이진 h3 top30"},
    # ── 호라이즌 축 (장외 자율 루프가 추가) ─────────────────────────────────
    # 근거: label_wave5 실측 — h8 + 분위0.3 + top30 이 3분할 평균 0.5727±0.0277
    # (최고 분할 0.6015 / 최저 0.5463). 그러나 그 실험은 150종목·단일분할 프로토콜이라
    # 이 스윕(49종목·5폴드 확장창)과 직접 비교가 불가하다 → 같은 프로토콜로 h8 을 채워
    # "라벨 호라이즌 효과"와 "유니버스 효과"를 분리한다.
    {"id": "LS_quant_q30_h8",  "kind": "quantile", "horizon": 8, "q": 0.30, "select": "top30",
     "desc": "분위0.3 h8 top30 — wave5 최고 설정을 동일 프로토콜로 검증"},
    {"id": "LS_quant_q30_h10", "kind": "quantile", "horizon": 10, "q": 0.30, "select": "top30",
     "desc": "분위0.3 h10 top30 — 호라이즌 상단(과확장 여부 확인)"},
    {"id": "LS_rel_h8",        "kind": "relative", "horizon": 8, "q": None, "select": "top30",
     "desc": "시장상대 이진 h8 top30 — 분위 대신 중앙값 기준"},
    {"id": "LS_quant_q25_h5", "kind": "quantile", "horizon": 5, "q": 0.25, "select": "top30",
     "desc": "분위0.25 h5 top30 (표본 +)"},
    {"id": "LS_quant_q20_h5", "kind": "quantile", "horizon": 5, "q": 0.20, "select": "top30",
     "desc": "분위0.20 h5 top30 (표본 ++)"},
    # ── 피처 변환 축 (라벨은 최고 설정 고정, 횡단면 정규화만 바꾼다) ──────────────
    {"id": "TR_rank_h5",    "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "transform": "rank",
     "desc": "피처 날짜별 횡단면 rank(pct) — 시장레벨 성분 제거"},
    {"id": "TR_zscore_h5",  "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "transform": "zscore",
     "desc": "피처 날짜별 횡단면 z-score"},
    {"id": "TR_rank_h5_t60", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top60",
     "transform": "rank",
     "desc": "횡단면 rank + top60 (정규화하면 더 많은 피처를 쓸 여지)"},
    {"id": "TR_rank_h3",     "kind": "quantile", "horizon": 3, "q": 0.30, "select": "top30",
     "transform": "rank",
     "desc": "횡단면 rank + h3"},
    {"id": "TR_rank_h6",     "kind": "quantile", "horizon": 6, "q": 0.30, "select": "top30",
     "transform": "rank",
     "desc": "횡단면 rank + h6"},
    {"id": "TR_rank_h5_t40", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top40",
     "transform": "rank",
     "desc": "횡단면 rank + h5 + top40"},
    # ── 피처 풀 분리 (룩어헤드 검증) ─────────────────────────────────────────
    {"id": "PO_timevary_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "pool": "timevary",
     "desc": "시간가변 피처만 — 종목-상수(최신 스냅샷) 제외"},
    {"id": "PO_const_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "pool": "const",
     "desc": "종목-상수 피처만 — 룩어헤드 의심군 단독 성능"},
    {"id": "PO_timevary_rank_h5", "kind": "quantile", "horizon": 5, "q": 0.30, "select": "top30",
     "transform": "rank", "pool": "timevary",
     "desc": "시간가변 + 횡단면 rank"},
]


def transform_matrix(Xdf, dates, kind):
    """피처 행렬의 **날짜별 횡단면 변환**.

    근거(실측): 패널 210피처 중 34개가 날짜 내 종목간 분산이 0(시장 전체 동일값)이다.
    또 스케일이 피처마다 제각각(원/비율/지수)이다. 날짜별 rank/z-score 로 바꾸면
    시장레벨 성분이 사라지고 종목간 비교 가능한 형태가 된다 — 이 스택에서 측정된 적 없다.
    각 조회일의 정보만 쓰므로(같은 날 다른 종목) 미래 정보 누수가 아니다.
    """
    if kind in (None, "none"):
        return Xdf.values
    if kind == "rank":
        return Xdf.groupby(dates).rank(pct=True).values
    if kind == "zscore":
        g = Xdf.groupby(dates)
        mu = g.transform("mean")
        sd = g.transform("std").replace(0.0, np.nan)
        return ((Xdf - mu) / sd).fillna(0.0).values
    raise ValueError(f"unknown transform: {kind}")


def main():
    ap = argparse.ArgumentParser(description="라벨 설계 스윕 (확장창 walk-forward)")
    ap.add_argument("--panel", default="/app/app/models/wf/panel_420.npz")
    ap.add_argument("--days", type=int, default=420)
    ap.add_argument("--limit", type=int, default=50)
    # ── 유니버스 확장 옵션 (비우면 현행 기본값: KOSDAQ·코드순·최소 50일) ──
    # 유니버스를 바꿀 때는 --panel 파일명도 새로 줘라(캐시가 파일명으로만 구분된다).
    ap.add_argument("--market", default=None,
                    help="'KOSPI' | 'KOSDAQ' | 'all'(둘 다) | 미지정(현행 기본 KOSDAQ)")
    ap.add_argument("--since", default=None, help="유니버스 기준 시작일(YYYY-MM-DD)")
    ap.add_argument("--min-days", type=int, default=None, help="최소 거래일수")
    ap.add_argument("--min-value", type=float, default=None, help="일평균 거래대금 하한(원)")
    ap.add_argument("--order", default=None, choices=["code", "value"],
                    help="code(현행 알파벳순) | value(거래대금 상위)")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--wait-for-panel", type=int, default=0,
                    help="패널 캐시가 없으면 최대 N분 대기(다른 러너가 빌드 중일 때)")
    ap.add_argument("--only", default=None,
                    help="쉼표 구분 실험 id 만 실행 (예: LS_quant_q30_h5,LS_rel_h3)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.dry_run:
        for c in CONFIGS:
            print(c["id"], "|", c["desc"])
        return 0

    waited = 0
    while args.wait_for_panel and not os.path.exists(args.panel):
        if waited >= args.wait_for_panel * 60:
            print(f"패널 캐시 대기 초과({args.wait_for_panel}분): {args.panel}", flush=True)
            return 1
        time.sleep(30)
        waited += 30
        if waited % 300 == 0:
            print(f"  패널 대기 중... {waited // 60}분", flush=True)

    ml.log(f"label_sweep start KST={ml.now_kst().isoformat(timespec='seconds')} "
           f"panel={args.panel} folds={args.folds} seeds={args.seeds}")
    # 'all' → None(시장 필터 해제). 미지정(None)이면 build_panel 이 현행 기본값을 쓴다.
    uni = {}
    if args.market is not None:
        uni["market"] = None if args.market == "all" else args.market
    if args.since is not None:
        uni["since"] = args.since
    if args.min_days is not None:
        uni["min_days"] = args.min_days
    if args.min_value is not None:
        uni["min_value"] = args.min_value
    if args.order is not None:
        uni["order"] = args.order
    if uni:
        ml.log(f"universe 옵션: {uni}")
    df, names = W.build_panel(args.panel, args.limit, args.days, log=ml.log, **uni)
    base_names = [n for n in names if n in df.columns]
    all_dates = sorted(df["date"].astype(str).unique())
    ml.log(f"panel rows={len(df)} dates={len(all_dates)} ({all_dates[0]} ~ {all_dates[-1]})")

    import train_curated as tc
    tc.select_curated_features = lambda n, a=False: list(n)

    out_path = "/app/reports/overnight/wf_label_sweep.jsonl"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    results = []

    cfgs = CONFIGS
    if args.only:
        want = {s.strip() for s in args.only.split(",") if s.strip()}
        cfgs = [c for c in CONFIGS if c["id"] in want]
        if not cfgs:
            print(f"--only 에 해당하는 실험 없음: {sorted(want)}", flush=True)
            return 1
    ml.log(f"실행 실험 {len(cfgs)}개: {[c['id'] for c in cfgs]}")

    for cfg in cfgs:
        exp_id = cfg["id"]
        rec = {"exp": exp_id, "desc": cfg["desc"], "kind": cfg["kind"],
               "horizon": cfg["horizon"], "q": cfg["q"], "select": cfg["select"],
               "transform": cfg.get("transform"),
               "ts": ml.now_iso(), "status": "failed", "folds": {}}
        ml.log(f"=== {exp_id}: {cfg['desc']} ===")
        try:
            y = W.make_labels(df, cfg["kind"], cfg["horizon"], cfg["q"])
            d = df.copy()
            d["_y"] = y
            d = d[~pd.isna(d["_y"])]
            dd = sorted(d["date"].astype(str).unique())
            n = len(dd)
            step = n // (args.folds + 1)
            fold_means, fold_sizes = [], []
            for i in range(1, args.folds + 1):
                cut = dd[step * i - 1]
                nxt = dd[min(n - 1, step * (i + 1) - 1)]
                h = cfg["horizon"]
                purge = set(dd[max(0, step * i - h):step * i])
                tr = d[(d["date"] <= cut) & (~d["date"].isin(purge))]
                te = d[(d["date"] > cut) & (d["date"] <= nxt)]
                if min(len(tr), len(te)) < 100:
                    ml.log(f"  {exp_id} fold{i}: 표본 부족(tr={len(tr)} te={len(te)}), 건너뜀")
                    continue
                trd = tr["date"].astype(str).values
                ted = te["date"].astype(str).values
                tkind = cfg.get("transform")
                Xtr = np.nan_to_num(
                    transform_matrix(tr[base_names], trd, tkind).astype(np.float32), nan=0.0)
                ytr = tr["_y"].values.astype(int)
                Xte = np.nan_to_num(
                    transform_matrix(te[base_names], ted, tkind).astype(np.float32), nan=0.0)
                yte = te["_y"].values.astype(int)
                cols = np.std(Xtr, axis=0) > 0
                # ── 피처 풀 필터 (종목-상수 vs 시간가변) ─────────────────────────
                # 실측: top30 을 지배하는 피처(net_income, op_margin, roa, debt_ratio…)가
                # **종목당 값이 1개**(14개월 패널 전체에서 상수)다 → 최신 스냅샷을 과거 날짜에
                # 적용한 것이라면 룩어헤드이고, 측정 AUC 가 부풀려진다. 이 필터로 분리 측정한다.
                pool = cfg.get("pool")
                if pool in ("timevary", "const"):
                    nun = tr[base_names].groupby(tr["stock_code"].values).nunique()
                    is_const = (nun.max(axis=0).values <= 1)
                    pool_mask = is_const if pool == "const" else ~is_const
                    cols = cols & pool_mask
                fn = [f for f, m in zip(base_names, cols) if m]
                Xtr, Xte = Xtr[:, cols], Xte[:, cols]
                idx, sel_desc = W.subset(fn, cfg["select"], Xtr, ytr)
                sel = [fn[j] for j in idx]
                aucs = []
                for seed in range(args.seeds):
                    a, _m, _c, _e = ml.train_seed(
                        Xtr[:, idx], None, Xte[:, idx], ytr, None, yte, sel,
                        f"/app/app/models/wf/labelsweep_{exp_id}", seed,
                        W.BASE["recipe"]["lr"], W.BASE["recipe"]["depth"],
                        W.BASE["recipe"]["n_estimators"], True, None)
                    aucs.append(float(a))
                fold_means.append(float(np.mean(aucs)))
                fold_sizes.append((len(tr), len(te)))
                rec["folds"][f"fold{i}"] = {"train_rows": len(tr), "test_rows": len(te),
                                            "test_from": dd[step * i], "test_to": nxt,
                                            "mean": float(np.mean(aucs)),
                                            "n_features": len(sel)}
            if fold_means:
                rec["status"] = "ok"
                rec["auc_mean"] = float(np.mean(fold_means))
                rec["auc_std"] = float(np.std(fold_means))
                rec["n_rows_used"] = int(len(d))
                ml.log(f"  → {exp_id} AUC mean={rec['auc_mean']:.4f} "
                       f"std={rec['auc_std']:.4f} folds={len(fold_means)} rows={len(d)}")
        except Exception as e:  # noqa: BLE001
            rec["error"] = f"{type(e).__name__}: {e}"
            ml.log(f"  !! {exp_id} 실패: {rec['error']}")
        results.append(rec)
        with open(out_path, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    ok = [r for r in results if r.get("status") == "ok"]
    ok.sort(key=lambda r: -r["auc_mean"])
    print("\n=== 라벨 스윕 결과 (폴드 평균 기준) ===")
    for r in ok:
        print(f"  {r['exp']:18s} AUC {r['auc_mean']:.4f} ± {r['auc_std']:.4f} "
              f"| {r['desc']}")
    if ok:
        with open("/app/reports/overnight/wf_label_sweep_summary.json", "w") as f:
            json.dump({"finished_at": ml.now_iso(), "config": vars(args),
                       "best": ok[0], "results": results}, f,
                      ensure_ascii=False, indent=2)
        print(f"\nbest: {ok[0]['exp']} ({ok[0]['desc']}) AUC {ok[0]['auc_mean']:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

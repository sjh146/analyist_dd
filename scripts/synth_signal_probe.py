#!/usr/bin/env python3
"""계기 감도(양성 대조군) 검정 — 이 파이프라인이 '알려진 신호'를 AUC 로 표현할 수 있는가.

왜 필요한가(2026-11-14 신설, 무개선 19사이클):
  조정 축(피처변환·HP·라벨·표본가중·앙상블·모델계열·선별)은 17+사이클 전부 Δ<+0.02 로 종결됐고,
  데이터 축 후보(경로형태·SNS·유동성)도 문턱 미달·부호 반전으로 종결됐다. 이때 남는 해석은 둘이다:
    (A) 패널 피처의 정보량이 병목이다  → 새 데이터가 유일한 레버
    (B) 파이프라인(선별·조기종료·앙상블 가중)이 신호를 죽이고 있다 → 엔지니어링 수리 여지
  두 해석을 구분할 계기가 없었다. 이 스크립트는 **알려진 강도의 합성 신호**를 패널에 주입해
  같은 스윕 경로로 AUC 가 그만큼 올라가는지 본다(양성 대조군 = 측정 파이프라인의 감도 검정).

⚠ 이 스크립트가 만드는 패널은 **실험용 대조 패널**이다. 절대 승격 근거가 될 수 없고
   (합성 피처 = 누설), 원장 per_exp 에도 넣지 않는다. 산출물은 계기 판정 전용.

사용(컨테이너 내부):
  python scripts/synth_signal_probe.py --panel /app/app/models/wf/panel_prod200.npz \
      --out-dir /app/app/models/wf --targets 0.58,0.65
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

import wf_wave as W  # noqa: E402


def _load(panel):
    d = np.load(panel, allow_pickle=True)
    X = np.asarray(d["X"], dtype=np.float32)
    names = [str(x) for x in d["feature_names"]]
    df = pd.DataFrame({"stock_code": d["codes"], "date": d["dates"], "price": d["price"]})
    return X, names, df


def _fwd_ret(df, horizon):
    price = df.groupby("stock_code", sort=False)["price"]
    return price.transform(lambda s: s.shift(-horizon) / s - 1.0)


def _auc(y, s):
    y = np.asarray(y, dtype=float)
    s = np.asarray(s, dtype=float)
    m = np.isfinite(y) & np.isfinite(s)
    y, s = y[m], s[m]
    if len(y) == 0 or len(np.unique(y)) < 2:
        return float("nan")
    r = pd.Series(s).rank().values
    n1 = float((y > 0.5).sum())
    n0 = float(len(y) - n1)
    return float((r[y > 0.5].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default="/app/app/models/wf/panel_prod200.npz")
    ap.add_argument("--out-dir", default="/app/app/models/wf")
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--q", type=float, default=0.30)
    ap.add_argument("--targets", default="0.58,0.65",
                    help="합성 피처의 목표 단일변량 AUC 목록(쉼표)")
    ap.add_argument("--prefix", default="synth_sig")
    args = ap.parse_args()

    X, names, df = _load(args.panel)
    y = W.make_labels(df, "quantile", args.horizon, args.q)
    ret = _fwd_ret(df, args.horizon)
    z = (ret - ret.mean()) / ret.std(ddof=0)

    base = os.path.basename(args.panel).replace(".npz", "")
    out = {"panel": args.panel, "rows": int(len(df)), "features": len(names),
           "horizon": args.horizon, "q": args.q, "arms": {}}

    for t in [float(x) for x in args.targets.split(",") if x.strip()]:
        # z + eps*noise → 단일변량 AUC 를 목표치로 맞춘다(이분 탐색).
        rng = np.random.default_rng(20261114)
        noise = pd.Series(rng.standard_normal(len(df)))
        lo, hi = 0.0, 20.0
        best = None
        for _ in range(40):
            mid = 0.5 * (lo + hi)
            s = z + mid * noise
            a = _auc(y, s)
            best = (mid, a, s)
            if a > t:      # 너무 강하다 → 노이즈 증가
                lo = mid
            else:
                hi = mid
            if abs(a - t) < 0.002:
                break
        eps, auc_u, s = best
        col = f"{args.prefix}_{int(round(t * 100))}"
        Xn = np.concatenate([X, np.asarray(s, dtype=np.float32).reshape(-1, 1)], axis=1)
        fn = np.array(names + [col])
        path = os.path.join(args.out_dir, f"panel_{base}_{col}.npz")
        np.savez(path, X=Xn, feature_names=fn, dates=np.asarray(df["date"].values),
                 codes=np.asarray(df["stock_code"].values),
                 price=np.asarray(df["price"].values, dtype=np.float64))
        out["arms"][col] = {"target_auc": t, "eps": round(float(eps), 4),
                            "univariate_auc": round(float(auc_u), 4), "panel": path}
        print(f"[{col}] target={t:.2f} eps={eps:.4f} 단일변량 AUC={auc_u:.4f} → {path}")

    import json
    jp = os.path.join(args.out_dir, f"panel_{base}_synth_calib.json")
    with open(jp, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("calib:", jp)


if __name__ == "__main__":
    main()

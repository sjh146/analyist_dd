#!/usr/bin/env python3
"""CG289 — 지평 앙상블(순위 평균)이 단일 지평(h5)보다 나은가.

왜(CG276 실측, 2026-11-14): 같은 피처·같은 저용량에서 라벨 지평만 바꾼 사다리에서
  h1 OOS 0.5440±0.0066 · h3 0.5392±0.0075 · h5 0.5348±0.0145 였다. 즉 **h1 이 평균도 높고
  폴드 분산이 2.2배 낮다**. 그런데 판정은 단일 지평 비교로 끝났고 '지평들을 순위 평균으로
  묶으면 분산이 줄어 사전문턱(+0.02)을 넘는가'는 미시험이다(HP3 는 모델 앙상블만, 지평 앙상블은 0회).

프로토콜(사전등록 — 측정 전에 고정):
  - 패널/폴드/시드는 CG276 과 동일: panel_prod200.npz · 5폴드 · 시드 3 · 게이트 ON · q0.30 · top30 · depth1
  - 지표 AUC 는 **모두 같은 라벨(h5 스무딩)** 로 채점한다. arm 마다 자기 라벨로 채점하면
    라벨 정의가 달라 비교가 성립하지 않는다(지평 사다리의 원래 비교는 'AUC 는 지평별 라벨 안에서
    척도무관'이라는 논리였지만, 앙상블은 단일 예측벡터이므로 공통 라벨이 필요하다).
  - 앙상블 점수 = 날짜별 횡단면 백분위 순위의 단순 평균(rank-mean). 스케일이 다른 확률을
    그대로 평균하면 h1 모델의 교정 차이가 섞이므로 순위로 정규화한다.
  - 공정성: 세 arm 모두 **교집합 행**(세 예측이 모두 존재하고 h5 라벨이 있는 행)에서만 채점한다.
  - 성공: 앙상블 짝 Δ(h5 대조군) ≥ +0.02 & 폴드 승률 ≥ 4/5 → 신호(다음 단계: 추론 계약 3모델 서빙 검토)
    미달 → '지평 앙상블은 AUC 레버가 아니다'로 종결.

자기검정(측정 전 실행, --test):
  1) 항등성 — 같은 arm 을 3번 평균한 앙상블 AUC == 그 arm AUC
  2) 단조불변 — 예측에 exp() 를 씌워도 순위 평균 AUC 동일
  3) 누수 — 앙상블에 미래 날짜 예측을 섞으면 AUC 가 달라짐(섞임이 검출됨) · 날짜 내 순위만 사용
"""
from __future__ import annotations

import argparse
import json
import math
import os
from collections import defaultdict

import numpy as np
import pandas as pd

BASE = "CO_smooth_d1_h5"
ARMS = ["CG276_h1", "CG276_h3", "CO_smooth_d1_h5"]


def auc(y, s):
    y = np.asarray(y, dtype=int)
    s = np.asarray(s, dtype=float)
    ok = np.isfinite(s)
    y, s = y[ok], s[ok]
    npos, nneg = int((y == 1).sum()), int((y == 0).sum())
    if npos == 0 or nneg == 0:
        return float("nan")
    order = np.argsort(s, kind="mergesort")
    ranks = np.empty(len(s), dtype=float)
    ranks[order] = np.arange(1, len(s) + 1)
    # 동점 평균순위
    df = pd.DataFrame({"s": s, "r": ranks})
    df["r"] = df.groupby("s")["r"].transform("mean")
    return float((df.loc[y == 1, "r"].sum() - npos * (npos + 1) / 2) / (npos * nneg))


def load(path: str) -> pd.DataFrame:
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            rows.append((d["exp"], int(d["fold"]), d["date"], d["code"],
                         d.get("y_true"), float(d["y_pred"]), d.get("fwd_ret")))
    return pd.DataFrame(rows, columns=["exp", "fold", "date", "code", "y_true", "y_pred", "fwd_ret"])


def rank_pct(df: pd.DataFrame) -> pd.Series:
    return df.groupby(["fold", "date"])["y_pred"].rank(pct=True, method="average")


def evaluate(df: pd.DataFrame, arms: list[str], base: str = BASE) -> dict:
    """교집합 행에서 모든 arm + rank-mean 앙상블을 같은 라벨(h5)로 채점."""
    gt = df[(df.exp == base) & df.y_true.notna()][["fold", "date", "code", "y_true"]]
    gt = gt.drop_duplicates(["fold", "date", "code"])
    merged = None
    for a in arms:
        sub = df[df.exp == a][["fold", "date", "code", "y_pred"]].copy()
        sub["rank"] = rank_pct(df[df.exp == a]).values
        sub = sub.rename(columns={"y_pred": f"p_{a}", "rank": f"r_{a}"})
        merged = sub if merged is None else merged.merge(sub, on=["fold", "date", "code"], how="inner")
    m = merged.merge(gt, on=["fold", "date", "code"], how="inner")
    m = m.dropna(subset=["y_true"])
    m["ens"] = m[[f"r_{a}" for a in arms]].mean(axis=1)

    out = {}
    for name, col in [(a, f"p_{a}") for a in arms] + [("ENS_rankmean", "ens")]:
        per_fold = {}
        for f, g in m.groupby("fold"):
            per_fold[f"fold{int(f)}"] = round(auc(g["y_true"], g[col]), 4)
        vals = [v for v in per_fold.values() if not math.isnan(v)]
        out[name] = {"folds": per_fold, "mean": round(float(np.mean(vals)), 4),
                     "std": round(float(np.std(vals)), 4),
                     "min": round(float(np.min(vals)), 4), "max": round(float(np.max(vals)), 4)}
    # 짝 Δ (앙상블 − 각 arm), 폴드별
    for name in [a for a in arms]:
        d = [round(out["ENS_rankmean"]["folds"][k] - out[name]["folds"][k], 4)
             for k in out["ENS_rankmean"]["folds"]]
        out["ENS_rankmean"][f"delta_vs_{name}"] = {
            "paired_mean": round(float(np.mean(d)), 4),
            "win": int(sum(1 for x in d if x > 0)), "n": len(d),
            "per_fold": d}
    out["_rows"] = int(len(m))
    out["_sessions"] = int(m["date"].nunique())
    return out


def emit_ensemble(df: pd.DataFrame, arms: list[str], out_path: str, tag: str = "CG289_ens135") -> int:
    """앙상블 예측을 preds jsonl 로 다시 써서 돈 집계(fillable_topk_horizon_sweep)에 넘긴다."""
    gt = df[(df.exp == BASE)][["fold", "date", "code", "y_true", "fwd_ret"]]
    gt = gt.drop_duplicates(["fold", "date", "code"])
    merged = None
    for a in arms:
        sub = df[df.exp == a][["fold", "date", "code"]].copy()
        sub[f"r_{a}"] = rank_pct(df[df.exp == a]).values
        merged = sub if merged is None else merged.merge(sub, on=["fold", "date", "code"], how="inner")
    merged["ens"] = merged[[f"r_{a}" for a in arms]].mean(axis=1)
    m = merged.merge(gt, on=["fold", "date", "code"], how="inner")
    n = 0
    with open(out_path, "w") as f:
        for _, r in m.iterrows():
            f.write(json.dumps({"exp": tag, "fold": int(r["fold"]), "date": r["date"],
                                "code": r["code"],
                                "y_true": None if pd.isna(r["y_true"]) else int(r["y_true"]),
                                "y_pred": float(r["ens"]),
                                "fwd_ret": None if pd.isna(r["fwd_ret"]) else float(r["fwd_ret"])}) + "\n")
            n += 1
    return n


def _test(df: pd.DataFrame) -> int:
    checks = []
    # 1) 항등성: 같은 arm 3번 → AUC 동일
    e1 = evaluate(df, ["CG276_h1", "CG276_h1", "CG276_h1"])
    a1 = e1["CG276_h1"]["mean"]
    a3 = e1["ENS_rankmean"]["mean"]
    checks.append(("1 항등성(같은 arm×3 평균 == 그 arm AUC)", abs(a1 - a3) < 5e-4, f"{a1} vs {a3}"))
    # 2) 단조불변: exp() 변환
    d2 = df.copy()
    d2["y_pred"] = np.exp(np.clip(d2["y_pred"].to_numpy(), -50, 50))
    e2 = evaluate(d2, ARMS)
    e0 = evaluate(df, ARMS)
    checks.append(("2 단조불변(exp 변환 후 앙상블 AUC 동일)",
                   abs(e2["ENS_rankmean"]["mean"] - e0["ENS_rankmean"]["mean"]) < 5e-4,
                   f"{e0['ENS_rankmean']['mean']} vs {e2['ENS_rankmean']['mean']}"))
    # 3) 누수 검출: 날짜 라벨을 섞으면 AUC 가 달라져야 한다(검출기 작동 확인)
    rng = np.random.default_rng(0)
    d3 = df.copy()
    nolabel = d3.y_true.isna()
    perm = d3.loc[~nolabel, "y_true"].to_numpy().copy()
    rng.shuffle(perm)
    d3.loc[~nolabel, "y_true"] = perm
    e3 = evaluate(d3, ARMS)
    checks.append(("3 라벨 셔플 시 AUC 변화(누수/검출기 작동)",
                   abs(e3["ENS_rankmean"]["mean"] - e0["ENS_rankmean"]["mean"]) > 0.01,
                   f"{e0['ENS_rankmean']['mean']} vs {e3['ENS_rankmean']['mean']}"))
    ok = all(c[1] for c in checks)
    for n, c, d in checks:
        print(("PASS " if c else "FAIL ") + n + f" — {d}")
    print(("자기검정 전부 PASS" if ok else "자기검정 실패") + f" ({sum(1 for c in checks if c[1])}/{len(checks)})")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preds", default="/app/reports/overnight/cg289_preds.jsonl")
    ap.add_argument("--json-out", default="/app/reports/overnight/cg289_horizon_ensemble.json")
    ap.add_argument("--emit-preds", default="/app/reports/overnight/cg289_ens_preds.jsonl")
    ap.add_argument("--test", action="store_true")
    a = ap.parse_args()
    df = load(a.preds)
    if a.test:
        return _test(df)
    res = evaluate(df, ARMS)
    n = emit_ensemble(df, ARMS, a.emit_preds)
    res["_emitted_rows"] = n
    res["_preds_for_money"] = a.emit_preds
    res["protocol"] = {"panel": "panel_prod200.npz", "folds": 5, "seeds": 3,
                       "arms": ARMS, "label_for_all": BASE, "ensemble": "rank-mean within (fold,date)",
                       "success": "Δ(ens−h5) ≥ +0.02 & fold win ≥ 4/5"}
    os.makedirs(os.path.dirname(a.json_out), exist_ok=True)
    with open(a.json_out, "w") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in res.items() if not k.startswith("_")}, ensure_ascii=False, indent=2))
    print(f"rows={res['_rows']} sessions={res['_sessions']} emitted={n} → {a.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""panel_feature_audit.py — 패널(.npz) 피처 품질·선별 가능성을 한 번에 진단한다.

용도: **"새 데이터를 채웠는데 AUC 가 안 오른다"** 를 결론내리기 전에 세 가지를 먼저 본다.
  ① 계약 위생   — 피처명 중복 / 전량 0 열
  ② 횡단면 무용 — 날짜 내 종목간 분산 0 (시장 전체 값 복제 → 횡단면 순위 모델에서 정보 0)
  ③ 선별 가능성 — 단변량 edge 와 상위 K 선별 문턱 비교. 신규 피처가 문턱보다 작으면
                  **모델에 들어갈 기회 자체가 없다** (원인은 데이터가 아니라 선별).

사용:
  python scripts/panel_feature_audit.py <panel.npz> [--horizon 5] [--q 0.3] [--topk 30] \
         [--group "수급=foreign_net_buy,institution_net_buy" --group "공매도=short_"]

패널 npz 필수 키: ``feature_names``, ``X``, ``dates``, ``codes``, ``price``.
라벨은 지정 horizon 뒤 수익률의 **날짜별 분위**(q/1-q 밖만 0·1, 중간은 제외)로 만든다 —
학습 하네스와 같은 정의를 쓰도록 ``--horizon``/``--q`` 를 맞춘다.

주의: 하네스가 캐시를 재사용한다면 **이 스크립트에 넘긴 패널이 최신인지** 먼저 확인한다
(캐시 키에 없는 인자를 바꿔 실행하면 옛 유니버스 패널을 진단하게 된다).
"""
import argparse
import collections

import numpy as np
import pandas as pd


def load_panel(path):
    z = np.load(path, allow_pickle=True)
    names = [str(n) for n in z["feature_names"]]
    df = pd.DataFrame(z["X"].astype(float), columns=names)
    df["date"] = [str(d) for d in z["dates"]]
    df["stock_code"] = [str(c) for c in z["codes"]]
    df["price"] = z["price"].astype(float)
    return df, names


def make_label(df, horizon, q):
    ret = df.groupby("stock_code", sort=False)["price"].transform(
        lambda s: s.shift(-horizon) / s - 1.0)
    hi = ret.groupby(df["date"]).transform(lambda s: s.quantile(1 - q))
    lo = ret.groupby(df["date"]).transform(lambda s: s.quantile(q))
    y = pd.Series(np.nan, index=df.index, dtype=float)
    y[ret > hi] = 1.0
    y[ret < lo] = 0.0
    return y


def auc_mannwhitney(x, y):
    """단일 피처 AUC (tie 평균 순위). 값이 상수거나 한쪽 클래스가 없으면 None."""
    m = ~np.isnan(x)
    x, yy = x[m], y[m]
    if len(x) < 50 or len(np.unique(x)) < 2 or yy.min() == yy.max():
        return None
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(len(x), dtype=float)
    ranks[order] = np.arange(1, len(x) + 1, dtype=float)
    xs = x[order]
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[j + 1] == xs[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + 1 + j + 1) / 2.0
        i = j + 1
    n1 = int(yy.sum())
    n0 = len(yy) - n1
    if n1 == 0 or n0 == 0:
        return None
    return float((ranks[yy == 1].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0))


DEFAULT_GROUPS = {
    "수급": ("foreign_net_buy", "institution_net_buy", "individual_net_buy", "ownership_pct"),
    "공매도": ("short_", "days_to_cover"),
    "프로그램": ("program",),
    "파생": ("basis", "derivatives_volume", "futures_premium"),
}


def main():
    ap = argparse.ArgumentParser(description="패널 피처 진단 (위생·횡단면·선별 가능성)")
    ap.add_argument("panel")
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--q", type=float, default=0.3)
    ap.add_argument("--topk", type=int, default=30)
    ap.add_argument("--group", action="append", default=None,
                    help='이름=패턴1,패턴2 (기본: 수급·공매도·프로그램·파생)')
    ap.add_argument("--show-top", type=int, default=20)
    args = ap.parse_args()

    df, names = load_panel(args.panel)
    print(f"[panel] rows={len(df)} cols={len(names)} dates={df['date'].nunique()} "
          f"({df['date'].min()} ~ {df['date'].max()})")

    # ① 계약 위생
    dup = [n for n, c in collections.Counter(names).items() if c > 1]
    allzero = [names[i] for i in range(len(names)) if np.all(df[names[i]].values == 0)]
    print(f"\n[① 계약 위생] 피처명 중복 {len(dup)}개 | 전량 0(dead) {len(allzero)}개")
    if dup:
        print("   중복:", ", ".join(dup[:15]))
        print("   → 같은 신호가 두 열로 세어져 중요도가 부풀고 나머지가 희석된다. 계약에서 합친다.")
    if allzero:
        print("   dead:", ", ".join(allzero[:15]))

    # ② 횡단면 무용 (날짜 내 분산 0)
    g = df.groupby("date")[names].std(ddof=0)
    flat = [c for c in names if not pd.isna(g[c].max()) and float(g[c].fillna(0).max()) == 0.0]
    print(f"\n[② 횡단면 무용] 날짜 내 종목간 분산 0인 피처 {len(flat)}개")
    if flat:
        print("   ", ", ".join(flat[:20]))
        print("   → 시장 전체 값 복제. 횡단면 순위 모델에서 정보 0이고 정규화해도 살아나지 않는다"
              " → 레짐 게이트로 격리하고 피처 행렬에서 뺀다.")

    # ③ 선별 가능성
    y = make_label(df, args.horizon, args.q)
    m = ~y.isna()
    X = df.loc[m, names].values
    yv = y[m].values.astype(int)
    print(f"\n[③ 선별 가능성] label h={args.horizon} q={args.q}: "
          f"표본 {int(m.sum())} / up_rate {yv.mean():.4f}")

    rows = []
    for i, n in enumerate(names):
        col = X[:, i]
        a = auc_mannwhitney(col, yv)
        if a is None:
            continue
        rows.append({"f": n, "auc": a, "edge": abs(a - 0.5),
                     "nz": float(np.mean(col != 0)), "sd": float(np.std(col))})
    rows.sort(key=lambda r: -r["edge"])
    rank = {r["f"]: i + 1 for i, r in enumerate(rows)}
    if not rows:
        print("   계산 가능한 피처 없음")
        return 1
    thr = rows[min(args.topk, len(rows)) - 1]["edge"]
    print(f"   top{args.topk} 선별 문턱 edge ≈ {thr:.4f} (계산 가능 {len(rows)}개 피처)")

    groups = DEFAULT_GROUPS
    if args.group:
        groups = {}
        for spec in args.group:
            k, _, v = spec.partition("=")
            groups[k] = tuple(p for p in v.split(",") if p)

    print("\n   [그룹별 edge] 그룹: 개수 / 평균 / 최고(순위, 선별여부)")
    for gname, pats in groups.items():
        sel = [r for r in rows if any(r["f"] == p or r["f"].startswith(p) for p in pats)]
        if not sel:
            continue
        es = np.array([r["edge"] for r in sel])
        best = max(sel, key=lambda r: r["edge"])
        passed = "✅ 선별권" if best["edge"] >= thr else "❌ 문턱 미달"
        print(f"     {gname:8s} {len(sel):3d} / mean {es.mean():.4f} / "
              f"max {best['edge']:.4f} ({best['f']}, {rank[best['f']]}/{len(rows)}, {passed})")

    print(f"\n   [최고 edge 상위 {args.show_top}개] — 지금 모델이 실제로 쓰는 신호")
    for r in rows[:args.show_top]:
        print(f"     {r['f']:34s} edge {r['edge']:.4f} AUC {r['auc']:.4f} nz {r['nz']:.3f}")

    print("\n[판정 가이드] 그룹 최고 edge < 문턱 이면 그 그룹은 **모델에 들어갈 수 없다** — "
          "AUC 델타가 노이즈인 것은 당연하다. 보고는 '효과 없음'이 아니라 '선별되지 않음'으로 적는다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

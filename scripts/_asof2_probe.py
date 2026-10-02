"""CG65 후속 진단: 수리 패널(panel_420_asof2)의 재무 피처가 정말 시점정합인가.

질문: 스크린이 value_per/value_pbr/quality_roe/roe/per_current/pbr_current 를
'market_level' 로 분류했다(= 날짜별 횡단면 유니크 1개?). 그렇다면 as-of 수리가
'종목별 조인' 을 깨고 '모든 종목에 같은 값' 을 넣은 것일 수 있다.

측정(각 피처): 종목별 유니크(중앙값), 날짜별 유니크(중앙값), 결측률, 처음 3행 샘플.
읽기 전용.
"""
import json
import numpy as np

for p in ["/app/app/models/wf/panel_420_asof2.npz", "/app/app/models/wf/panel_420_asofpatch.npz"]:
    print("=" * 70)
    print("PANEL", p)
    try:
        z = np.load(p, allow_pickle=True)
    except Exception as e:  # noqa: BLE001
        print("  ERR", e)
        continue
    print("  keys:", list(z.keys()))
    X = z["X"] if "X" in z else None
    feats = list(z["feature_names"]) if "feature_names" in z else None
    codes = list(z["codes"]) if "codes" in z else None
    dates = list(z["dates"]) if "dates" in z else None
    print("  X", None if X is None else X.shape, "| feats", None if feats is None else len(feats),
          "| codes", None if codes is None else len(codes),
          "| dates", None if dates is None else len(set(map(str, dates))))
    if X is None or feats is None:
        continue
    import collections
    idx = {f: i for i, f in enumerate(feats)}
    targets = ["value_per", "value_pbr", "value_ncav", "quality_roa", "quality_score",
               "roe", "quality_roe", "per_current", "pbr_current", "operating_profit",
               "net_income", "days_to_cover", "foreign_ownership_pct", "atr_pct"]
    codes_a = np.array([str(c) for c in codes])
    dates_a = np.array([str(d) for d in dates])
    for t in targets:
        if t not in idx:
            print(f"  {t:26s} MISSING")
            continue
        col = X[:, idx[t]].astype(float)
        # per-stock unique
        su, du = [], []
        for c in np.unique(codes_a):
            m = codes_a == c
            su.append(len(np.unique(np.round(col[m], 6))))
        for d in np.unique(dates_a):
            m = dates_a == d
            du.append(len(np.unique(np.round(col[m], 6))))
        nz = float((col != 0).mean())
        print(f"  {t:26s} nz={nz:6.3f} stock_uniq_med={int(np.median(su)):3d} date_uniq_med={int(np.median(du)):3d} "
              f"date_uniq_max={int(np.max(du)):3d} sample={np.round(col[:4],4).tolist()}")

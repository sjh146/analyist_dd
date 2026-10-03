#!/usr/bin/env python3
"""청정 패널(prod200) 피처 스크린 요약 — 선별 문턱(|AUC−0.5|≥0.044) 통과 피처 계수 (schema: rows)."""
import json

P = "/home/jhshi/analyist_dd/services/xgboost-ml/reports/panel_screen_prod200.json"
d = json.load(open(P, encoding="utf-8"))
rows = d["rows"]
THR = 0.044
tv = [r for r in rows if not r.get("stock_constant") and not r.get("market_level")]
tv_thr = [r for r in tv if (r.get("auc_abs_edge") or 0) >= THR]
print(f"총 {len(rows)} · 시간가변(비종목상수·비시장레벨) {len(tv)} · "
      f"그중 |AUC−0.5|≥{THR} = {len(tv_thr)}개")

def show(rs, n=12):
    for r in sorted(rs, key=lambda x: -(x.get("auc_abs_edge") or 0))[:n]:
        print(f"  {r['auc_abs_edge']:.4f} auc={r['auc']:.4f} {r['feature']:34s} "
              f"fill={r['fill']:.4f} n_obs={r['n_obs']} ic_t={r['ic_t']}")

print("\n[시간가변 & 문턱 통과]")
show(tv_thr)
print("\n[문턱 통과 전체(시간가변+종목상수+시장레벨) 상위 12]")
show([r for r in rows if (r.get("auc_abs_edge") or 0) >= THR])

print("\n[fill ≥ 0.10 이면서 문턱 통과]")
show([r for r in tv_thr if r.get("fill", 0) >= 0.10])

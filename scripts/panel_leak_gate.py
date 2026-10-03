#!/usr/bin/env python3
"""패널 누수 지문 사전 게이트 — 실험 전에 패널이 as-of 청정한지 판정한다 (CG85).

WHY(실측 2026-10-03): 하드 판정규칙 ③(누수 게이트)은 "종목 상수 피처가 선별을 지배하면 중단"인데,
그 지문을 **실험 전에** 확인하는 도구가 없었다. 실측으로 패널 함대를 갈라 보니 정확히
`as-of 수리 시점(2026-10-02 CG63 getter 수정)` 전후로 갈렸다:

  CLEAN (수리 후 빌드)  panel_420_asof3 · panel_420_asof4ev · panel_prod200
                        → value_pbr 종목당유니크 중앙 182 · value_per 157 · quality_roa 3
  LEAKY (수리 전 빌드)  panel_150u · panel_995 · panel_420_asofpatch · panel_420_asof2(부분)
                        → 유니크 중앙 1 (예: asof2 의 value_pbr=2/ value_per=2 — value_* getter 미수리)

지문: `FactorFeatures`/`QualityScorer` 의 `date` 없는 getter 는 빌드 시점 최신 보고서를 모든 과거
행에 넣는다 → 그 컬럼이 **종목 내 상수(유니크 1)** 가 되고, 단일피처 AUC 가 0.55 대로 올라가
풀링 edge top-k 선별 슬롯을 먹는다(누수 지문 · CG63). 따라서 '실험 전 청정성 판정'은 필수다.

⚠ 읽기 전용. `--strict` 를 주면 누수 패널이 있을 때 rc=1(사전 게이트), 없으면 rc=0.
   기본 rc=0(구동기 판정용 — 원장에 판정을 남긴다).

용법:
  docker exec stock_xgboost_ml python /app/scripts/panel_leak_gate.py \\
      --json-out /app/reports/overnight/cg85_panel_gate.json
"""
import argparse
import collections
import glob
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np

# as-of 누수에 취약한 재무 getter 계열(= date 인자가 없던 경로). 이들이 '비영인데 종목 상수'면
# 빌드 시점 최신 보고서가 과거 행에 들어간 룩어헤드 지문이다.
OFFENDERS = [
    "value_per", "value_pbr", "value_psr", "value_pcr", "value_ncav",
    "roe", "per_current", "pbr_current",
    "debt_ratio", "op_margin", "net_margin", "revenue", "operating_profit", "net_income",
    "quality_roa", "quality_score", "quality_f_score", "quality_asset_growth",
]
# 이 비영 비율 미만이면 '데이터가 없어 상수'일 뿐 누수라 단정하지 않는다(예: value_ncav 0%).
MIN_NONZERO_PCT = 5.0
DEFAULT_DIR = "/app/app/models/wf"


def classify(names, X, codes, dates):
    """패널 하나의 누수 판정 → dict(verdict, offenders, leaky_columns, ...)."""
    idx_of = {str(n): i for i, n in enumerate(names)}
    offenders, leaky = {}, []
    for name in OFFENDERS:
        i = idx_of.get(name)
        if i is None:
            continue
        col = X[:, i]
        nz = 100.0 * float(np.mean(col != 0))
        per = collections.defaultdict(set)
        for r, c in enumerate(codes):
            per[c].add(round(float(col[r]), 6))
        uniq = sorted(len(v) for v in per.values())
        med = uniq[len(uniq) // 2] if uniq else 0
        rec = {"median_unique": med, "min_unique": uniq[0] if uniq else 0,
               "max_unique": uniq[-1] if uniq else 0, "nonzero_pct": round(nz, 2)}
        offenders[name] = rec
        if med <= 1 and nz >= MIN_NONZERO_PCT:
            leaky.append(name)
    out = {
        "rows": int(X.shape[0]), "cols": len(names), "n_stocks": len(set(codes)),
        "date_min": min(dates) if dates else None, "date_max": max(dates) if dates else None,
        "offenders": offenders, "leaky_columns": sorted(leaky),
        "verdict": "LEAKY" if leaky else "CLEAN",
    }
    return out


def scan(path):
    z = np.load(path, allow_pickle=True)
    names = [str(n) for n in z["feature_names"]]
    X = z["X"]
    codes = [str(c) for c in z["codes"]]
    dates = [str(d)[:10] for d in z["dates"]]
    return classify(names, X, codes, dates)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=DEFAULT_DIR)
    ap.add_argument("--panels", default="", help="쉼표구분 파일명(기본: dir 안 전부)")
    ap.add_argument("--json-out", default="")
    ap.add_argument("--strict", action="store_true", help="누수 패널이 있으면 rc=1")
    a = ap.parse_args()

    if a.panels:
        files = [f.strip() for f in a.panels.split(",") if f.strip()]
        paths = [os.path.join(a.dir, f) for f in files]
    else:
        paths = sorted(glob.glob(os.path.join(a.dir, "*.npz")))

    panels, clean, leaky_names, missing = {}, [], [], []
    for p in paths:
        fn = os.path.basename(p)
        if not os.path.exists(p):
            missing.append(fn)
            continue
        try:
            r = scan(p)
        except Exception as e:  # pragma: no cover
            panels[fn] = {"verdict": "ERROR", "error": str(e)}
            continue
        panels[fn] = r
        (clean if r["verdict"] == "CLEAN" else leaky_names).append(fn)
        print(f"== {fn} rows={r['rows']} cols={r['cols']} 종목 {r['n_stocks']}"
              f" {r['date_min']}~{r['date_max']} → {r['verdict']}")
        for n in r["leaky_columns"]:
            o = r["offenders"][n]
            print(f"     누수지문 {n:20s} 종목당유니크 중앙 {o['median_unique']}"
                  f" | 비영 {o['nonzero_pct']:6.2f}%")

    result = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        "panel_dir": a.dir, "min_nonzero_pct": MIN_NONZERO_PCT,
        "n_panels": len(panels), "n_clean": len(clean), "n_leaky": len(leaky_names),
        "clean": sorted(clean), "leaky": sorted(leaky_names), "missing": missing,
        "panels": panels,
    }
    print(f"\n청정 {len(clean)}개: {sorted(clean)}")
    print(f"누수 {len(leaky_names)}개: {sorted(leaky_names)}  ← 실험에 쓰지 말 것(수리 후 재빌드 필요)")
    if missing:
        print(f"없음: {missing}")

    if a.json_out:
        tmp = a.json_out + ".tmp"
        os.makedirs(os.path.dirname(a.json_out) or ".", exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        os.replace(tmp, a.json_out)
        print(f"\n요약 저장: {a.json_out}")
    if a.strict and leaky_names:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

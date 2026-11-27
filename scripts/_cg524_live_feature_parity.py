#!/usr/bin/env python3
"""CG524 — 배포 추론 경로의 피처 정합성 감사(읽기 전용).

WHY: 배포 챔피언의 라이브 순서정보가 무작위 수준(spearman ρ −0.103, CG523)이고
최근창 로버스트도 0.4742(CG510)인데, 같은 모델 계열이 학습 패널에서는 OOS 0.55 를 낸다.
가장 싼 설명 후보는 '추론 경로가 학습 때와 다른 피처 벡터를 만든다'이다
(predictor.py L210-213 은 `features.get(f, 0.0)` — 없는 피처를 조용히 0 으로 채운다).

측정:
  A) 계약 정합: champion/feature_names.json(학습 시 이름 173) 중 FeaturePipeline 이
     실제로 산출하지 못하는 이름의 수 = 추론 시 0.0 으로 대체되는 피처 수.
  B) 값 정합: 같은 (code, date) 에 대해 ①추론 경로 build_features 결과와
     ②학습 패널(npz) 행을 피처별로 비교 — 일치율·최대괴리·부호상관.
  C) 라이브 벡터의 0 비율 vs 패널 행의 0 비율.
  횡단면 Spearman(코드 축)이 정합성의 핵심 지표다.

함정(실측 2026-11-27): FeaturePipeline() 을 pg_conn 없이 만들면 가격 피처가 전량 0 이 되어
0 비율 95%·불일치 69% 라는 거짓 경보가 난다. 서비스 배선(main.py L37-38)처럼
FeaturePipeline(pg_conn=PostgresStorage()._get_conn()) 으로 만들어야 한다.

출력: /app/reports/overnight/cg524_live_feature_parity.json (+ stdout 요약)
사용: docker exec stock_xgboost_ml python /app/scripts/_cg524_live_feature_parity.py --n-codes 8
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time

import numpy as np


def spearman(a, b):
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    ok = np.isfinite(a) & np.isfinite(b)
    a, b = a[ok], b[ok]
    if len(a) < 4 or np.all(a == a[0]) or np.all(b == b[0]):
        return None
    ra = np.argsort(np.argsort(a)).astype(float)
    rb = np.argsort(np.argsort(b)).astype(float)
    ra -= ra.mean()
    rb -= rb.mean()
    den = math.sqrt((ra ** 2).sum() * (rb ** 2).sum())
    return float((ra * rb).sum() / den) if den else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default="/app/app/models/wf/panel_200liq_short.npz")
    ap.add_argument("--champion-names", default="/app/app/models/champion/feature_names.json")
    ap.add_argument("--date", default=None, help="패널 날짜(기본: 패널 최근일)")
    ap.add_argument("--n-codes", type=int, default=8)
    ap.add_argument("--out", default="/app/reports/overnight/cg524_live_feature_parity.json")
    args = ap.parse_args()

    d = np.load(args.panel, allow_pickle=True)
    X = np.asarray(d["X"], dtype=np.float64)
    fn = [str(x) for x in d["feature_names"]]
    dates = [str(x) for x in d["dates"]]
    codes = [str(x) for x in d["codes"]]
    udates = sorted(set(dates))
    target = args.date or udates[-1]

    champ = [str(x) for x in json.load(open(args.champion_names))]
    from app.feature_engine.feature_pipeline import FeaturePipeline  # noqa: E402

    pg = None
    try:
        from app.storage.postgres_storage import PostgresStorage  # noqa: E402
        pg = PostgresStorage()._get_conn()
    except Exception as e:  # noqa: BLE001
        print(f"WARN: PostgresStorage 연결 실패 — pg_conn 없이 진행: {type(e).__name__}: {e}")
    fp = FeaturePipeline(pg_conn=pg)
    pipe_names = [str(x) for x in fp.get_feature_names()]

    idx = [i for i, x in enumerate(dates) if x == target]
    step = max(1, len(idx) // args.n_codes)
    pick = idx[::step][: args.n_codes]

    out = {
        "panel": args.panel,
        "panel_date": target,
        "panel_dates": [udates[0], udates[-1]],
        "panel_rows": int(len(dates)),
        "n_codes_panel_at_date": len(idx),
        "champion_n_features": len(champ),
        "pipeline_n_names_declared": len(pipe_names),
        "champ_names_missing_from_pipeline_declaration": sorted(set(champ) - set(pipe_names)),
        "pipeline_names_not_in_champion": sorted(set(pipe_names) - set(champ)),
        "rows": [],
    }

    live_by_code: dict[str, dict] = {}
    panel_by_code: dict[str, dict] = {}

    for i in pick:
        code = codes[i]
        t0 = time.time()
        row = {"code": code, "elapsed_s": None}
        try:
            f = fp.build_features(code, target)
            row["n_effective_keys"] = len(f)
            row["champ_names_unproduced_at_runtime"] = sorted(set(champ) - set(f))
        except Exception as e:  # noqa: BLE001
            row["error"] = f"{type(e).__name__}: {e}"[:300]
            row["elapsed_s"] = round(time.time() - t0, 1)
            out["rows"].append(row)
            continue
        row["elapsed_s"] = round(time.time() - t0, 1)
        panel_row = {k: float(v) for k, v in zip(fn, X[i])}
        common = [k for k in fn if k in f]
        live_vec, pan_vec = [], []
        n_zero_live = n_zero_pan = n_diff = 0
        diffs = {}
        for k in common:
            lv, pv = float(f[k]), panel_row[k]
            live_vec.append(lv)
            pan_vec.append(pv)
            if lv == 0.0 or abs(lv) < 1e-12:
                n_zero_live += 1
            if pv == 0.0 or abs(pv) < 1e-12:
                n_zero_pan += 1
            if np.isfinite(lv) and np.isfinite(pv) and not math.isclose(lv, pv, rel_tol=1e-6, abs_tol=1e-9):
                n_diff += 1
                scale = max(abs(pv), 1e-9)
                diffs[k] = round(abs(lv - pv) / scale, 6)
        row.update({
            "n_common_features": len(common),
            "n_feature_values_differ": n_diff,
            "frac_feature_values_differ": round(n_diff / max(1, len(common)), 4),
            "zero_frac_live": round(n_zero_live / max(1, len(common)), 4),
            "zero_frac_panel": round(n_zero_pan / max(1, len(common)), 4),
            "top_rel_diffs": dict(sorted(diffs.items(), key=lambda kv: -kv[1])[:8]),
        })
        live_by_code[code] = dict(zip(common, live_vec))
        panel_by_code[code] = dict(zip(common, pan_vec))
        out["rows"].append(row)

    cs = {}
    for k in fn:
        a = [live_by_code[c].get(k) for c in live_by_code]
        b = [panel_by_code[c].get(k) for c in panel_by_code]
        if any(v is None for v in a):
            continue
        r = spearman(a, b)
        if r is not None:
            cs[k] = r
    vals = np.array(list(cs.values()), dtype=float)
    out["cross_section_agreement"] = {
        "n_features": int(len(vals)),
        "median_spearman": round(float(np.median(vals)), 4) if len(vals) else None,
        "frac_spearman_ge_0.8": round(float((vals >= 0.8).mean()), 4) if len(vals) else None,
        "frac_spearman_le_0.2": round(float((vals <= 0.2).mean()), 4) if len(vals) else None,
        "worst_10": dict(sorted(cs.items(), key=lambda kv: kv[1])[:10]),
    }

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)

    print(json.dumps({k: out[k] for k in (
        "panel", "panel_date", "panel_dates", "n_codes_panel_at_date",
        "champion_n_features", "pipeline_n_names_declared",
        "champ_names_missing_from_pipeline_declaration",
        "cross_section_agreement",
    )}, ensure_ascii=False, indent=1))
    for r in out["rows"]:
        print(json.dumps(r, ensure_ascii=False)[:400])
    print("OUT:", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""CG177 대체 프로브 — 정지 원천 3종의 '입력 열화'가 스코어 압축을 만드는가 (읽기 전용).

WHY(2026-11-09 엔지니어): CG175 는 2026-10-02~10-08 발행본 압축(max 0.22~0.29 · >=0.55 0행)을
현재(백필 후) 데이터로 **재현하지 못했다**. 남은 두 원인 중 '서빙 모델 신원'과 '입력 열화'를
가르려면 그 구간 입력을 복원해야 하는데, 정지 원천 3종(supply_market_features·
financial_ratio_features·macro_features)에는 적재시각(created_at)이 **없어** '당시 존재한 행'을
복원할 수 없다(실측: information_schema 에 해당 컬럼 부재).

→ CG177 setup_needed 가 명시한 대체안(= 열화 방향만 검정)을 구현한다:
  A(신선)  : 현재 DB 로 만든 피처 벡터로 채점
  B(결손)  : A 에서 정지 원천 유래 피처 53개(173 중)를 0.0 으로 대체 후 채점
     (서빙 경로 predictor.py 의 결손 대체 `features.get(f, 0.0)` 와 같은 값으로 만든다)

판정(사전 등록, 변경 금지):
  ① mean(B) − mean(published) 의 절대값 < 0.02  → **입력 열화가 압축을 설명**(A/B 방향 일치)
  ② 그 외(|Δ| ≥ 0.02)                          → 입력 열화로는 압축이 설명되지 않음 → **서빙 모델 신원**
     (단 B 가 A 보다 낮아지는 방향 자체는 기록한다 — 부분 기여 여부)
보조 지표: 압축 격차 gap = mean(A) − mean(published) 를 B 가 몇 % 회수하는가.

읽기 전용. 쓰기는 --out JSON 하나뿐.
사용(컨테이너): docker exec stock_xgboost_ml sh -c 'cd /app && python -u scripts/_cg177_stale_input_ab.py'
스모크: ... --dates 2026-10-08 --max-per-day 3
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

import numpy as np  # noqa: E402

CONF_TS = 0.55
DEFAULT_DATES = ["2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08"]
STALE_TABLES = ("supply_market_features", "financial_ratio_features", "macro_features")
OUT = "/app/app/reports/cg177_stale_input_ab.json"


def _stats(vals) -> dict:
    a = np.asarray([v for v in vals if v is not None], dtype=float)
    if a.size == 0:
        return {"n": 0}
    return {
        "n": int(a.size),
        "mean": round(float(a.mean()), 4),
        "max": round(float(a.max()), 4),
        "p90": round(float(np.percentile(a, 90)), 4),
        "ge_055": int((a >= CONF_TS).sum()),
        "ge_055_pct": round(100.0 * float((a >= CONF_TS).mean()), 2),
    }


def stale_feature_names(conn, model_feats) -> dict:
    """정지 원천 3종의 컬럼 ∩ 모델 피처 = 열화 대상. 테이블별로 나눠 돌려준다."""
    cur = conn.cursor()
    cur.execute(
        "SELECT table_name, column_name FROM information_schema.columns"
        " WHERE table_name = ANY(%s)", (list(STALE_TABLES),))
    out = {t: [] for t in STALE_TABLES}
    for table, col in cur.fetchall():
        if col in model_feats:
            out[table].append(col)
    cur.close()
    return {t: sorted(v) for t, v in out.items() if v}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", default=",".join(DEFAULT_DATES))
    ap.add_argument("--max-per-day", type=int, default=30)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--model", default="/app/app/models/champion")
    ap.add_argument("--label", default="champion")
    args = ap.parse_args()

    from app.feature_engine.feature_pipeline import FeaturePipeline
    from app.models.ensemble_model import EnsembleModel
    from app.storage.postgres_storage import PostgresStorage

    dates = [d.strip() for d in args.dates.split(",") if d.strip()]
    ens = EnsembleModel(model_dir=args.model)
    ens.load(args.model)
    names = json.load(open(os.path.join(args.model, "feature_names.json")))
    name_set = set(names)

    st = PostgresStorage()
    conn = st._get_conn()
    cur = conn.cursor()
    stale = stale_feature_names(conn, name_set)
    stale_cols = sorted({c for v in stale.values() for c in v})

    out = {"probe": "cg177_stale_input_ab", "model": args.label, "model_dir": args.model,
           "dates": dates, "max_per_day": args.max_per_day, "conf_ts": CONF_TS,
           "stale_source_cols": stale, "n_stale_cols": len(stale_cols),
           "n_model_feats": len(names), "published_dist": {}, "sample": {},
           "errors": [], "note": "정지 원천에 적재시각이 없어 '당시 행' 복원 불가 → 열화 방향 검정(0.0 대체)"}

    sample_rows = []
    for date in dates:
        cur.execute(
            "SELECT stock_code, confidence FROM ml_predictions"
            " WHERE prediction_date = %s AND confidence IS NOT NULL", (date,))
        rows = [(r[0], float(r[1])) for r in cur.fetchall()]
        out["published_dist"][date] = _stats([c for _, c in rows])
        if not rows:
            continue
        rows.sort(key=lambda x: x[1], reverse=True)
        by_code = sorted(rows, key=lambda x: x[0])
        step = max(1, len(by_code) // max(1, args.max_per_day))
        seen = set()
        for code, conf in rows[: args.max_per_day] + by_code[::step][: args.max_per_day]:
            if code in seen:
                continue
            seen.add(code)
            sample_rows.append((date, code, conf))

    fp = FeaturePipeline(pg_conn=conn)
    pub, fresh, zeroed, pairs = [], [], [], []
    # 열화 대상 컬럼이 신선 벡터에서 already 0 인 비율(검정력) — 이미 0 이면 0 대체는 무변화다.
    already_zero = {c: 0 for c in stale_cols}
    rows_cnt = 0
    t0 = time.time()
    for i, (date, code, pub_conf) in enumerate(sample_rows, 1):
        try:
            feats = fp.build_features(code, date)
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"build_features {date}/{code}: {type(e).__name__}: {e}")
            continue
        rows_cnt += 1
        vec = np.array([[float(feats.get(n, 0.0) or 0.0) for n in names]], dtype=float)
        vec_b = vec.copy()
        idx = [j for j, n in enumerate(names) if n in set(stale_cols)]
        for j in idx:
            if abs(float(vec[0, j])) < 1e-12:
                already_zero[names[j]] += 1
        vec_b[0, idx] = 0.0
        try:
            pa = float(np.asarray(ens.predict(vec), dtype=float).ravel()[0])
            pb = float(np.asarray(ens.predict(vec_b), dtype=float).ravel()[0])
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"predict {date}/{code}: {type(e).__name__}: {e}")
            continue
        pub.append(pub_conf); fresh.append(pa); zeroed.append(pb)
        pairs.append((pub_conf, pa, pb))
        out["sample"].setdefault(date, []).append(
            {"code": code, "published": round(pub_conf, 4),
             "fresh": round(pa, 4), "zeroed": round(pb, 4)})
        if i % 20 == 0:
            print(f"[{i}/{len(sample_rows)}] {time.time() - t0:.0f}s", flush=True)

    summary = {"published_sample": _stats(pub), "fresh": _stats(fresh), "zeroed": _stats(zeroed)}
    if pairs:
        d_za = [b - a for _, a, b in pairs]
        d_pf = [a - p for p, a, _ in pairs]
        d_pz = [p - b for p, _, b in pairs]
        summary["paired_delta"] = {
            "zeroed_minus_fresh": {"n": len(d_za),
                                   "mean": round(float(np.mean(d_za)), 4),
                                   "median": round(float(np.median(d_za)), 4)},
            "fresh_minus_published": {"n": len(d_pf),
                                      "mean": round(float(np.mean(d_pf)), 4),
                                      "median": round(float(np.median(d_pf)), 4)},
            "zeroed_minus_published": {"n": len(d_pz),
                                       "mean": round(float(np.mean(d_pz)), 4),
                                       "median": round(float(np.median(d_pz)), 4)},
        }
        gap = float(np.mean(d_pf))
        recov = float(np.mean(d_pz))
        out["compression_gap_explained_pct"] = (
            round(100.0 * recov / gap, 1) if abs(gap) > 1e-9 else None)
    out["summary"] = summary
    out["n_sample_rows"] = rows_cnt
    out["elapsed_s"] = round(time.time() - t0, 1)
    # 검정력: 정지 컬럼 중 신선 벡터에서 항상 0 이었던 비율
    out["power"] = {
        "stale_cols_always_zero_pct": round(100.0 * sum(1 for c in stale_cols
                                                        if rows_cnt and already_zero[c] == rows_cnt)
                                            / max(1, len(stale_cols)), 1),
        "stale_cols_mostly_zero": sorted((c for c in stale_cols if rows_cnt and
                                          2 * already_zero[c] > rows_cnt),
                                         key=lambda c: -already_zero[c])[:15],
    }

    # ---- 판정(사전 등록) ----
    if not pairs:
        out["verdict"] = "판정 불가 — 짝 표본 없음"
    else:
        dz = float(np.mean(d_pz))
        df = float(np.mean(d_pf))
        if abs(dz) < 0.02:
            out["verdict"] = (f"입력 열화 재현 — zeroed 짝 Δ평균(published − zeroed) {dz:+.4f} "
                              f"|Δ|<0.02 → 압축은 정지 원천 결손으로 설명된다.")
        else:
            out["verdict"] = (f"입력 열화 미재현 — zeroed 짝 Δ평균 {dz:+.4f}(|Δ|≥0.02). "
                              f"신선−발행 격차 {df:+.4f} 대비 zeroed 가 격차의 "
                              f"{out.get('compression_gap_explained_pct')}% 만 회수 → 잔여는 "
                              f"**서빙 모델 신원 또는 미보존 모델**.")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: out.get(k) for k in ("verdict", "n_stale_cols", "n_sample_rows",
                                              "compression_gap_explained_pct", "power")},
                     ensure_ascii=False, indent=1))
    print("[summary]", json.dumps(summary, ensure_ascii=False))
    print(f"[ok] {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

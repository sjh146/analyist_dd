#!/usr/bin/env python3
"""CG175 배포 스코어 압축 원인 이분 프로브 — 읽기 전용(read-only).

WHY(2026-10-11 엔지니어 자율): CG162 는 2026-10-02~10-08 발행된 전 종목 confidence 의
최대가 소비 문턱(0.55)에 **한 행도** 닿지 못했다가 컨테이너 재시작(10-09) 후 복구(max 0.6375)
된 사실을 남겼다. 남은 원인 후보는 둘이다:
  (a) **모델 신원** — 실행 중 프로세스가 옛/다른 모델(champion 173피처)로 채점했다.
  (b) **입력(피처)** — 같은 모델인데 그 구간의 입력 피처가 망가져(결측 대체·원천 정지) 스코어가
      눌렸다.
이 프로브는 그 구간의 (종목, 날짜)를 **오프라인으로 다시 채점**해 발행본과 짝으로 비교한다:
  - 발행본(운영 DB ml_predictions) : 그날 실제로 소비자에게 나간 confidence
  - 재채점 champion(173피처)        : 지금 디스크의 배포 모델로 같은 날짜·종목을 다시 채점
  - 재채점 cg166(193피처·서빙정합)  : 같은 날짜·종목을 후보 모델로 다시 채점
판정 논리(사전 등록, 변경 금지):
  ① 재채점 champion 분포가 발행본과 일치(±0.02)  → '입력/파이프라인' 문제(피처가 눌렸다)
  ② 재채점 champion 이 발행본보다 뚜렷이 높다      → '서빙 모델 신원' 문제(당시 다른 모델이 돌았다)
  ③ cg166 재채점이 문턱 도달률을 회복            → 그 크기를 수치로 기록(승격 근거 아님·진단)

읽기 전용 — DB 쓰기 없음. 재현(컨테이너):
  docker exec stock_xgboost_ml sh -c 'cd /app && python -u scripts/_cg163_scale_bisect.py'
빠른 스모크:
  ... --dates 2026-10-08 --max-per-day 3
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

import numpy as np  # noqa: E402

CONF_TS = 0.55
DEFAULT_DATES = ["2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08"]
MODELS = [
    ("champion", "/app/app/models/champion"),
    ("cg166", "/app/app/models/champion_cand_cg166"),
]
OUT = "/app/app/reports/cg175_scale_bisect.json"


def _parse_models(spec: str):
    """`label=dir;label=dir` → [(label, dir)]. 빈 문자열이면 기본 MODELS."""
    out = []
    for part in (spec or "").split(";"):
        part = part.strip()
        if not part:
            continue
        label, _, d = part.partition("=")
        if label.strip() and d.strip():
            out.append((label.strip(), d.strip()))
    return out or MODELS



def _stats(vals) -> dict:
    a = np.asarray([v for v in vals if v is not None], dtype=float)
    if a.size == 0:
        return {"n": 0}
    return {
        "n": int(a.size),
        "mean": round(float(a.mean()), 4),
        "max": round(float(a.max()), 4),
        "p90": round(float(np.percentile(a, 90)), 4),
        "p95": round(float(np.percentile(a, 95)), 4),
        "ge_055": int((a >= CONF_TS).sum()),
        "ge_055_pct": round(100.0 * float((a >= CONF_TS).mean()), 2),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", default=",".join(DEFAULT_DATES))
    ap.add_argument("--max-per-day", type=int, default=30,
                    help="날짜별 표본 상한(발행 confidence 상위 K + 결정적 균등표본 K)")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--models", default="",
                    help="label=dir;label=dir … (미지정 시 기본 champion/cg166). "
                         "CG177: 발행본을 재현하는 '모델 신원'을 찾기 위해 과거 챔피언 디렉터리를 후보로 넣는다.")
    args = ap.parse_args()
    dates = [d.strip() for d in args.dates.split(",") if d.strip()]
    model_spec = _parse_models(args.models)

    from app.models.ensemble_model import EnsembleModel
    from app.feature_engine.feature_pipeline import FeaturePipeline
    from app.storage.postgres_storage import PostgresStorage

    out: dict = {"probe": "cg175_scale_bisect", "dates": dates,
                 "max_per_day": args.max_per_day, "conf_ts": CONF_TS,
                 "published_dist": {}, "sample": {}, "verdict": None, "errors": []}

    st = PostgresStorage()
    conn = st._get_conn()
    cur = conn.cursor()

    # ---- 모델 로드 ---------------------------------------------------------
    models = []
    for label, d in model_spec:
        try:
            ens = EnsembleModel(model_dir=d)
            ens.load(d)
            names = json.load(open(os.path.join(d, "feature_names.json")))
            models.append((label, ens, names))
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"load {label}: {type(e).__name__}: {e}")

    # ---- 발행본 전량 분포(저비용) + 표본 선택 -------------------------------
    sample_rows: list[tuple[str, str, float]] = []
    for date in dates:
        cur.execute(
            "SELECT stock_code, confidence FROM ml_predictions"
            " WHERE prediction_date = %s AND confidence IS NOT NULL",
            (date,),
        )
        rows = [(r[0], float(r[1])) for r in cur.fetchall()]
        out["published_dist"][date] = _stats([c for _, c in rows])
        if not rows:
            continue
        rows.sort(key=lambda x: x[1], reverse=True)
        top = rows[: args.max_per_day]
        # 결정적 균등표본(정렬된 코드 기준): 표본 편향을 줄인다
        by_code = sorted(rows, key=lambda x: x[0])
        step = max(1, len(by_code) // max(1, args.max_per_day))
        even = by_code[::step][: args.max_per_day]
        seen: set[str] = set()
        for code, conf in top + even:
            if code in seen:
                continue
            seen.add(code)
            sample_rows.append((date, code, conf))

    # ---- 오프라인 재채점 ----------------------------------------------------
    fp = FeaturePipeline(pg_conn=conn)
    pub_all, ret = [], {label: [] for label, _, _ in models}
    pairs = {label: [] for label, _, _ in models}  # (published, re-scored)
    # 피처 결손 센서스(CG177 후속): 서빙 경로가 173피처 중 몇 개를 결손(→0.0)으로 채우는가.
    # predictor.py L189 `features.get(f, 0.0)` + L193 `np.nan_to_num(..., nan=0.0)` 는 결손을
    # 조용히 0 으로 대체한다 — 결손률이 높으면 그 자체가 스코어를 기준선 쪽으로 압축한다.
    census_names = models[0][2] if models else []
    absent_cnt = {n: 0 for n in census_names}
    zero_cnt = {n: 0 for n in census_names}
    rows_cnt = 0
    fails = 0
    t0 = time.time()
    for i, (date, code, pub_conf) in enumerate(sample_rows, 1):
        try:
            feats = fp.build_features(code, date)
        except Exception as e:  # noqa: BLE001
            fails += 1
            out["errors"].append(f"build_features {date}/{code}: {type(e).__name__}: {e}")
            continue
        rows_cnt += 1
        for n in census_names:
            if n not in feats:
                absent_cnt[n] += 1
                zero_cnt[n] += 1
            elif float(feats.get(n) or 0.0) == 0.0:
                zero_cnt[n] += 1
        row = {"date": date, "code": code, "published": round(pub_conf, 4)}
        pub_all.append(pub_conf)
        for label, ens, names in models:
            vec = np.array([[float(feats.get(n, 0.0) or 0.0) for n in names]], dtype=float)
            try:
                p = float(np.asarray(ens.predict(vec), dtype=float).ravel()[0])
            except Exception as e:  # noqa: BLE001
                out["errors"].append(f"predict {label} {date}/{code}: {type(e).__name__}: {e}")
                p = None
            row[label] = None if p is None else round(p, 4)
            if p is not None:
                ret[label].append(p)
                pairs[label].append((pub_conf, p))
        out["sample"].setdefault(date, []).append(row)
        if i % 20 == 0:
            print(f"[{i}/{len(sample_rows)}] {time.time() - t0:.0f}s fails={fails}", flush=True)

    # ---- 요약 --------------------------------------------------------------
    summary = {"published_sample": _stats(pub_all), "rescored": {}, "paired_delta": {}}
    for label, _, _ in models:
        summary["rescored"][label] = _stats(ret[label])
        pr = pairs[label]
        if pr:
            deltas = [r - p for p, r in pr]
            summary["paired_delta"][label] = {
                "n": len(pr),
                "mean_rescored_minus_published": round(float(np.mean(deltas)), 4),
                "median_delta": round(float(statistics.median(deltas)), 4),
                "rescored_gt_published_pct": round(100.0 * float(np.mean([d > 0 for d in deltas])), 1),
            }
    out["summary"] = summary
    # ---- 피처 결손 센서스 요약 ----------------------------------------------
    if rows_cnt and census_names:
        always_absent = sorted(n for n, c in absent_cnt.items() if c == rows_cnt)
        always_zero = sorted(n for n, c in zero_cnt.items() if c == rows_cnt)
        out["feature_census"] = {
            "n_rows": rows_cnt,
            "n_features": len(census_names),
            "always_absent": len(always_absent),
            "always_zero": len(always_zero),
            "absent_ratio_pct": round(100.0 * sum(absent_cnt.values())
                                      / (rows_cnt * len(census_names)), 2),
            "zero_ratio_pct": round(100.0 * sum(zero_cnt.values())
                                    / (rows_cnt * len(census_names)), 2),
            "absent_over_50pct": sorted(
                (n for n, c in absent_cnt.items() if 2 * c > rows_cnt),
                key=lambda n: -absent_cnt[n])[:20],
            "absent_sample": always_absent[:20],
        }
    out["n_sample_rows"] = len(sample_rows)
    out["n_failures"] = fails
    out["elapsed_s"] = round(time.time() - t0, 1)

    # ---- 판정(사전 등록) ---------------------------------------------------
    # CG177 확장: 후보 모델 중 **발행본을 재현하는 신원**이 있는지 본다(단일 champion 고정 판정을
    # 일반화). 재현 = 짝 Δ평균 |Δ| < 0.02. 재현 모델이 있으면 원인은 '서빙 모델 신원'이고,
    # 후보 전부 |Δ| ≥ 0.02 면 원인은 후보군 밖(당시 입력 열화)이다.
    pub_s = summary["published_sample"]
    repro = {}
    for label, _, _ in models:
        d_ = summary["paired_delta"].get(label, {}).get("mean_rescored_minus_published")
        if d_ is not None:
            repro[label] = d_
    out["reproduction"] = repro
    if not repro:
        out["verdict"] = "판정 불가 — 짝 표본 없음"
    else:
        best_label, best_delta = min(repro.items(), key=lambda kv: abs(kv[1]))
        hits = [lb for lb, dl in repro.items() if abs(dl) < 0.02]
        if hits:
            out["verdict"] = (
                f"발행본 재현 — {', '.join(hits)} 가 발행본과 일치(최근접 {best_label} 짝 Δ평균 "
                f"{best_delta:+.4f} < 0.02). → 원인 = **서빙 모델 신원**(압축 구간의 서빙 프로세스가"
                f" 그 모델을 들고 있었다). 후보별 Δ: "
                + ", ".join(f"{lb} {dl:+.4f}" for lb, dl in repro.items()))
        else:
            out["verdict"] = (
                f"발행본 미재현 — 후보 {len(repro)}종 전부 |Δ| ≥ 0.02(최근접 {best_label} "
                f"{best_delta:+.4f}). → 원인 = **후보군 밖**(당시 입력 피처 열화 또는 미보존 모델)."
                f" 후보별 Δ: " + ", ".join(f"{lb} {dl:+.4f}" for lb, dl in repro.items()))
    cg = summary["rescored"].get("cg166", {})
    if cg.get("ge_055_pct") is not None and pub_s.get("ge_055_pct") is not None:
        out["threshold_recovery"] = {
            "published_ge_055_pct": pub_s.get("ge_055_pct"),
            "cg166_ge_055_pct": cg.get("ge_055_pct"),
        }

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: out[k] for k in ("verdict", "feature_census", "n_sample_rows",
                                          "n_failures", "elapsed_s") if k in out},
                     ensure_ascii=False, indent=1))
    print("[summary]", json.dumps(out["summary"], ensure_ascii=False))
    print(f"[ok] {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

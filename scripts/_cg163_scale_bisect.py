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
    args = ap.parse_args()
    dates = [d.strip() for d in args.dates.split(",") if d.strip()]

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
    for label, d in MODELS:
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
    fails = 0
    t0 = time.time()
    for i, (date, code, pub_conf) in enumerate(sample_rows, 1):
        try:
            feats = fp.build_features(code, date)
        except Exception as e:  # noqa: BLE001
            fails += 1
            out["errors"].append(f"build_features {date}/{code}: {type(e).__name__}: {e}")
            continue
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
    out["n_sample_rows"] = len(sample_rows)
    out["n_failures"] = fails
    out["elapsed_s"] = round(time.time() - t0, 1)

    # ---- 판정(사전 등록) ---------------------------------------------------
    champ = summary["rescored"].get("champion", {})
    champ_delta = summary["paired_delta"].get("champion", {}).get("mean_rescored_minus_published")
    pub_s = summary["published_sample"]
    if champ.get("n", 0) == 0:
        out["verdict"] = "측정 불가 — 표본 없음"
    elif champ_delta is None:
        out["verdict"] = "판정 불가 — 짝 표본 없음"
    elif abs(champ_delta) < 0.02:
        out["verdict"] = (
            f"발행본 재현됨 — 재채점 champion 이 발행본과 일치(짝 Δ평균 {champ_delta:+.4f} < 0.02)."
            " 눌림은 발행 시점 입력/모델 신원의 성질이며 현재 (모델·피처) 로는 재현되지 않는다"
            "(단, 스냅샷 부재로 원인 분리 불가).")
    else:
        # |Δ| ≥ 0.02 — 큰 폭 불일치. 원인이 '모델 신원'인지 '당시 입력(피처) 열화'인지
        # 이 프로브만으로는 분리할 수 없다(features_used 가 전 행 빈 배열 = 스냅샷 없음, CG164 실측).
        out["verdict"] = (
            f"발행본 미재현 — 재채점 champion 이 발행본과 뚜렷이 다르다(짝 Δ평균 {champ_delta:+.4f},"
            f" |Δ|≥0.02). 후보 원인 = ①서빙 모델 신원(당시 다른 모델) ②당시 입력 피처 열화(원천 정지)."
            " 이 프로브만으로는 분리 불가(발행 시 features_used 스냅샷 없음)."
            " 참고: CG165 원천 정지(supply/financial/macro 09-30~10-08)와 시점이 겹친다.")
    cg = summary["rescored"].get("cg166", {})
    if cg.get("ge_055_pct") is not None and pub_s.get("ge_055_pct") is not None:
        out["threshold_recovery"] = {
            "published_ge_055_pct": pub_s.get("ge_055_pct"),
            "cg166_ge_055_pct": cg.get("ge_055_pct"),
        }

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: out[k] for k in ("verdict", "summary", "n_sample_rows", "n_failures", "elapsed_s")},
                     ensure_ascii=False, indent=1))
    print(f"[ok] {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

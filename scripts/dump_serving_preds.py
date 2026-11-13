#!/usr/bin/env python3
"""dump_serving_preds.py — 배포 서빙 예측(ml_predictions)을 돈 지표 입력 스키마로 덤프한다 (CG246).

WHY (실측 배경)
  지금까지 모든 돈 지표는 **재학습 OOF 예측**(champion_robust_eval --dump-preds)으로 쟀다.
  그런데 매매에 실제로 쓰이는 것은 `ml_predictions` 에 매일 적재되는 서빙 confidence 다.
  둘은 다른 물건일 수 있다(전처리·유니버스·모델버전·결측 대체가 다르다).
  실측(2026-11-13): `ml_predictions` 는 model_version 2종
    v1.0                             15일(2026-09-22~10-08) 63,436행
    champ-20261002T024420-0.5513      5일(2026-10-09~11-12) 21,733행
  → 배포 챔피언의 서빙 예측은 **5세션**뿐이다(그 사이 11-09/11-10/11-12 는 하루씩 띄엄띄엄).
  표본이 작으므로 이 스크립트의 출력은 '판정'이 아니라 **배포 실체 측정**이다.

출력 스키마 = scripts/fillable_topk_expectancy.py --arm-jsonl
  {"exp","fold","date","code","y_pred"}  (+ "y_true": null)

널 대조군
  같은 행의 점수를 난수로 바꾼 파일(seed 고정)을 함께 쓴다 → top-k 가 '무작위 k 바스켓'이 되고,
  짝 Δ(arm − random)가 모델 순위의 기여분이 된다(풀평균 기준선은 측정기가 자체 계산).

사용(컨테이너 안 — psycopg2·DB 접근은 컨테이너에 있다)
  docker exec stock_xgboost_ml sh -c 'cd /app && python scripts/dump_serving_preds.py \
      --out-dir /app/reports/overnight/cg246 --seed 42'
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys


def db_connect():
    import psycopg2
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT", 5434)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def fetch(conn, version=None):
    q = ("SELECT model_version, prediction_date, stock_code, confidence "
         "FROM ml_predictions WHERE confidence IS NOT NULL")
    params = []
    if version:
        q += " AND model_version = %s"
        params.append(version)
    q += " ORDER BY prediction_date, stock_code"
    cur = conn.cursor()
    cur.execute(q, params)
    rows = cur.fetchall()
    cur.close()
    return rows


def safe_tag(v):
    return "".join(c if (c.isalnum() or c in "-_.") else "_" for c in str(v))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="서빙 예측 → 돈 지표 입력 jsonl 덤프")
    ap.add_argument("--out-dir", default="/app/reports/overnight/cg246")
    ap.add_argument("--version", default=None, help="ml_predictions.model_version (기본: 전부)")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args(argv)

    os.makedirs(args.out_dir, exist_ok=True)
    conn = db_connect()
    rows = fetch(conn, args.version)
    conn.close()
    if not rows:
        print("행 없음 — ml_predictions 비어 있음", file=sys.stderr)
        return 2

    by_ver = {}
    for ver, d, code, conf in rows:
        by_ver.setdefault(ver, []).append((d, code, float(conf)))

    rng = random.Random(args.seed)
    manifest = {"versions": {}, "seed": args.seed, "n_rows": len(rows), "out_dir": args.out_dir}
    for ver, rs in sorted(by_ver.items()):
        tag = "svc_" + safe_tag(ver)
        arm_p = os.path.join(args.out_dir, f"{tag}.jsonl")
        ctl_p = os.path.join(args.out_dir, f"{tag}_random.jsonl")
        dates = sorted({str(d) for d, _, _ in rs})
        with open(arm_p, "w", encoding="utf-8") as fa, open(ctl_p, "w", encoding="utf-8") as fc:
            for d, code, conf in rs:
                base = {"exp": tag, "fold": 0, "date": str(d),
                        "code": str(code).zfill(6), "y_true": None}
                fa.write(json.dumps({**base, "y_pred": conf}, ensure_ascii=False) + "\n")
                fc.write(json.dumps({**base, "exp": tag + "_rand", "y_pred": rng.random()},
                                    ensure_ascii=False) + "\n")
        manifest["versions"][ver] = {
            "tag": tag, "arm": arm_p, "control": ctl_p + "#" + tag + "_rand",
            "n_rows": len(rs), "n_dates": len(dates), "dates": dates,
        }
        print(f"{ver}: {len(rs)}행 · {len(dates)}일 ({dates[0]}~{dates[-1]}) → {arm_p}")

    with open(os.path.join(args.out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print("[ok] " + os.path.join(args.out_dir, "manifest.json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

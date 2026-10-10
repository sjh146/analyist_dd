#!/usr/bin/env python3
"""CG161 프로브 — `ml_predictions` 의 **일자별 다중 실행** 실측(읽기 전용).

왜: `save_prediction` 은 `ON CONFLICT (stock_code, prediction_date, model_version) DO NOTHING` 이라
한 prediction_date 를 **여러 번 실행하면 먼저 들어간 행이 이긴다**. 그런데 실측상 같은 날짜에
created_at 이 수 시간 떨어진 실행이 존재하면, 그날의 행 집합은 **두 시점의 피처/모델 상태가 섞인
스냅샷**이 된다. 전방 스코어카드·top-k·IC 는 날짜 단위로 집계하므로 이 혼합이 측정에 직접 들어간다.

30분 이상 간격이면 별도 실행(run)으로 자른다. 각 run 의 크기·시각·평균 confidence 를 낸다.

실행: docker exec stock_xgboost_ml sh -c 'cd /app && python -u scripts/_cg161_run_mix_probe.py'
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime

OUT = "/app/reports/cg161_run_mix_probe.json"
GAP_MIN = 30


def main() -> int:
    import psycopg2

    conn = psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )
    cur = conn.cursor()
    cur.execute(
        "SELECT prediction_date, stock_code, confidence, created_at, model_version "
        "FROM ml_predictions ORDER BY prediction_date, created_at, stock_code"
    )
    rows = cur.fetchall()
    conn.close()

    by_date = defaultdict(list)
    for d, code, conf, created, ver in rows:
        by_date[str(d)[:10]].append((created, code, float(conf) if conf is not None else None, ver))

    dates, multi = [], []
    for d in sorted(by_date):
        items = by_date[d]
        runs, cur_run = [], [items[0]]
        for prev, cur in zip(items, items[1:]):
            if (cur[0] - prev[0]).total_seconds() > GAP_MIN * 60:
                runs.append(cur_run)
                cur_run = [cur]
            else:
                cur_run.append(cur)
        runs.append(cur_run)
        rec = {
            "date": d,
            "n": len(items),
            "n_runs": len(runs),
            "model_versions": sorted({r[3] for r in items}),
            "runs": [
                {
                    "n": len(rn),
                    "first": rn[0][0].isoformat(sep=" "),
                    "last": rn[-1][0].isoformat(sep=" "),
                    "mean_confidence": round(
                        sum(x[2] for x in rn if x[2] is not None)
                        / max(1, sum(1 for x in rn if x[2] is not None)), 4
                    ),
                }
                for rn in runs
            ],
        }
        if len(runs) > 1:
            means = [r["mean_confidence"] for r in rec["runs"]]
            rec["mean_gap_between_runs"] = round(max(means) - min(means), 4)
            multi.append(rec)
        dates.append(rec)

    out = {
        "probe": "cg161_run_mix_probe",
        "metric": "prediction_run_mix_audit",
        "gap_minutes": GAP_MIN,
        "n_dates": len(dates),
        "n_dates_multi_run": len(multi),
        "multi_run_share": round(len(multi) / len(dates), 4) if dates else None,
        "multi_run_dates": multi,
        "per_date": dates,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2, sort_keys=True)

    print(f"날짜 {out['n_dates']} · 다중실행 {len(multi)} ({out['multi_run_share']})")
    for r in multi:
        spans = " | ".join(f"{x['n']}행 {x['first'][:16]} μ{x['mean_confidence']}" for x in r["runs"])
        print(f"  [혼합] {r['date']} runs={r['n_runs']} μ차={r['mean_gap_between_runs']} :: {spans}")
    print("증거:", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""select_training_universe 시간 안정성 프로브(읽기 전용).

가설: date_from 기본값 `_default_date_from()` = now-60일 이므로 **날짜가 바뀌면 유니버스가 바뀐다**.
`eligible.sort(latest desc)[:600]` → seed 셔플 → 200 이라, 적격 집합이 조금만 흔들려도 셔플 입력
순서가 바뀌어 200종목이 통째로 재추첨된다. 그러면 ① 패널(고정 end-date)과 프로덕션 학습 유니버스가
어긋나고 ② 유니버스 정렬(CG141·CG73)의 목표가 매일 바뀌는 이동표적이 된다.

실행: docker exec -e PYTHONPATH=/app stock_xgboost_ml python /app/scripts/_universe_stability_probe.py
"""
import datetime as dt
import os

import numpy as np
import psycopg2

from app.training.universe import select_training_universe, _fetch_eligible

PANEL = os.environ.get("PANEL", "/app/app/models/wf/panel_prod200.npz")


def conn():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD"),
    )


def main():
    z = np.load(PANEL, allow_pickle=True)
    panel = {str(c.item()) if hasattr(c, "item") else str(c) for c in z["codes"]}
    c = conn()
    today = dt.date.today()
    dates = {
        "default(now-60)": None,
        "2026-08-03": "2026-08-03",   # panel_prod200 빌드 시점의 now-60
        "2026-08-10": "2026-08-10",   # 오늘의 now-60
        "2026-09-27": "2026-09-27",
    }
    res = {}
    for label, df in dates.items():
        u = select_training_universe(c, limit=200, min_days=30, seed=0, date_from=df)
        res[label] = u
        print(f"[{label}] n={len(u)} ∩panel={len(set(u) & panel)} sample={sorted(u)[:4]}")
    base = set(res["default(now-60)"])
    for label, u in res.items():
        if label == "default(now-60)":
            continue
        print(f"  default ∩ {label} = {len(base & set(u))}/200")

    # 적격 집합 자체도 날짜 의존인지 확인
    for label, df in (("now-60(2026-08-10)", None), ("2026-08-03", "2026-08-03")):
        df = df or (today - dt.timedelta(days=60)).strftime("%Y-%m-%d")
        e = _fetch_eligible(c, df, 30)
        print(f"eligible[{label}]: {len(e)}종목 (date_from={df})")
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

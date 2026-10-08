"""CG141 오프라인 스코프 프로브(읽기 전용) — 공매도 수집 목록 불일치의 크기를 실측한다.

현행 수집: scripts/kis_short_selling_backfill.py::universe() = 최근 95일 평균 거래대금 상위 200
(= 유동성 상위 = KOSPI 대형주). 크론: /home/jhshi/cron/kis_short_program.sh --limit 200.
학습 유니버스: app.training.universe.select_training_universe(limit=200, seed=0) (= 결정적 표본).

이 프로브는 두 목록과 패널(panel_prod200) 코드의 교집합을 각각 재서 '수집 범위 미스매치'의
크기와, 학습 유니버스로 바꿨을 때의 커버리지를 숫자로 남긴다. KIS 호출 0 · 쓰기 0.

실행: docker exec -e PYTHONPATH=/app stock_xgboost_ml python /app/scripts/_cg141_scope_probe.py
"""
import os
import sys

import numpy as np
import psycopg2

sys.path.insert(0, "/app")
from app.training.universe import select_training_universe  # noqa: E402

PANEL = os.environ.get("PANEL", "/app/app/models/wf/panel_prod200.npz")


def conn():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD"),
    )


def panel_codes():
    z = np.load(PANEL, allow_pickle=True)
    out = set()
    for c in z["codes"]:
        out.add(str(c.item()) if hasattr(c, "item") else str(c))
    return out


def liquidity_list(cur, limit=200, days=95):
    """현행 수집 목록(스크립트와 동일 SQL)."""
    cur.execute(
        """
        SELECT s.stock_code
        FROM stocks s
        JOIN (
            SELECT m.stock_code, AVG(m.trading_value) AS avg_tv
            FROM market_data m
            WHERE m.trade_date >= (SELECT MAX(trade_date) FROM market_data) - INTERVAL '%s days'
              AND m.trading_value IS NOT NULL AND m.volume > 0
              AND m.stock_code ~ '^[0-9]{6}$'
            GROUP BY m.stock_code
        ) liq ON liq.stock_code = s.stock_code
        WHERE COALESCE(s.instrument_type, 'STOCK') = 'STOCK'
        ORDER BY liq.avg_tv DESC NULLS LAST, s.stock_code
        LIMIT %s
        """ % (days, limit)
    )
    return [r[0] for r in cur.fetchall()]


def main():
    pc = panel_codes()
    c = conn()
    cur = c.cursor()
    liq = liquidity_list(cur, 200, 95)
    tu = select_training_universe(c, limit=200, min_days=30, seed=0)
    print(f"panel codes: {len(pc)}")
    print(f"현행 수집(유동성 상위 200): n={len(liq)} ∩ panel = {len(set(liq) & pc)}")
    print(f"학습 유니버스(recency seed0 200): n={len(tu)} ∩ panel = {len(set(tu) & pc)}")
    print(f"두 목록 상호 교집합: {len(set(liq) & set(tu))}")
    cur.execute("SELECT COUNT(DISTINCT stock_code), MIN(trade_date), MAX(trade_date) FROM krx_short_selling")
    n, mn, mx = cur.fetchone()
    print(f"krx_short_selling: {n}종목 · {mn}~{mx}")
    cur.execute(
        "SELECT COUNT(DISTINCT stock_code) FROM krx_short_selling WHERE stock_code = any(%s)",
        (sorted(pc),),
    )
    print(f"krx_short_selling ∩ panel = {cur.fetchone()[0]}")
    cur.close()
    c.close()
    ok = len(set(tu) & pc) >= 0.9 * len(pc)
    print(f"[판정] 학습 유니버스 스코프 전환 시 패널 커버리지 {len(set(tu) & pc)}/{len(pc)} "
          f"→ 목표 ≥90% {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

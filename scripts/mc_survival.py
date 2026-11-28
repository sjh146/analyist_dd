"""몬테카를로 생존력 시뮬레이션 — 이 규칙으로 24.6만원이 1년을 버티는가.

입력은 **실측 분봉 페이오프 분포**다(가정한 정규분포가 아니다):
  1) minute_bars_unified 에서 (종목,일)마다 시가 진입 → -stop% / +target% 첫 터치 판정
  2) 그 페이오프 배열을 복원추출해 1년(240거래일 × 2건) 경로를 10,000번 생성
  3) 비용(요율 + 슬리피지)을 건당 차감, 사이징은 실설정(단타 노출 30% / 2슬롯 = 건당 15%)

출력: 최종 자본 분포, 파산(-50%) 확률, 최대낙폭 분포, 월 수익 분포, 개선(지정가 청산) 효과 비교

사용:
    python3 scripts/mc_survival.py --equity 246121 --paths 10000
"""
from __future__ import annotations

import argparse
import os
from collections import defaultdict

import numpy as np
import psycopg2


def load_env(path: str = "/home/jhshi/analyist_dd/.env") -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def pg_connect():
    load_env()
    host = os.environ.get("POSTGRES_HOST") or "127.0.0.1"
    port = int(os.environ.get("POSTGRES_PORT", "5434"))
    if not os.path.exists("/.dockerenv"):
        host = "127.0.0.1" if host == "postgres" else host
        port = 5434 if port == 5432 else port
    return psycopg2.connect(host=host, port=port, dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
                            user=os.environ.get("POSTGRES_USER", "stock_user"),
                            password=os.environ.get("POSTGRES_PASSWORD", ""))


def empirical_payoffs(stop_pct: float, target_pct: float) -> np.ndarray:
    conn = pg_connect()
    cur = conn.cursor()
    cur.execute("select stock_code, trade_date::text, open_price from market_data where open_price > 0")
    opens = {(str(c), d): float(o) for c, d, o in cur.fetchall()}
    cur.execute("""
        SELECT stock_code, trade_date::text, time, close_price
        FROM minute_bars_unified WHERE close_price > 0 ORDER BY stock_code, trade_date, time
    """)
    bars = defaultdict(list)
    for c, d, t, p in cur.fetchall():
        bars[(str(c), d)].append((int(t), float(p)))
    conn.close()

    out = []
    for key, series in bars.items():
        o = opens.get(key)
        if not o:
            continue
        up, dn = o * (1 + target_pct), o * (1 - stop_pct)
        payoff = None
        for _, px in sorted(series):
            if px >= up:
                payoff = target_pct * 100
                break
            if px <= dn:
                payoff = -stop_pct * 100
                break
        if payoff is None:
            payoff = (sorted(series)[-1][1] / o - 1.0) * 100
        out.append(payoff)
    return np.array(out)


def simulate(payoffs: np.ndarray, equity: float, cost_pct: float, paths: int,
             trades_per_day: int, days: int, exposure: float, seed: int = 42) -> dict:
    rng = np.random.default_rng(seed)
    slots = trades_per_day
    finals, max_dd, ruin, months = [], [], 0, []
    for _ in range(paths):
        eq = equity
        peak = eq
        dd = 0.0
        month_end = []
        for d in range(days):
            for _ in range(trades_per_day):
                notional = eq * (exposure / slots)
                p = payoffs[rng.integers(0, len(payoffs))] - cost_pct
                eq += notional * (p / 100.0)
                if eq <= 0:
                    eq = 0.0
                    break
                peak = max(peak, eq)
                dd = max(dd, (peak - eq) / peak)
            if eq <= 0:
                break
            if (d + 1) % 20 == 0:
                month_end.append(eq)
        finals.append(eq)
        max_dd.append(dd)
        if eq < equity * 0.5:
            ruin += 1
        months.extend(month_end)
    finals = np.array(finals)
    max_dd = np.array(max_dd)
    return {
        "final_median": float(np.median(finals)),
        "final_p5": float(np.percentile(finals, 5)),
        "final_p95": float(np.percentile(finals, 95)),
        "p_profit": float((finals > equity).mean()),
        "p_ruin_50": ruin / paths,
        "maxdd_median": float(np.median(max_dd)),
        "maxdd_p95": float(np.percentile(max_dd, 95)),
        "months": months,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--equity", type=float, default=246121)
    ap.add_argument("--paths", type=int, default=10000)
    ap.add_argument("--days", type=int, default=240)
    ap.add_argument("--trades-per-day", type=int, default=2)
    ap.add_argument("--exposure", type=float, default=0.30)
    ap.add_argument("--stop", type=float, default=0.0075)
    ap.add_argument("--target", type=float, default=0.05)
    args = ap.parse_args()

    payoffs = empirical_payoffs(args.stop, args.target)
    print(f"실측 페이오프 {len(payoffs):,}건 · 평균 {payoffs.mean():+.3f}% · "
          f"목표 {np.mean(payoffs >= args.target*100*0.99):.1%} · 손절 {np.mean(payoffs <= -args.stop*100*0.99):.1%}")

    scenarios = [
        ("현행(시장가 청산)", 0.18 + 0.245),
        ("개선(지정가 청산)", 0.18 + 0.05),
        ("개선+진입 bid", 0.18 + 0.05 + 0.05),
    ]
    for name, cost in scenarios:
        r = simulate(payoffs, args.equity, cost, args.paths, args.trades_per_day, args.days, args.exposure)
        print(f"\n== {name} (왕복비용 {cost:.3f}%) ==")
        print(f"  1년 뒤 자본 중앙 {r['final_median']:,.0f}원 (5% {r['final_p5']:,.0f} / 95% {r['final_p95']:,.0f})")
        print(f"  수익 확률 {r['p_profit']:.1%} · -50% 파산 확률 {r['p_ruin_50']:.2%}")
        print(f"  최대낙폭 중앙 {r['maxdd_median']:.1%} · 95% {r['maxdd_p95']:.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

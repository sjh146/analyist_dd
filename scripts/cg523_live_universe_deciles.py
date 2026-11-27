#!/usr/bin/env python3
"""cg523_live_universe_deciles.py — **배포 유니버스**(ml_predictions, 하루 ~3.5천종목)에서
챔피언 점수가 순서 정보를 갖는가. 분위별 순기대 + 세션별 짝Δ(D10−D1).

무엇을 재나 (CG523, 2026-11-27 엔지니어 자율 — CG522 조사 후속):
  CG522 실측으로 패널(200종목) 돈 지표의 Δ(+1.02%p/5d)는 **배포 대상 유니버스와 다른 표본**임이
  드러났다 — 라이브 상위5 ∩ 패널200 = 5.2%(7/135). 라이브 예측은 하루 3,531~3,909종목을 채점한다.
  그래서 "패널에서 잰 Δ"는 라이브 엣지의 대리지표가 아니다. 이 스크립트는 **모델이 실제로 채점하는
  전 종목 횡단면**에서 점수의 순서 정보(분위 단조성)를 잰다.

계산 규칙(최우선 규칙 3):
  · 표본: ml_predictions(prediction_date, stock_code, confidence) ⨝ market_data(같은 날 종가)
  · 라벨: h=5 거래일 뒤 종가 → close-to-close %, 수수료 왕복 0.21%p 차감
  · 체결성 근사: 진입일 등락 ≥ +25%(상한가 근처) 행 제외 — fillable_expectancy 와 같은 취지
  · 단위: 세션 내 10분위 동일비중 → %p/세션. 판정은 **세션별 짝 Δ(D10−D1)** 로 한다(시장 베타 상쇄)
  · 산출 스키마는 구동기 `score_deciles` 파서 계약을 그대로 따른다
    → 판정 문턱(ρ≥+0.5 & D10−D1≥+0.3%p)은 구동기 `judge_score_deciles` 가 사전 등록대로 적용한다.

사용(호스트, 읽기 전용):
  python3 scripts/cg523_live_universe_deciles.py --json-out data/reports/overnight/cg523_summary.json
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from datetime import datetime, timedelta, timezone

HORIZON = 5
FEE_ROUNDTRIP = 0.21
DECILES = 10
MAX_DAY_RET = 25.0
MIN_ROWS_SESSION = 200

SQL = """
WITH px AS (
  SELECT stock_code, trade_date, close_price, trading_value,
         (close_price / NULLIF(lag(close_price) OVER (PARTITION BY stock_code ORDER BY trade_date),0) - 1) * 100 AS day_ret,
         (lead(close_price, %(h)s) OVER (PARTITION BY stock_code ORDER BY trade_date) / NULLIF(close_price,0) - 1) * 100 AS fwd
  FROM market_data WHERE trade_date >= %(d_from)s
)
SELECT p.prediction_date::text, p.stock_code, p.confidence::float, px.day_ret::float, px.fwd::float,
       px.trading_value::float
FROM ml_predictions p
JOIN px ON px.stock_code = p.stock_code AND px.trade_date = p.prediction_date
WHERE p.prediction_date >= %(d_from)s AND p.confidence IS NOT NULL
"""


def _env(path=".env"):
    """repo .env 를 읽는다 — 호스트 실행 시 접속 정보(포트 5434·비밀번호)는 여기 있다."""
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def connect():
    import psycopg2
    e = {**_env(), **os.environ}
    kw = dict(
        host=e.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(e.get("POSTGRES_PORT", "5434")),
        dbname=e.get("POSTGRES_DB", "stock_trading"),
        user=e.get("POSTGRES_USER", "stock_user"),
        password=e.get("POSTGRES_PASSWORD", ""),
    )
    try:
        return psycopg2.connect(**kw)
    except psycopg2.OperationalError:
        kw.update(host="127.0.0.1", port=5434)
        return psycopg2.connect(**kw)



def spearman(xs, ys):
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        for pos, idx in enumerate(order):
            r[idx] = float(pos)
        return r
    if len(xs) < 3:
        return None
    rx, ry = rank(xs), rank(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** .5
    dy = sum((b - my) ** 2 for b in ry) ** .5
    return round(num / (dx * dy), 4) if dx and dy else None


def tstat(v):
    if len(v) < 3:
        return None
    sd = statistics.stdev(v)
    return round(statistics.mean(v) / (sd / len(v) ** .5), 2) if sd else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json-out", required=True)
    ap.add_argument("--since", default="2026-09-22", help="ml_predictions 시작일(기본 = 테이블 최초일)")
    ap.add_argument("--min-rows-session", type=int, default=MIN_ROWS_SESSION)
    ap.add_argument("--min-value-eok", type=float, default=0.0,
                    help="최소 거래대금(억원) — 배포 트레이더의 유동성 필터(50억)와 같은 모집단에서 재려면 50")
    args = ap.parse_args()

    d_from = (datetime.fromisoformat(args.since) - timedelta(days=15)).date().isoformat()
    conn = connect()
    cur = conn.cursor()
    cur.execute(SQL, {"h": HORIZON, "d_from": d_from})
    rows = cur.fetchall()
    cur.close()
    conn.close()

    per_date = {}
    skipped = {"no_fwd": 0, "day_ret_gate": 0, "small_session": 0, "value_gate": 0}
    vmin = (args.min_value_eok or 0.0) * 1e8
    for d, code, conf, day_ret, fwd, tval in rows:
        if fwd is None:
            skipped["no_fwd"] += 1
            continue
        if day_ret is not None and day_ret >= MAX_DAY_RET:
            skipped["day_ret_gate"] += 1
            continue
        if vmin > 0 and (tval is None or tval < vmin):
            skipped["value_gate"] += 1
            continue
        per_date.setdefault(d, []).append((conf, fwd - FEE_ROUNDTRIP))

    dec_sess = {k: {} for k in range(1, DECILES + 1)}
    sess_pool, sess_d1, sess_d10, sess_n = {}, {}, {}, {}
    for d in sorted(per_date):
        v = sorted(per_date[d], key=lambda x: x[0])
        if len(v) < args.min_rows_session:
            skipped["small_session"] += 1
            continue
        n = len(v)
        sess_n[d] = n
        sess_pool[d] = statistics.mean(f for _, f in v)
        for i, (_, f) in enumerate(v):
            k = min(DECILES, int(i * DECILES / n) + 1)
            dec_sess[k].setdefault(d, []).append(f)
    if not sess_n:
        print(json.dumps({"error": "표본 없음(fwd 결측이거나 세션 최소표본 미달)"}, ensure_ascii=False))
        return 2

    deciles = []
    for k in range(1, DECILES + 1):
        means = {d: statistics.mean(v) for d, v in dec_sess[k].items() if v}
        sess_means = list(means.values())
        n_rows = sum(len(v) for v in dec_sess[k].values())
        deciles.append({
            "decile": k,
            "mean_pct": round(statistics.mean(sess_means), 4) if sess_means else None,
            "t_stat": tstat(sess_means),
            "pos_sessions_pct": (round(100.0 * sum(1 for x in sess_means if x > 0) / len(sess_means), 1)
                                 if sess_means else None),
            "n_rows": n_rows,
        })

    dates = sorted(sess_n)
    d1 = {d: statistics.mean(dec_sess[1][d]) for d in dates if d in dec_sess[1]}
    d10 = {d: statistics.mean(dec_sess[DECILES][d]) for d in dates if d in dec_sess[DECILES]}
    common = [d for d in dates if d in d1 and d in d10]
    deltas = [d10[d] - d1[d] for d in common]
    half = len(deltas) // 2
    pool_means = [sess_pool[d] for d in dates]
    rho = spearman([x["mean_pct"] for x in deciles if x["mean_pct"] is not None],
                   [x["decile"] for x in deciles if x["mean_pct"] is not None])

    def _power(series, target_pcts=(0.3, 0.2, 0.1)):
        """MDE80(2.802·SE, α0.05 양측·검정력 80%)와 목표 효과별 필요 세션 수.

        t=0.26 짜리 결과를 '랭킹 무효'로 읽으면 안 된다 — 세션 수가 적으면 실효 엣지
        (0.1~0.3%p)를 원리적으로 검출할 수 없어 '노이즈'가 아니라 '판정불가(저검정력)'다.
        """
        n = len(series)
        if n < 3:
            return None
        sd = statistics.stdev(series)
        se = sd / n ** .5
        mean = statistics.mean(series)
        return {
            "n_sessions": n,
            "mean_pct": round(mean, 4),
            "sd_daily_pct": round(sd, 4),
            "se_pct": round(se, 4),
            "ci95_pct": [round(mean - 1.96 * se, 4), round(mean + 1.96 * se, 4)],
            "mde80_pct": round(2.802 * se, 4),
            "required_sessions": {str(t): int((2.802 * sd / t) ** 2) + 1 for t in target_pcts},
        }

    p_d10d1 = _power(deltas)
    top5 = [statistics.mean([f for _, f in sorted(per_date[d], key=lambda x: x[0])[-5:]]) for d in dates]
    p_top5_pool = _power([top5[i] - pool_means[i] for i in range(len(dates))])
    monotonic = all(deciles[i]["mean_pct"] is not None and deciles[i + 1]["mean_pct"] is not None
                    and deciles[i + 1]["mean_pct"] >= deciles[i]["mean_pct"]
                    for i in range(len(deciles) - 1))

    out = {
        "generated_at": datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="seconds"),
        "config": {
            "source": "ml_predictions ⨝ market_data", "horizon": HORIZON, "exit": "close_h",
            "fee_roundtrip_pct": FEE_ROUNDTRIP, "max_day_ret_pct": MAX_DAY_RET,
            "min_rows_session": args.min_rows_session, "dates": [dates[0], dates[-1]],
            "min_value_eok": args.min_value_eok,
            "days": len(dates),
        },
        "models": {
            "live": {
                "n_sessions": len(dates),
                "deciles": deciles,
                "spearman_rho": rho,
                "d10_minus_d1_pct": round(statistics.mean(deltas), 4) if deltas else None,
                "paired_d10_d1": {
                    "mean_pct": round(statistics.mean(deltas), 4) if deltas else None,
                    "t_stat": tstat(deltas),
                    "pos": sum(1 for x in deltas if x > 0), "neg": sum(1 for x in deltas if x < 0),
                    "front_pct": round(statistics.mean(deltas[:half]), 4) if half else None,
                    "back_pct": round(statistics.mean(deltas[half:]), 4) if half else None,
                },
                "pool_mean_pct": round(statistics.mean(pool_means), 4),
                "pool_t": tstat(pool_means),
                "monotonic": monotonic,
                "power": {
                    "d10_minus_d1": p_d10d1,
                    "top5_minus_pool": p_top5_pool,
                    "note": ("MDE80 = 2.802·SE (α0.05 양측·검정력 80%). MDE80 이 목표 효과보다 "
                             "크면 그 결과는 '신호 없음'이 아니라 '판정불가(저검정력)'다."),
                },
                "session_deltas": {d: round(x, 4) for d, x in zip(common, deltas)},
                "n_rows_scored": sum(sess_n.values()),
                "n_rows_after_filter": sum(sess_n.values()),
                "n_skipped": skipped,
                "avg_codes_per_session": round(statistics.mean(list(sess_n.values())), 1),
                "topk_reference": {
                    "top5_mean_pct": round(statistics.mean(
                        [statistics.mean([f for _, f in sorted(per_date[d], key=lambda x: x[0])[-5:]])
                         for d in dates]), 4),
                    "top10_mean_pct": round(statistics.mean(
                        [statistics.mean([f for _, f in sorted(per_date[d], key=lambda x: x[0])[-10:]])
                         for d in dates]), 4),
                },
            }
        },
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.json_out)), exist_ok=True)
    with open(args.json_out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps({"out": args.json_out, "sessions": len(dates), "rho": rho,
                      "d10_minus_d1_pct": out["models"]["live"]["d10_minus_d1_pct"],
                      "pool_mean_pct": out["models"]["live"]["pool_mean_pct"],
                      "skipped": skipped}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""cg528_universe_signal_power.py — 배포 유니버스 횡단면 신호의 **검정력 있는** 채점 (CG528).

왜 이 스크립트인가 (2026-11-27 엔지니어 자율, CG524·CG526 후속):
  CG523/CG524/CG526 은 ml_predictions 이력이 34세션(그것도 10-11~11-09 공백)뿐이라
  쓸 수 있는 세션이 17~21개였고, 그 표본의 MDE80 은 1.65~4.4%p — 등록 문턱 0.3%p 의 5~15배다.
  즉 **모델 얘기가 아니라 표본 얘기**로 판정이 불가능했다. 그런데 시장 데이터(market_data)는
  종목 3,985 · 952세션(2023-01-02~2026-11-26)이 있다 → 모델 없이 계산되는 횡단면 신호는
  같은 프로토콜로 **수백 세션**에서 채점할 수 있고, 그러면 MDE80 이 문턱 수준(≈0.35%p)으로 내려간다.

  이것이 지금 필요한 이유: 23사이클째 로버스트 AUC 가 기준선 대비 +0.02 를 못 넘고, 남은 판단거리는
  "이 종목 풀에서 **어떤** 횡단면 순서라도 돈이 되는가"다. 그 질문에 답할 수 있는 유일한 표본이
  이 스크립트다. 모델 축(챔피언 점수)은 세션이 17~21개뿐이라 여전히 판정불가 → 축 종결 금지.

프로토콜(최우선 규칙 3 준수):
  · 표본: market_data (배포 트레이더가 쓰는 전 종목 횡단면), --min-value-eok 로 유동성 모집단 고정
  · 라벨: close_h = h거래일 뒤 종가 / next_open = h거래일 뒤 시가 (트레이더 09:00~09:10 청산 근사)
  · 체결성: 진입일 등락 ≥ +25%(상한가 근처) 행 제외 · 수수료 왕복 0.21%p 차감
  · 지표: 10분위 평균, ρ, D10−D1 세션별 짝Δ(t·앞/뒤 절반), 풀평균(널 기준선), MDE80·필요세션
  · 중첩 보정: h거래일 라벨은 세션 간 중첩된다 → **비중첩(stride=h) 부분표본 t** 를 함께 낸다

사용(호스트, 읽기 전용):
  python3 scripts/cg528_universe_signal_power.py --json-out data/reports/overnight/cg528.json \
      --since 2025-06-01 --min-value-eok 50 --horizon 5 --exit close_h \
      --signals lowvol,reversal_1d,return_5d,momentum_20d
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import statistics
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("cg523", os.path.join(HERE, "cg523_live_universe_deciles.py"))
cg523 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cg523)

FEE_ROUNDTRIP = cg523.FEE_ROUNDTRIP
MAX_DAY_RET = cg523.MAX_DAY_RET

SQL = """
WITH px0 AS (
  SELECT stock_code, trade_date, close_price, open_price, trading_value,
         close_price / NULLIF(lag(close_price) OVER w, 0) - 1 AS ret
  FROM market_data WHERE trade_date >= %(px_from)s
  WINDOW w AS (PARTITION BY stock_code ORDER BY trade_date)
)
SELECT trade_date::text, stock_code, trading_value::float,
       ret::float * 100 AS day_ret,
       __FWD__ AS fwd,
       (close_price / NULLIF(lag(close_price, 5) OVER (PARTITION BY stock_code ORDER BY trade_date), 0) - 1) * 100 AS ret_5d,
       (close_price / NULLIF(lag(close_price, 20) OVER (PARTITION BY stock_code ORDER BY trade_date), 0) - 1) * 100 AS ret_20d,
       stddev_samp(ret) OVER (PARTITION BY stock_code ORDER BY trade_date
                              ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) * 100 AS vol_20d,
       count(ret) OVER (PARTITION BY stock_code ORDER BY trade_date
                        ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS n_ret_20d
FROM px0
WHERE trade_date >= %(since)s
"""

SIGNALS = {
    "lowvol": lambda r: (-r["vol_20d"]) if (r.get("n_ret_20d") or 0) >= 20 else None,
    "volatility_20d": lambda r: r["vol_20d"] if (r.get("n_ret_20d") or 0) >= 20 else None,
    "reversal_1d": lambda r: (-r["day_ret"]) if r.get("day_ret") is not None else None,
    "return_5d": lambda r: r.get("ret_5d"),
    "momentum_20d": lambda r: r.get("ret_20d"),
}


def stride_t(series, stride):
    """비중첩 부분표본 t — h일 라벨의 세션 중첩을 없앤 보수적 통계량."""
    vals = list(series)
    sub = vals[::stride] if stride and stride > 1 else vals
    if len(sub) < 3:
        return None, len(sub)
    sd = statistics.stdev(sub)
    t = round(statistics.mean(sub) / (sd / len(sub) ** .5), 2) if sd else None
    return t, len(sub)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json-out", required=True)
    ap.add_argument("--since", default="2025-06-01", help="평가 시작일(선행 h일 확보를 위해 90일 앞부터 조회)")
    ap.add_argument("--min-value-eok", type=float, default=50.0, help="최소 거래대금(억원) — 배포 유동성 필터")
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--exit", choices=sorted(cg523.FWD_EXPR), default="close_h")
    ap.add_argument("--min-rows-session", type=int, default=200)
    ap.add_argument("--year", type=int, default=0, help=">0 이면 그 해만 평가(기간효과 분해용)")
    ap.add_argument("--signals", default="lowvol,reversal_1d,return_5d,momentum_20d")
    a = ap.parse_args()

    keys = [s.strip() for s in a.signals.split(",") if s.strip()]
    unknown = [k for k in keys if k not in SIGNALS]
    if unknown:
        print(json.dumps({"error": f"알 수 없는 신호: {unknown} (가능: {list(SIGNALS)})"}, ensure_ascii=False))
        return 2

    d_from = (datetime.fromisoformat(a.since) - timedelta(days=120)).date().isoformat()
    conn = cg523.connect()
    cur = conn.cursor()
    cur.execute(SQL.replace("__FWD__", cg523.FWD_EXPR[a.exit]),
                {"h": a.horizon, "px_from": d_from, "since": a.since})
    raw = cur.fetchall()
    cur.close()
    conn.close()

    vmin = (a.min_value_eok or 0.0) * 1e8
    points = {k: {} for k in keys}
    skipped = {"no_fwd": 0, "day_ret_gate": 0, "value_gate": 0, "year_gate": 0, "no_history": 0}
    for d, code, tval, day_ret, fwd, ret5, ret20, vol20, nret20 in raw:
        if fwd is None:
            skipped["no_fwd"] += 1
            continue
        fwd = float(fwd)
        tval = float(tval) if tval is not None else None
        vol20 = float(vol20) if vol20 is not None else None
        ret5 = float(ret5) if ret5 is not None else None
        ret20 = float(ret20) if ret20 is not None else None
        if a.year and not str(d).startswith(str(a.year)):
            skipped["year_gate"] += 1
            continue
        if day_ret is not None and day_ret >= MAX_DAY_RET:
            skipped["day_ret_gate"] += 1
            continue
        if vmin > 0 and (tval is None or tval < vmin):
            skipped["value_gate"] += 1
            continue
        rec = {"day_ret": day_ret, "ret_5d": ret5, "ret_20d": ret20,
               "vol_20d": vol20, "n_ret_20d": nret20}
        for k in keys:
            sv = SIGNALS[k](rec)
            if sv is None:
                skipped["no_history"] += 1
                continue
            points[k].setdefault(d, []).append((sv, fwd - FEE_ROUNDTRIP))

    models, errors = {}, {}
    for k in keys:
        if not points[k]:
            errors[k] = "표본 없음"
            continue
        blk, deltas = cg523.evaluate(points[k], a.min_rows_session, k)
        if deltas is None or blk.get("error"):
            errors[k] = blk.get("error")
            continue
        st, sn = stride_t(deltas, a.horizon)
        blk["nonoverlap"] = {"stride": a.horizon, "n": sn, "t_stat": st,
                             "mean_pct": round(statistics.mean(deltas[::a.horizon]), 4)}
        dec = blk["deciles"]
        blk["arm_long_only"] = {"decile": 10, "mean_pct": dec[-1]["mean_pct"],
                                "pool_mean_pct": blk["pool_mean_pct"],
                                "excess_vs_pool_pct": (round(dec[-1]["mean_pct"] - blk["pool_mean_pct"], 4)
                                                       if dec[-1]["mean_pct"] is not None else None)}
        models[k] = blk

    if not models:
        print(json.dumps({"error": "전 신호 표본 없음", "skipped": skipped}, ensure_ascii=False))
        return 2

    dates = sorted(models[keys[0]]["session_deltas"]) if keys[0] in models else []
    out = {
        "generated_at": datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="seconds"),
        "config": {"source": "market_data(전 종목 횡단면)", "since": a.since,
                   "horizon": a.horizon, "exit": a.exit, "fee_roundtrip_pct": FEE_ROUNDTRIP,
                   "min_value_eok": a.min_value_eok, "year": a.year,
                   "days": models[keys[0]].get("n_sessions") if keys[0] in models else None,
                   "date_range": [dates[0], dates[-1]] if dates else None,
                   "note": "비중첩 t(nonoverlap)를 1차 통계량으로 본다 — h일 라벨은 세션 중첩된다"},
        "skipped": skipped, "errors": errors, "models": models,
    }
    os.makedirs(os.path.dirname(os.path.abspath(a.json_out)), exist_ok=True)
    with open(a.json_out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print(json.dumps({"out": a.json_out, "sessions": out["config"]["days"], **{
        k: {"rho": v.get("spearman_rho"), "d10_minus_d1": v.get("d10_minus_d1_pct"),
            "t": (v.get("paired_d10_d1") or {}).get("t_stat"),
            "nonoverlap_t": v["nonoverlap"]["t_stat"],
            "mde80": ((v.get("power") or {}).get("d10_minus_d1") or {}).get("mde80_pct"),
            "d1": v["deciles"][0]["mean_pct"], "d10": v["deciles"][-1]["mean_pct"],
            "pool": v.get("pool_mean_pct"),
            "front_back": [(v.get("paired_d10_d1") or {}).get("front_pct"),
                           (v.get("paired_d10_d1") or {}).get("back_pct")]}
        for k, v in models.items()}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

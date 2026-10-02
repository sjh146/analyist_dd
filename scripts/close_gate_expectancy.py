#!/usr/bin/env python3
"""close 경로 게이트의 '돈 기준' 기대값 — 수수료 반영 + 세션 단위 집계 (읽기 전용).

WHY (실측 2026-10-02): `data/reports/close_gate_probe/` 는 87세션·1,736후보를
"발굴일 종가 매수 → 다음 거래일 시가 매도"(트레이더 close 프로파일의 실제 청산 정의)로
재생해 놓았는데, 요약(summary.json)은 **gross** 이고 세션 단위 유의성도 없다.
게이트를 완화할지 말지는 "수수료 빼고도 남는가" + "세션 단위로도 양(+)인가"로 판정해야 한다.

계산 정의
  순수익률(%) = ret_next_open_pct − (매수 수수료 + 매도 수수료 + 거래세) × 100
                = ret − (0.00015 + 0.00015 + 0.0018) × 100   [요율은 pnl_backtest.py 와 동일]
  게이트 버킷   : r1_ok / heat_ok 조합 4개
  전략 시뮬     : 세션별 score 내림차순 상위 K(기본 3 — 동시 3종목 한도) 종목을
                  동일비중 매수 → 다음 시가 매도. 세션 평균을 만든 뒤 세션들에 대해
                  평균·중앙·양(+)세션 비율·t통계를 낸다(행 단위 과대표집 방지).

한계(보고에 함께 쓴다)
  · 진입가 근사: 실제는 14:50~15:29 체결가, 여기서는 발굴일 종가.
  · 후보는 재생성물(당시 발행본 아님) — 모델 버전이 당시와 다를 수 있다.
  · 슬리피지·호가 갭은 수수료 모델에 없다(보수적으로 낮게 잡힌 순기대).
  · 표본은 2026-05-26~09-30 구간이라 레짐 편향 가능.

사용:
  cd /home/jhshi/analyist_dd && python3 scripts/close_gate_expectancy.py \
      --trades data/reports/close_gate_probe/trades.csv --topk 3 --json-out data/reports/close_gate_expectancy.json
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from collections import defaultdict

# 수수료 모델 — scripts/pnl_backtest.py 와 같은 값(한국 주식)
FEE_BUY = 0.00015
FEE_SELL = 0.00015
TAX_SELL = 0.0018
ROUND_TRIP = (FEE_BUY + FEE_SELL + TAX_SELL) * 100          # %p


def load(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                rows.append({
                    "date": r["date"], "code": r["code"],
                    "score": float(r["score"]),
                    "day_chg": float(r["day_change_pct"]),
                    "ret": float(r["ret_next_open_pct"]),
                    "gate_ok": str(r["gate_ok"]).lower() in ("true", "1"),
                    "r1_ok": str(r["r1_ok"]).lower() in ("true", "1"),
                    "heat_ok": str(r["heat_ok"]).lower() in ("true", "1"),
                })
            except (KeyError, ValueError, TypeError):
                continue
    return rows


def net(ret_pct):
    return ret_pct - ROUND_TRIP


def bucket(rows):
    """r1/heat 조합 4버킷."""
    out = defaultdict(list)
    for r in rows:
        key = ("r1_ok" if r["r1_ok"] else "r1_fail") + "_" + ("heat_ok" if r["heat_ok"] else "heat_fail")
        out[key].append(r)
    return out


def session_stats(per_session):
    """세션별 평균 리스트 → 평균·중앙·양(+)비율·t통계·세션수."""
    vals = [v for v in per_session if v is not None]
    n = len(vals)
    if not n:
        return {"n_sessions": 0}
    mean = sum(vals) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / (n - 1)) if n > 1 else 0.0
    t = (mean / (sd / math.sqrt(n))) if sd > 0 else 0.0
    sv = sorted(vals)
    return {"n_sessions": n, "avg_pct": round(mean, 4), "median_pct": round(sv[n // 2], 4),
            "pos_sessions_pct": round(100 * sum(1 for v in vals if v > 0) / n, 1),
            "t_stat": round(t, 2), "sd": round(sd, 3)}


def simulate(rows, topk, mode):
    """mode: current(R1 ∧ HEAT) | no_heat(R1만) | no_r1(HEAT만) | none(게이트 없음)."""
    by_date = defaultdict(list)
    for r in rows:
        if mode == "current" and not (r["r1_ok"] and r["heat_ok"]):
            continue
        if mode == "no_heat" and not r["r1_ok"]:
            continue
        if mode == "no_r1" and not r["heat_ok"]:
            continue
        by_date[r["date"]].append(r)
    per_session, detail = [], {}
    for d, rs in sorted(by_date.items()):
        picked = sorted(rs, key=lambda x: -x["score"])[:topk]
        if not picked:
            per_session.append(None)
            continue
        sess = sum(net(p["ret"]) for p in picked) / len(picked)
        per_session.append(sess)
        detail[d] = {"n_pick": len(picked), "avg_net_pct": round(sess, 4),
                     "codes": [p["code"] for p in picked]}
    return session_stats(per_session), detail


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="close 경로 게이트 기대값(수수료 반영)")
    ap.add_argument("--trades", default="data/reports/close_gate_probe/trades.csv")
    ap.add_argument("--topk", type=int, default=3, help="세션당 매수 종목 수(동시 3종목 한도)")
    ap.add_argument("--max-day-chg", type=float, default=None,
                    help="당일등락 상한 필터(%%): 이 이상 오른 후보 제외 — 상한가 근처는 매수 체결 불가")
    ap.add_argument("--json-out", default="data/reports/close_gate_expectancy.json")
    a = ap.parse_args(argv)

    if not os.path.exists(a.trades):
        print(f"trades 파일 없음: {a.trades}", file=sys.stderr)
        return 2
    rows = load(a.trades)
    n_raw, n_sess_raw = len(rows), len({r["date"] for r in rows})
    if a.max_day_chg is not None:
        rows = [r for r in rows if r["day_chg"] < a.max_day_chg]
    print(f"표본: {len(rows)}후보 / {len({r['date'] for r in rows})}세션"
          + (f" (원본 {n_raw}후보/{n_sess_raw}세션, 당일등락 < +{a.max_day_chg}% 필터)"
             if a.max_day_chg is not None else "")
          + f" · 수수료 왕복 {ROUND_TRIP:.2f}%p 반영")

    out = {"n_rows": len(rows), "n_sessions": len({r['date'] for r in rows}),
           "round_trip_cost_pct": ROUND_TRIP, "topk": a.topk,
           "buckets": {}, "strategy": {}}

    print("\n[게이트 버킷 — 행 단위 기대값]")
    for key, rs in sorted(bucket(rows).items(), key=lambda x: -len(x[1])):
        g = sum(r["ret"] for r in rs) / len(rs)
        n = sum(net(r["ret"]) for r in rs) / len(rs)
        w = 100 * sum(1 for r in rs if r["ret"] > 0) / len(rs)
        print(f"  {key:20} n={len(rs):5} gross {g:+.3f}% → net {n:+.3f}% · 승률 {w:.1f}%")
        out["buckets"][key] = {"n": len(rs), "gross_pct": round(g, 4), "net_pct": round(n, 4),
                               "win_pct": round(w, 1)}

    print(f"\n[전략 시뮬 — 세션별 score 상위 {a.topk} 동일비중, 다음 시가 청산]")
    for mode, label in (("current", "현행(R1 ∧ HEAT)"), ("no_heat", "HEAT 해제(R1만)"),
                        ("no_r1", "R1 해제(HEAT만)"), ("none", "게이트 없음")):
        st, detail = simulate(rows, a.topk, mode)
        out["strategy"][mode] = {"label": label, **st}
        if st["n_sessions"]:
            print(f"  {label:16} 세션 {st['n_sessions']:3} · 순기대 {st['avg_pct']:+.3f}% "
                  f"· 중앙 {st['median_pct']:+.3f}% · 양(+)세션 {st['pos_sessions_pct']:.0f}% "
                  f"· t {st['t_stat']:+.2f}")
        else:
            print(f"  {label:16} 진입 세션 0 — 게이트가 경로를 닫았다")

    # 판정
    cur = out["strategy"]["current"]["n_sessions"]
    nh = out["strategy"]["no_heat"]
    verdict = []
    if cur == 0:
        verdict.append("현행 게이트로는 표본 기간 내내 진입 0세션 → 이 경로는 사실상 닫혀 있다")
    if nh.get("n_sessions") and nh.get("avg_pct", 0) > 0:
        verdict.append(f"HEAT 해제 시 세션 {nh['n_sessions']}개에서 순기대 {nh['avg_pct']:+.3f}% "
                       f"(t={nh['t_stat']:.2f}) — 근거 강도는 t값으로 판단")
    out["verdict"] = verdict

    os.makedirs(os.path.dirname(a.json_out), exist_ok=True)
    with open(a.json_out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\n[판정]")
    for v in verdict or ["판정 보류(표본 부족)"]:
        print(f"  · {v}")
    print(f"\n기록: {a.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""분봉 재생 — '일봉 애매(worst≠best)'를 실제 1분 종가 순서로 확정 (docs/spec_minute_replay.md v3 §2).

계약 문서: docs/spec_minute_replay.md (2026-11-18). **측정 단계만** — 실주문 경로 변경 없음,
DB 는 SELECT 전용(market_data·minute_close_bars) + 출력 파일 2개(주문·브리지·트레이더 무접근).

WHY (스펙 인용 + 실측 — 2026-11-18):
  일봉엔 장중 순서가 없다 → capital_sim 은 매 거래를 worst(손절 우선)/best(익절 우선) 두 극단으로
  계산한다. 실측(data/reports/capital_sim.json 조회): fixed_1_3 same_day **worst −0.74%/건 ~
  best +0.71%/건** 으로 갈린다(스펙 인용 '애매(worst≠best) 95.9%' 의 대상). 순서를 확정하려면
  분 단위 실제 경로가 필요하다 → minute_close_bars(신규 백필 테이블, scripts/naver_minute_backfill.py
  가 적재 — 종가·누적거래량만, OHLC 없음)의 1분 종가를 진입일 09:00 부터 순차로 재생한다.

실측 조회 명령(2026-11-18, 접속은 이 프로세스 환경변수 — 비밀번호 출력 금지):
  -- data/reports/capital_sim.json → fixed_1_3 same_day worst −0.74 · best +0.71 %/건(n=460)
  -- Naver 분봉 응답 실측(스펙 + 재확인): 하루 정규장(09:00~15:30) 381행/일 · OHLC null ·
  --   종가·누적거래량만 → 판정은 분봉 **종가** 경로(1분 종가 교차 = 근사 — 한계로 명시).

재사용(스펙: 'capital_sim.py 의 로직을 import 해서 재사용(복붙 금지)'):
  cs.build_signals(신호 정의) · cs.entry_budget/cs.size_qty(수량) · cs.RULES(규칙) ·
  cs._sell/cs._finalize/cs._summarize/cs.max_drawdown(체결·통계) · cs.FEE_PCT(왕복 0.21%) ·
  esb.load/compute_features(피처) — 모두 import, 복사 없음.

경로 규칙(스펙 §2):
  - 신호일 t 의 다음 거래일(D) **09:00 분봉부터** 순차로 종가를 본다. 진입가는 D 시가(entry_open,
    capital_sim 과 동일), 임계 = 진입가×(1±pct).
  - 임계를 **처음 넘은 분**에서 그 분 종가로 체결. 같은 분에 양방향(손절·익절) 동시 교차는
    **보수적으로 손절 우선** + 계수(ambiguous_same_minute).
    *종가만 저장(스펙: OHLC 저장 금지)이라 고정 규칙(손절<익절)에서는 단일 종가가 두 임계를 동시에
    넘을 수 없어 이 계수는 구조적으로 0 — 고가/저가가 있는 데이터였다면 발생했을 사건의 계측기
    (테스트 ①은 임계가 겹치는 퇴화 규칙으로 직접 호출해 우선순위 계약을 검증).*
  - 15:30 까지 미도달 → 당일 종가(=그날 마지막 정규장 분봉 종가) 청산(--same-day-close 기본 on)
    또는 5세션 보유(--no-same-day-close, 세션은 종목이 실제 거래한 날만 센다 — capital_sim 동일).
  - 분봉 데이터가 없는 (종목,일) 조합은 건너뛰고 센다(skipped_no_minute), **일봉 대체 금지**.
    스윙 보유 중 세션 분봉 공백은 마지막 실측 분봉 종가로 청산(no_minute_gap) — 추정 없음.
  - 일봉 밴드: 같은 신호·같은 프로파일에 대한 capital_sim worst/best 기대값을 같은 표에 병기
    (daily_worst_ev/daily_best_ev) — 분봉 경로가 밴드를 어디로 좁히는지 비교하는 용도.
  - 워크포워드: --from~--to 를 신호일 기준 절반으로 나눠 앞/뒤 각각 보고(후자는 재조정 없음 —
    규칙은 CLI 고정이라 선택 절차 자체가 없음). 각 구간은 동일 시작자본으로 독립 실행.

멱등: 출력(JSON/MD)에 실행 시각을 넣지 않는다. 같은 입력이면 같은 바이트(tests ⑥).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2

REPO = Path(__file__).resolve().parent.parent
SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
import capital_sim as cs  # noqa: E402 — 신호·수량·한도·비용 재사용(스펙: import 만)
import edge_stack_backtest as esb  # noqa: E402 — load/compute_features

RULE_IDS = ("fixed_1_3", "fixed_15_45", "trail_atr")


def connect():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT", "5434")),
        user=os.environ["POSTGRES_USER"], password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"), connect_timeout=15)


# ---------------------------------------------------------------------- 분봉 경로
def walk_day(closes, entry_px, rule_def, atr=None, state=None):
    """한 세션의 분봉 종가 경로 — 임계 첫 교차 분에서 그 분 종가로 체결(스펙 §2).

    closes : 09:00~15:30 분봉 종가(시간 오름차순). OHLC 는 저장하지 않으므로 없다(스펙 금지).
    반환   : (reason, exit_px, exit_idx, ambiguous, state)
      reason : "stop"|"target"|"trail"|None(당일 생존) · exit_px = 그 분 종가 · exit_idx = 분 인덱스
      ambiguous: 같은 분 양방향 동시 교차 → 손절 우선(스펙). 종가만으로는 고정 규칙에서 구조적
                 불가(계측기 — docstring 참고).
      state  : trail 전용 {"wm","activated","stop_px"} — 세션 간 유지(제자리 갱신).
    """
    kind = rule_def["kind"]
    stop_pct = rule_def["stop_pct"]
    stop_init = float(entry_px) * (1.0 - stop_pct / 100.0)
    if kind == "fixed":
        stop_px = stop_init
        tp_px = float(entry_px) * (1.0 + rule_def["tp_pct"] / 100.0)
        for i, c in enumerate(closes):
            c = float(c)
            hit_stop, hit_tp = c <= stop_px, c >= tp_px
            if hit_stop and hit_tp:            # 같은 분 양방향 → 보수 손절 우선(스펙)
                return ("stop", c, i, True, None)
            if hit_stop:
                return ("stop", c, i, False, None)
            if hit_tp:
                return ("target", c, i, False, None)
        return (None, None, None, False, None)
    # trail — capital_sim worst 순서와 동일: 기존 스탑 먼저 → 워터마크/활성화 갱신.
    # 분 단위에서는 갱신된 스탑(new_stop = wm−atr < wm = 현 분 종가 이하)이 같은 분 종가에 걸릴
    # 수 없으므로 capital_sim 의 '상향 스탑이 같은 봉 저가에 걸리는 비관 경로'가 필요 없다 —
    # 순서가 이미 분봉으로 확정되어 있다(이것이 재생의 목적).
    st = state if state is not None else {"wm": float(entry_px), "activated": False,
                                          "stop_px": stop_init}
    act_px = float(entry_px) * (1.0 + stop_pct / 100.0 * rule_def["act_r"])
    for i, c in enumerate(closes):
        c = float(c)
        hit_stop = c <= st["stop_px"]
        hit_act = (not st["activated"]) and (c >= act_px)
        if hit_stop and hit_act:               # 같은 분 양방향 → 손절 우선
            return ("stop", c, i, True, st)
        if hit_stop:
            return ("trail" if st["activated"] else "stop", c, i, False, st)
        if c >= act_px and not st["activated"]:
            st["activated"] = True
        if c > st["wm"]:
            st["wm"] = c
        if st["activated"] and atr is not None and not np.isnan(atr) and atr > 0:
            ns = max(stop_init, st["wm"] - float(atr) * rule_def["atr_mult"])
            if ns > st["stop_px"]:
                st["stop_px"] = ns
    return (None, None, None, False, st)


# ------------------------------------------------------------------------ 엔진
def _finalize_trade(pos):
    t = cs._finalize(pos)
    t["hold_minutes"] = int(pos["hold_minutes"])
    t["ambiguous_same_minute"] = bool(pos["ambiguous"])
    return t


def run_minute_replay(signals, bars, minute, rule_id, profile, *, equity0, max_pos,
                      per_stock_pct, daily_max, price_min, price_max,
                      slip_pct=0.0, fee_pct=cs.FEE_PCT, hold_sessions=5, dates):
    """분봉 경로 재생 1회(단일 경로 — worst/best 없음, 순서가 실제 분봉으로 확정됨).

    minute : {(stock_code, iso_date): [분봉 종가...]} — 09:00~15:30 오름차순.
    신호·한도·수량·비용·체결 순서는 capital_sim 과 동일(import 재사용 — 스펙 '복붙 금지'):
      체크 순서 가격대 → 중복보유 → 동시보유 → 일일건수 → 자본 → 분봉 존재.
    반환: {"metrics": dict, "trades": list, "skips": dict, "curve": [(iso, eq), ...]}
    """
    rule_def = cs.RULES[rule_id]
    ctx = {"cash": float(equity0), "slip_pct": float(slip_pct)}
    positions, trades = [], []
    skips = {"price_band": 0, "dup": 0, "slots": 0, "daily_max": 0, "capital": 0,
             "no_minute": 0}
    daily = {}
    held = set()
    curve = []
    by_date = {}
    for s in signals:
        by_date.setdefault(s["entry_date"], []).append(s)

    def close_pos(pos):
        positions.remove(pos)
        held.discard(pos["stock"])
        trades.append(_finalize_trade(pos))

    for ts in dates:
        d64 = ts.to_datetime64()
        iso = ts.date().isoformat()
        # 1) 보유 포지션 세션(스윙만 여기까지 살아있음 — same_day 는 진입일 종가에 정리)
        for pos in positions[:]:
            i = cs._bar_at(bars, pos["stock"], d64)
            if i is None:
                continue                       # 이 종목 오늘 휴장 → 캐리(capital_sim 동일)
            closes = minute.get((pos["stock"], iso))
            if not closes:                     # 분봉 공백 → 마지막 실측 종가 청산(추정 금지)
                cs._sell(ctx, pos, pos["last_close"], pos["qty"], "no_minute_gap")
                close_pos(pos)
                continue
            b = bars[pos["stock"]]
            pos["sessions"] += 1
            res = walk_day(closes, pos["entry_price"], rule_def, b["atr14"][i], pos["state"])
            if res[0] is not None:
                pos["hold_minutes"] += res[2] + 1
                pos["ambiguous"] = pos["ambiguous"] or res[3]
                cs._sell(ctx, pos, res[1], pos["qty"], res[0])
                close_pos(pos)
                continue
            pos["hold_minutes"] += len(closes)
            pos["last_close"] = closes[-1]
            if profile == "same_day":
                cs._sell(ctx, pos, closes[-1], pos["qty"], "eod")
                close_pos(pos)
                continue
            if pos["sessions"] >= hold_sessions:
                cs._sell(ctx, pos, closes[-1], pos["qty"], "time_stop")
                close_pos(pos)
        # 2) 당일 신규 진입(체크 순서 capital_sim 동일 — 그 다음에 분봉 존재 검사)
        new_positions = []
        for sig in by_date.get(ts, []):
            px = float(sig["entry_open"])
            if not (float(price_min) <= px <= float(price_max)):
                skips["price_band"] += 1
                continue
            if sig["stock"] in held:
                skips["dup"] += 1
                continue
            if len(positions) >= int(max_pos):
                skips["slots"] += 1
                continue
            if daily.get(ts, 0) >= int(daily_max):
                skips["daily_max"] += 1
                continue
            eq_now = ctx["cash"] + sum(p["qty"] * p["last_close"] for p in positions)
            budget = cs.entry_budget(eq_now, ctx["cash"], len(positions), max_pos,
                                     per_stock_pct)
            px_eff = px * (1.0 + slip_pct / 100.0)
            qty = cs.size_qty(px_eff, budget)
            if qty <= 0:
                skips["capital"] += 1
                continue
            closes = minute.get((sig["stock"], iso))
            if not closes:
                skips["no_minute"] += 1        # 일봉 대체 금지(스펙) — 건너뛰고 센다
                continue
            cost = qty * px_eff
            ctx["cash"] -= cost
            pos = {"stock": sig["stock"], "sig_idx": sig["sig_idx"],
                   "entry_date": sig["entry_date"], "entry_price": px, "qty": qty,
                   "entry_cost": cost, "fee": cost * fee_pct / 100.0,
                   "proceeds": 0.0, "sessions": 0, "last_close": px, "exits": [],
                   "hold_minutes": 0, "ambiguous": False,
                   "state": ({"wm": px, "activated": False,
                              "stop_px": px * (1.0 - rule_def["stop_pct"] / 100.0)}
                             if rule_def["kind"] == "trail" else None)}
            positions.append(pos)
            held.add(sig["stock"])
            daily[ts] = daily.get(ts, 0) + 1
            new_positions.append(pos)
        # 3) 오늘 진입한 포지션의 진입일 경로(09:00 분봉부터 — capital_sim 의 진입봉 세션 단계)
        for pos in new_positions:
            closes = minute[(pos["stock"], iso)]
            i = cs._bar_at(bars, pos["stock"], d64)
            atr = bars[pos["stock"]]["atr14"][i] if i is not None else None
            pos["sessions"] += 1
            res = walk_day(closes, pos["entry_price"], rule_def, atr, pos["state"])
            if res[0] is not None:
                pos["hold_minutes"] += res[2] + 1
                pos["ambiguous"] = pos["ambiguous"] or res[3]
                cs._sell(ctx, pos, res[1], pos["qty"], res[0])
                close_pos(pos)
                continue
            pos["hold_minutes"] += len(closes)
            pos["last_close"] = closes[-1]
            if profile == "same_day":
                cs._sell(ctx, pos, closes[-1], pos["qty"], "eod")
                close_pos(pos)
                continue
            if pos["sessions"] >= hold_sessions:
                cs._sell(ctx, pos, closes[-1], pos["qty"], "time_stop")
                close_pos(pos)
        # 4) 일별 마감 자본곡선(capital_sim 과 동일 정의: 현금 + 보유 평가액)
        eq = ctx["cash"] + sum(p["qty"] * p["last_close"] for p in positions)
        curve.append((iso, eq))
    # 데이터 끝까지 못 닫은 포지션 → 마지막 실측 분봉 종가로 강제 청산(capital_sim 과 동일 규율)
    for pos in positions[:]:
        cs._sell(ctx, pos, pos["last_close"], pos["qty"], "end_of_data")
        close_pos(pos)
    m = cs._summarize(trades, curve, skips, float(equity0))
    m.pop("ambiguous_pct")                      # 재생엔 worst/best 대비가 없음(단일 경로)
    m["skipped_no_minute"] = int(skips["no_minute"])
    m["ambiguous_same_minute"] = int(sum(1 for t in trades if t["ambiguous_same_minute"]))
    m["avg_hold_minutes"] = (cs._r4(np.mean([t["hold_minutes"] for t in trades]))
                             if trades else None)
    m["n_stop"] = m["n_target"] = m["n_no_minute_gap"] = 0
    for t in trades:
        for leg in t["exits"]:
            if leg[0] == "stop":
                m["n_stop"] += 1
            elif leg[0] == "target":
                m["n_target"] += 1
            elif leg[0] == "no_minute_gap":
                m["n_no_minute_gap"] += 1
    return {"metrics": m, "trades": trades, "skips": skips, "curve": curve}


# ---------------------------------------------------------------- 일봉 밴드·구간
def daily_band(signals, bars, dates, rule_id, profile, kw):
    """같은 신호·같은 프로파일에 대한 일봉 worst/best 기대값(capital_sim import) — 표 병기용."""
    w = cs.run_capital_sim(signals, bars, rule_id, profile, "worst", dates=dates, **kw)
    b = cs.run_capital_sim(signals, bars, rule_id, profile, "best", dates=dates, **kw)
    return w["metrics"], b["metrics"]


def _coverage_pct(n, skipped_no_minute):
    if n + skipped_no_minute == 0:
        return None
    return cs._r4(n / (n + skipped_no_minute) * 100.0)


# ------------------------------------------------------------------------ 보고서
def build_report(signals, bars, dates, minute, params, start, end, cfg,
                 n_loaded, raw_n, minute_stats):
    """전체 보고 dict(결정적 — 실행 시각 없음). cfg = {"json_out", "md_out"}."""
    mid = date.fromisoformat(start) + (date.fromisoformat(end) - date.fromisoformat(start)) // 2
    seg_defs = [("full", None, None),
                ("front", pd.Timestamp(start), pd.Timestamp(mid)),
                ("back", pd.Timestamp(mid + timedelta(days=1)), pd.Timestamp(end))]
    kw = params["engine_kw"]
    results = []
    for rule in params["rules"]:
        entries = []
        for seg_name, lo, hi in seg_defs:
            if lo is None:
                seg = signals
            else:
                seg = [s for s in signals if lo <= s["sig_date"] <= hi]
            r = run_minute_replay(seg, bars, minute, rule, params["profile"],
                                  dates=dates, **kw)
            dw, db = daily_band(seg, bars, dates, rule, params["daily_profile"], kw)
            m = r["metrics"]
            m["daily_worst_ev"] = dw["expectancy_pct"]
            m["daily_best_ev"] = db["expectancy_pct"]
            m["daily_n"] = dw["trades"]
            m["coverage_pct"] = _coverage_pct(m["trades"], m["skipped_no_minute"])
            entries.append({"segment": seg_name, "signals": int(len(seg)), **m})
        results.append({"rule": rule, "hold": params["profile"], "segments": entries})
    report = {
        "data_range": {"start": start, "end": end,
                       "walkforward_mid": mid.isoformat(),
                       "source": "market_data 신호(capital_sim 과 동일 정의) + "
                                 "minute_close_bars 분봉 종가 경로 · 진입 t+1 시가 · "
                                 "왕복비용 0.21% · 진입일 09:00~15:30 1분 종가 재생"},
        "params": {"equity": params["equity"], "max_pos": params["max_pos"],
                   "per_stock_pct": params["per_stock_pct"],
                   "daily_max": params["daily_max"],
                   "price_min": params["price_min"], "price_max": params["price_max"],
                   "fee_pct": params["fee_pct"], "hold": params["profile"],
                   "same_day_close": params["profile"] == "same_day",
                   "hold_sessions": params["hold_sessions"]},
        "minute_data": minute_stats,
        "results": results,
    }
    report["limits"] = [
        "Naver 분봉은 OHLC 가 null — **종가 경로만**으로 판정(1분 종가 교차 = 근사, 고가/저가 터치는 미관측)",
        "같은 분 양방향 교차는 종가만으로는 구조적으로 관측 불가(손절<익절) — 애매동일분 0 이 정상이며 계측기 유지",
        "Naver 는 최근 ~6거래일만 제공(2026-11-18 실측 11-10~11-17) — 그 이전 신호는 분봉 없음(skipped_no_minute)으로 정직하게 보고",
        "당일 종가 = 그날 마지막 정규장(09:00~15:30) 분봉 종가 — Naver 시세 기준(일봉 market_data 종가와 공급원이 다름)",
        "진입 t+1 시가·왕복 비용 0.21%·슬리피지 0 — capital_sim 과 동일(import 재사용)",
        "MaxDD 는 일별 마감 자본곡선(현금+보유 평가액) 피크 대비 — 장중 극값 미반영",
        "스윙 보유 중 세션 분봉 공백은 마지막 실측 분봉 종가로 청산(no_minute_gap) — 추정 없음",
    ]
    report["claim"] = {
        "runner": "minute_replay",
        "source_rows": {"market_data_fetched": int(n_loaded),
                        "signals_matched": int(raw_n),
                        "minute_rows_fetched": int(minute_stats["rows"])},
        "parsed_rows": {"signals_simulatable": int(len(signals)),
                        "minute_paths": int(minute_stats["paths"]),
                        "segment_runs": int(len(results) * 3),
                        "daily_band_runs": int(len(results) * 3 * 2)},
        "persisted_rows": {"result_records": int(len(results) * 3),
                           "json_file": 1, "md_file": 1,
                           "json_out": cfg["json_out"], "md_out": cfg["md_out"]},
        "limits": report["limits"],
        "note": "DB 는 SELECT 만(minute_close_bars 조회) — dq_claim.record_claim 은 미사용, 출력 "
                "JSON claim 절에 소스 수신/파서 생성/실제 저장 3분리 자기신고 내장(capital_sim 과 "
                "같은 관례). 실행 시각 미기록 → 같은 입력이면 같은 바이트(멱등). 재실행 시 파일 덮어쓰기.",
    }
    return report


def build_md(rep) -> str:
    """MD 보고서 — 규칙별 분봉 재생 vs 일봉 밴드 → 커버리지 → 제약 → 워크포워드 → 한계."""
    L = []  # noqa: E741
    a = L.append
    dr, md_, par = rep["data_range"], rep["minute_data"], rep["params"]
    a("# 분봉 재생 — '일봉 애매(worst≠best)' 를 실제 1분 종가 순서로 확정 (스펙 v3)")
    a("")
    a("- 데이터: `market_data` 신호 %s~%s(정의=capital_sim) + `minute_close_bars` %d행(%d종목 · "
      "%d일 · %s~%s) · 진입 t+1 시가 · 왕복 비용 0.21%% · 진입일 09:00~15:30 분봉 종가 재생"
      % (dr["start"], dr["end"], md_["rows"], md_["stocks"], md_["days"],
         md_["min_date"], md_["max_date"]))
    a("- **판정(스펙 §2)**: 분봉 `close` 가 임계(진입가×(1±pct))를 처음 넘은 분에서 그 분 종가 "
      "체결 · 같은 분 양방향은 보수적으로 손절 우선 · 미도달 시 당일 종가(=마지막 정규장 분봉) 청산")
    a("- **한계(첫 단락 명시)**: Naver 분봉은 OHLC null → **종가 경로만**(1분 종가 교차 = 근사). "
      "고가/저가 부재로 '같은 분 양방향' 교차는 구조적으로 관측 불가(손절<익절) — 애매동일분 0 이 "
      "정상이다.")
    a("")
    a("## 1. 규칙별 결과 — 분봉 재생 vs 일봉 worst/best 밴드")
    a("")
    a("| 규칙 | 구간 | n | 승률%% | 평균이익%% | 평균손실%% | R | 기대값%%/건 | 기대값원/건 | "
      "총손익 | MaxDD%% | 애매동일분 | 분봉없음 | 보유분 | 일봉worstEV | 일봉bestEV | 일봉n |")
    a("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rep["results"]:
        for seg in r["segments"]:
            m = seg
            a("| %s | %s | %d | %s | %s | %s | %s | %s | %s | %s | %s | %d | %d | %s | %s | %s | %d |"
              % (r["rule"], seg["segment"], m["trades"],
                 cs._f(m["win_pct"], ".1f"), cs._f(m["avg_win_pct"]),
                 cs._f(m["avg_loss_pct"]), cs._f(m["r_multiple"]),
                 cs._f(m["expectancy_pct"]), cs._f(m["expectancy_krw"], ".1f", "원"),
                 cs._w(m["total_pnl_krw"]), cs._f(m["maxdd_pct"], ".2f"),
                 m["ambiguous_same_minute"], m["skipped_no_minute"],
                 m["avg_hold_minutes"] if m["avg_hold_minutes"] is not None else "—",
                 cs._f(m["daily_worst_ev"]), cs._f(m["daily_best_ev"]), m["daily_n"]))
    a("")
    a("## 2. 분봉 커버리지")
    a("")
    a("- 커버리지 = 체결 n / (체결 n + 분봉없음 skipped_no_minute) — 자본·가격대 등 다른 사유로 "
      "탈락한 신호는 분모에 없다(일봉 대체 금지, 스펙).")
    a("")
    for r in rep["results"]:
        for seg in r["segments"]:
            cov = seg["coverage_pct"]
            a("- %s/%s(신호 %d): 체결 %d + 분봉없음 %d → **커버리지 %s%%**"
              % (r["rule"], seg["segment"], seg["signals"], seg["trades"],
                 seg["skipped_no_minute"],
                 ("%.1f" % cov) if cov is not None else "—"))
    a("")
    a("## 3. 진입 제약 (skipped — capital_sim 과 동일 집계 + 분봉 없음)")
    a("")
    a("| 규칙 | 구간 | 자본부족 | 가격대 | 슬롯초과 | 일일한도 | 중복보유 | 분봉없음 |")
    a("|---|---|---|---|---|---|---|---|")
    for r in rep["results"]:
        for seg in r["segments"]:
            m = seg
            a("| %s | %s | %d | %d | %d | %d | %d | %d |"
              % (r["rule"], seg["segment"], m["skipped_capital"],
                 m["skipped_price_band"], m["skipped_slots"], m["skipped_daily_max"],
                 m["skipped_dup"], m["skipped_no_minute"]))
    a("")
    a("## 4. 워크포워드 분할 (앞/뒤, 재조정 없음)")
    a("")
    a("- 분할 기준 신호일: %s~%s / %s~%s · 각 구간은 동일 시작자본 %s원으로 독립 실행 · 후자는 "
      "재조정 없음(규칙은 CLI 고정 — 선택 절차 자체가 없음)."
      % (dr["start"], dr["walkforward_mid"], dr["walkforward_mid"], dr["end"],
         "{:,.0f}".format(par["equity"])))
    a("")
    a("## 5. 한계")
    a("")
    for li in rep["limits"]:
        a("- %s" % li)
    return "\n".join(L) + "\n"


# -------------------------------------------------------------------- 데이터 로드
def load_minute(conn, start, end):
    """minute_close_bars SELECT 전용(쓰기 없음) — {(stock, iso_date): [close...]} 시간 오름차순."""
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT stock_code, trade_date, time, close_price FROM minute_close_bars"
            " WHERE trade_date BETWEEN %s AND %s"
            " ORDER BY stock_code, trade_date, time", (start, end))
        rows = cur.fetchall()
    finally:
        cur.close()
    minute = {}
    for code, td, _tm, px in rows:
        minute.setdefault((str(code), td.isoformat()), []).append(float(px))
    stats = {"rows": len(rows), "paths": len(minute),
             "stocks": len({k[0] for k in minute}),
             "days": len({k[1] for k in minute}),
             "min_date": min((k[1] for k in minute), default=None),
             "max_date": max((k[1] for k in minute), default=None)}
    return minute, stats


# ---------------------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="분봉 재생 (docs/spec_minute_replay.md §2)")
    ap.add_argument("--from", dest="from_", required=True)
    ap.add_argument("--to", required=True)
    ap.add_argument("--rule", choices=RULE_IDS, default=None,
                    help="기본: 3규칙 모두")
    ap.add_argument("--equity", type=float, default=245435.0)
    ap.add_argument("--max-pos", type=int, default=3)
    ap.add_argument("--per-stock-pct", type=float, default=0.3334)
    ap.add_argument("--daily-max", type=int, default=3)
    ap.add_argument("--price-min", type=float, default=1000.0)
    ap.add_argument("--price-max", type=float, default=50000.0)
    ap.add_argument("--same-day-close", dest="same_day_close", action="store_true",
                    default=True, help="당일 종가 청산(기본 on)")
    ap.add_argument("--no-same-day-close", dest="same_day_close", action="store_false")
    ap.add_argument("--hold-sessions", type=int, default=5,
                    help="스윙 프로파일 최대 보유 세션")
    ap.add_argument("--json-out", default=str(REPO / "data/reports/minute_replay.json"))
    ap.add_argument("--md-out", default=str(REPO / "data/reports/minute_replay.md"))
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    if args.from_ >= args.to:
        ap.error("--from 은 --to 보다 앞이어야 합니다")

    warm = (date.fromisoformat(args.from_) - timedelta(days=cs.WARMUP_DAYS)).isoformat()
    load_end = (date.fromisoformat(args.to) + timedelta(days=cs.BUFFER_DAYS)).isoformat()
    df = esb.load(warm, load_end)                     # capital_sim 과 동일 SELECT 필터
    df = esb.compute_features(df)
    raw_n, signals, bars, dates = cs.build_signals(df, args.from_, args.to)
    conn = connect()
    try:
        minute, minute_stats = load_minute(conn, args.from_, load_end)
    finally:
        conn.close()
    profile = ("same_day" if args.same_day_close
               else "swing%d" % args.hold_sessions)
    engine_kw = {"equity0": args.equity, "max_pos": args.max_pos,
                 "per_stock_pct": args.per_stock_pct, "daily_max": args.daily_max,
                 "price_min": args.price_min, "price_max": args.price_max,
                 "slip_pct": 0.0, "fee_pct": cs.FEE_PCT,
                 "hold_sessions": args.hold_sessions}
    params = {"equity": args.equity, "max_pos": args.max_pos,
              "per_stock_pct": args.per_stock_pct, "daily_max": args.daily_max,
              "price_min": args.price_min, "price_max": args.price_max,
              "fee_pct": cs.FEE_PCT, "hold_sessions": args.hold_sessions,
              "profile": profile, "daily_profile": profile,
              "rules": [args.rule] if args.rule else list(RULE_IDS),
              "engine_kw": engine_kw}
    cfg = {"json_out": args.json_out, "md_out": args.md_out}
    rep = build_report(signals, bars, dates, minute, params, args.from_, args.to, cfg,
                       n_loaded=len(df), raw_n=raw_n, minute_stats=minute_stats)

    jtxt = json.dumps(rep, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    mdtxt = build_md(rep)
    Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.md_out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.json_out, "w", encoding="utf-8") as f:
        f.write(jtxt)
    with open(args.md_out, "w", encoding="utf-8") as f:
        f.write(mdtxt)

    if not args.quiet:
        print("[minute_replay] %s~%s 신호 %d → 시뮬레이션 %d · 분봉 %d행(%d종목 · %d일)"
              % (args.from_, args.to, rep["claim"]["source_rows"]["signals_matched"],
                 rep["claim"]["parsed_rows"]["signals_simulatable"],
                 minute_stats["rows"], minute_stats["stocks"], minute_stats["days"]))
        for r in rep["results"]:
            for seg in r["segments"]:
                m = seg
                cov = m["coverage_pct"]
                print("[결과] %s/%s: n=%d EV %s%%(%s원) PnL %s MaxDD %s%% skip(no_minute=%d) "
                      "ambiguous_same_minute=%d avg_hold_min=%s 커버리지 %s%% "
                      "일봉밴드 worst %s%% ~ best %s%%(n=%d)"
                      % (r["rule"], seg["segment"], m["trades"],
                         ("%+.2f" % m["expectancy_pct"]) if m["expectancy_pct"] is not None
                         else "—",
                         ("%+.1f" % m["expectancy_krw"]) if m["expectancy_krw"] is not None
                         else "—",
                         cs._w(m["total_pnl_krw"]),
                         ("%.2f" % m["maxdd_pct"]) if m["maxdd_pct"] is not None else "—",
                         m["skipped_no_minute"], m["ambiguous_same_minute"],
                         m["avg_hold_minutes"] if m["avg_hold_minutes"] is not None else "—",
                         ("%.1f" % cov) if cov is not None else "—",
                         ("%+.2f" % m["daily_worst_ev"]) if m["daily_worst_ev"] is not None
                         else "—",
                         ("%+.2f" % m["daily_best_ev"]) if m["daily_best_ev"] is not None
                         else "—", m["daily_n"]))
        cl = rep["claim"]
        print("[저장] %s (%dB) / %s (%dB)" % (args.json_out, len(jtxt.encode()),
                                              args.md_out, len(mdtxt.encode())))
        print("[자기신고] source=%s parsed=%s persisted=%s" % (
            cl["source_rows"], cl["parsed_rows"], cl["persisted_rows"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

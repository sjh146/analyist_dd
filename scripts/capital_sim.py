#!/usr/bin/env python3
"""자본 제약 시뮬레이터 — 실계좌 조건 그대로 + 부분익절/트레일링 (docs/spec_capital_sim.md v2).

계약 문서: docs/spec_capital_sim.md (2026-11-18). **측정 단계만** — 실주문 경로 변경 없음,
DB 는 SELECT 전용(주문·브리지·트레이더 파일 무접근 — 스펙 금지 조항).

WHY (스펙 §왜 v2 인가 — v1 의 한계, 실측):
  1. 자본 제약 무시: 자본 245,435원·종목당 상한 1/3(≈81,811원)이라 고가주는 0주다.
     v1 은 그런 종목도 100% 체결된 것으로 계산했다.
  2. 동시 보유·일일 건수 무시: 동시 3종목·일 3건 한도와 15:00–15:20 강제청산 때문에
     실제로는 못 들어간 신호가 많다.
  3. MaxDD 측정 불가: v1 은 전액 재투입 복리로 5,873% 같은 값이 나와 null 계약으로 바꿨다.
     자본곡선을 실제로 굴려야 MaxDD 가 의미를 갖는다(피크 대비 일별 마감 자본 기준).

실측 조회 명령(2026-11-18 실행, 접속은 이 프로세스 환경변수 — 비밀번호 출력 금지):
  -- SELECT MIN(trade_date), MAX(trade_date), COUNT(*), COUNT(DISTINCT stock_code)
  -- FROM market_data;
  -- → 2023-01-02 ~ 2026-11-17 · 3,227,630행 · 3,980종목
  -- SELECT COUNT(*) FROM market_data WHERE trade_date BETWEEN '2026-04-01' AND '2026-11-14'
  --   AND close_price >= 1000 AND open_price > 0;
  -- → 548,792행 (v1 과 동일 필터)
  -- SELECT trade_date, close_price FROM market_data WHERE stock_code='096530'
  --   ORDER BY trade_date DESC LIMIT 3;
  -- → 2026-11-16 종가 24,200원 → floor(81,811.67/24,200) = 3주 (스펙 인용 실측 교차확인)
파일 근거: data/reports/edge_stack_backtest.json (v1, 2026-11-17) — 신호 raw 14,559 ·
  시뮬레이션 14,440건. 시작 자본에서 종목당 예산 = min(245,435×0.3334, 245,435/3) =
  min(81,828.03, 81,811.67) = 81,811.67원 (남은예산/남은슬롯이 상한을 지배).

구현 선택(모두 코드 주석에 사유):
  - edge_score 는 scripts/edge_stack.py 의 compute_edge() 를 **import 해서** 계산(복붙 금지).
    신호봉 직전 30봉만 넘긴다(compute_edge 는 MA20·HHV20·ATR14 산출에 30봉이면 충분 —
    전체 이력을 넘기는 것과 동일한 값, 미래 정보 없음).
  - 신호 정의는 v1 과 동일: chg>=3% · vol_ratio>=1.5 · 거래대금>=5억, 진입 t+1 시가.
    피처(chg·vol_ratio·nxt_*)는 scripts/edge_stack_backtest.py 의 load/compute_features 를
    **import 해서** 재사용(스펙 금지 '기존 스크립트 수정 금지 — import 만').
  - 수량 = floor(min(equity_now×per_stock_pct, 남은현금/남은슬롯) / 진입시가).
    equity_now = 현금 + 보유종목의 직전 세션 종가 평가(미실현 포함 — 일별 마감 기준).
  - 슬롯·건수 체크 순서: 가격대 → 중복보유 → 동시보유 → 일일건수 → 자본(skipped_* 기록).
    가격대는 진입가(t+1 시가) 기준, --price-min/--price-max 둘 다 적용.
  - 진입은 그날 기존 보유의 세션 청산 처리 **후** (빈 슬롯·현금 반영) — 결정적 순서.
    같은 날 신규 진입은 종목코드 오름차순으로 처리(동일일 내 순서 고정, v1 MaxDD 관례와 동일).
  - 청산 규칙 4종(스펙 표 그대로, 사후 조정 금지):
      fixed_1_3   손절 −1% / 익절 +3% (v1 최고)
      fixed_15_45 손절 −1.5% / 익절 +4.5%
      partial_1r  손절 −1% · +1R(=+1%)에서 절반 익절(수량 floor(qty/2) — 홀수면 나머지가 잔량,
                  qty=1 이면 부분익절 없음) · 잔량 +3R(=+3%) 또는 손절 · 5세션 타임스탑
      trail_atr   손절 −1% · +1R(=+1%) 도달 후 ATR 트레일(고점 −1×ATR14) · 5세션 타임스탑.
                  트레일 스탑은 초기 손절가 아래로 내려가지 않는다(max(초기손절, 고점−ATR) —
                  '손절 −1%' 계약 유지). ATR14 = TR 14일 단순평균(edge_stack 정본과 동일 정의,
                  Wilder 아님)이고 세션봉 t 시점 값(미래 정보 없음). 트레일 스탑은 그날
                  고가에서 '만들어지므로' 같은 봉의 저가에 걸리는지가 장중 순서에 따라 갈린다 →
                  worst = 비관적 순서(기존 스탑을 깨면 낮은 가격에, 오늘 상향된 스탑도 저가에
                  닿으면 그 가격에 청산) / best = 낙관적 순서(고가 먼저, 기존 스탑을 깬
                  경우에만 상향된 가격에 청산 — 기존 스탑 위의 저가는 생존).
  - 프로파일: same_day(단타 — 진입일 종가 강제청산, 15:00~15:20 강제청산의 프록시) /
    swing5(스윙 — 5세션 타임스탑, 세션은 종목이 실제 거래한 봉만 센다). 둘 다 돌린다.
  - 일봉엔 장중 순서가 없다 → v1 과 같은 규율로 worst(저가 먼저=손절 우선)/best(고가 먼저=
    익절 우선) 두 극단. ambiguous_pct = worst/best 결과(체결집합·손익)가 다른 거래 비율로
    v1 의 '당일 both-touch'를 다세션·부분익절로 일반화한 정의.
  - 비용: 왕복 0.21%(v1·실체결과 동일) — 거래당 1회, entry_cost×0.21% 를 최종 청산 시 차감.
    슬리피지 --slip-pct(기본 0): 진입가×(1+s), 청산가×(1−s) 적용, 보고서에 명시.
  - MaxDD: 일별 마감 자본곡선(현금+보유 평가액) 피크 대비 낙폭 %, 크기는 양수로 기록.
  - 워크포워드: v1 과 같은 분할(선택 04-01~08-15 / 홀드아웃 08-16~11-14, 신호일 기준).
    선택 구간 worst 기대값 순으로 규칙을 고르고 홀드아웃은 재조정 없이 적용. 추천 조건 4개는
    모두 홀드아웃 worst 기준(스펙: ①② 명시 홀드아웃, ③④ 문맥상 상속 — 코드에 고정).
    충족 조합이 없으면 "추천 없음" — 억지 추천 금지. 각 구간은 동일 시작자본으로 독립 실행.

멱등: 출력(JSON/MD)에 실행 시각을 넣지 않는다. 같은 입력이면 같은 바이트(tests ⑧).
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

REPO = Path(__file__).resolve().parent.parent
SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
import edge_stack  # noqa: E402  — compute_edge (스펙: import 해서 계산, 복붙 금지)
import edge_stack_backtest as esb  # noqa: E402  — load/compute_features (스펙: import 만)

FEE_PCT = 0.21                 # 왕복 비용 %(수수료+거래세, v1·실체결과 동일 — 스펙 기준선)
WARMUP_DAYS = 120              # 피처 워밍업(v1 과 동일 — 4월 초 신호도 vol_ratio 산출)
BUFFER_DAYS = 10               # 스윙 5세션 여유(말미 진입의 세션 봉 확보용)
DEFAULT_START, DEFAULT_END = "2026-04-01", "2026-11-14"
SELECT_END, HOLDOUT_START = "2026-08-15", "2026-08-16"   # 워크포워드 분할(스펙 고정)
MIN_HOLD_TRADES = 100          # 추천 조건 ③ 홀드아웃 표본(스펙: ≥100건)
REC_MIN_EV = 0.10              # 추천 조건 ① 홀드아웃 기대값 %/건(스펙: > +0.10)
REC_MAX_DD = 15.0              # 추천 조건 ④ MaxDD %(스펙: ≤ 15, 자본 대비)

# 청산 규칙 4종(스펙 표 그대로 — 사후 조정 금지)
RULES = {
    "fixed_1_3":   {"kind": "fixed", "stop_pct": 1.0, "tp_pct": 3.0},
    "fixed_15_45": {"kind": "fixed", "stop_pct": 1.5, "tp_pct": 4.5},
    "partial_1r":  {"kind": "partial", "stop_pct": 1.0, "part_r": 1.0, "tp_r": 3.0},
    "trail_atr":   {"kind": "trail", "stop_pct": 1.0, "act_r": 1.0, "atr_mult": 1.0},
}
RULE_IDS = ("fixed_1_3", "fixed_15_45", "partial_1r", "trail_atr")


# ------------------------------------------------------------------------- 수량
def entry_budget(equity_now, cash, slots_used, max_pos, per_stock_pct):
    """종목당 예산 = min(equity_now×per_stock_pct, 남은현금/남은슬롯) (스펙 §진입)."""
    cap = float(equity_now) * float(per_stock_pct)
    rem_slots = max(0, int(max_pos) - int(slots_used))
    cash_budget = float(cash) / float(rem_slots) if rem_slots > 0 else 0.0
    return min(cap, cash_budget)


def size_qty(price, budget):
    """정수 수량 내림 floor(budget/price). 가격·예산 비정상이면 0(진입 불가)."""
    if price is None or price <= 0 or budget is None or budget <= 0:
        return 0
    return int(budget // price)


# ---------------------------------------------------------------------- 시뮬레이터
def _bar_at(bars, stock, d64):
    """종목의 해당일 봉 위치(해당일 거래 없으면 None). 결정적 searchsorted."""
    b = bars[stock]
    i = int(np.searchsorted(b["dates"], d64))
    if i >= len(b["dates"]) or b["dates"][i] != d64:
        return None
    return i


def _sell(ctx, pos, px, qty, reason):
    """일부/전부 청산. 슬리피지는 매도가 하향(px×(1−s)) 적용."""
    eff = float(px) * (1.0 - ctx["slip_pct"] / 100.0)
    ctx["cash"] += qty * eff
    pos["qty"] -= qty
    pos["proceeds"] += qty * eff
    pos["exits"].append([reason, round(float(px), 4), int(qty)])


def _finalize(pos):
    pnl = pos["proceeds"] - pos["entry_cost"] - pos["fee"]
    return {"sig_idx": pos["sig_idx"], "stock": pos["stock"],
            "entry_date": pos["entry_date"].date().isoformat(),
            "sessions": pos["sessions"],
            "pnl_krw": pnl, "ret_pct": pnl / pos["entry_cost"] * 100.0,
            "exits": pos["exits"]}


def _session(ctx, pos, h, l, c, atr, rule_def, mode, profile, hold_sessions):
    """보유 포지션의 한 세션 봉 처리. 완전 청산되면 qty==0 이 된다(호출부에서 정리)."""
    kind = rule_def["kind"]
    stop_pct = rule_def["stop_pct"]
    entry = pos["entry_price"]
    if kind == "fixed":
        stop_px = entry * (1.0 - stop_pct / 100.0)
        tp_px = entry * (1.0 + rule_def["tp_pct"] / 100.0)
        if mode == "worst":                       # 저가 먼저 → 손절 우선
            if l <= stop_px:
                _sell(ctx, pos, stop_px, pos["qty"], "stop")
                return
            if h >= tp_px:
                _sell(ctx, pos, tp_px, pos["qty"], "target")
                return
        else:                                     # 고가 먼저 → 익절 우선
            if h >= tp_px:
                _sell(ctx, pos, tp_px, pos["qty"], "target")
                return
            if l <= stop_px:
                _sell(ctx, pos, stop_px, pos["qty"], "stop")
                return
    elif kind == "partial":
        stop_px = entry * (1.0 - stop_pct / 100.0)
        part_px = entry * (1.0 + stop_pct / 100.0 * rule_def["part_r"])   # +1R
        tp_px = entry * (1.0 + stop_pct / 100.0 * rule_def["tp_r"])       # +3R
        if mode == "worst":                       # 손절 우선 — 부분익절보다 먼저
            if l <= stop_px:
                _sell(ctx, pos, stop_px, pos["qty"], "stop")
                return
            if h >= part_px:
                q = pos["qty"] // 2
                if q > 0:
                    _sell(ctx, pos, part_px, q, "partial_1r")
                if h >= tp_px:
                    _sell(ctx, pos, tp_px, pos["qty"], "target_3r")
                    return
        else:
            if h >= part_px:
                q = pos["qty"] // 2
                if q > 0:
                    _sell(ctx, pos, part_px, q, "partial_1r")
                if h >= tp_px:
                    _sell(ctx, pos, tp_px, pos["qty"], "target_3r")
                    return
                if l <= stop_px:
                    _sell(ctx, pos, stop_px, pos["qty"], "stop")
                    return
            elif l <= stop_px:
                _sell(ctx, pos, stop_px, pos["qty"], "stop")
                return
    elif kind == "trail":
        st = pos["state"]
        stop_initial = entry * (1.0 - stop_pct / 100.0)
        act_px = entry * (1.0 + stop_pct / 100.0 * rule_def["act_r"])     # +1R 활성화
        # 오늘 고가로 갱신될 스탑(고점 −1×ATR14, 초기 손절가 아래로는 내려가지 않음 —
        # '손절 −1%' 계약 유지). 트레일 스탑은 그날 고가에서 "만들어지므로" 같은 봉의
        # 저가에 걸리는지 여부가 장중 순서에 따라 갈린다 → worst/best 로 나눈다:
        #   worst = 비관적 순서 — 기존 스탑을 깨면 낮은 가격에, 오늘 상향된 스탑도
        #           저가에 닿으면 그 가격에 청산(가능한 어느 순서든 불리한 쪽 확정).
        #   best  = 낙관적 순서 — 고가 먼저(상향 후) 저가. 기존 스탑을 깬 경우에만
        #           청산하되 상향된 가격에, 기존 스탑 위의 저가는 생존.
        new_wm = max(st["watermark"], h)
        activ_now = st["activated"] or new_wm >= act_px
        new_stop = st["stop_px"]
        if activ_now and atr is not None and not np.isnan(atr) and atr > 0:
            new_stop = max(stop_initial, new_wm - atr * rule_def["atr_mult"])
        if mode == "worst":
            if l <= st["stop_px"]:
                _sell(ctx, pos, st["stop_px"], pos["qty"],
                      "trail" if st["activated"] else "stop")
                return
            st["watermark"], st["activated"], st["stop_px"] = new_wm, activ_now, new_stop
            if l <= new_stop:                     # 상향된 스탑이 저가에 걸리는 비관 경로
                _sell(ctx, pos, new_stop, pos["qty"], "trail")
                return
        else:
            old_stop = st["stop_px"]
            st["watermark"], st["activated"], st["stop_px"] = new_wm, activ_now, new_stop
            if l <= old_stop:                     # 기존 스탑을 깬 경우만 청산(상향된 가격에)
                _sell(ctx, pos, st["stop_px"], pos["qty"],
                      "trail" if st["activated"] else "stop")
                return
    # 봉 생존 → 세션 증가, 프로파일별 타임스탑/강제청산
    pos["sessions"] += 1
    pos["last_close"] = c
    if profile == "same_day":
        _sell(ctx, pos, c, pos["qty"], "eod")
        return
    if profile.startswith("swing") and pos["sessions"] >= hold_sessions:
        _sell(ctx, pos, c, pos["qty"], "time_stop")
        return


def run_capital_sim(signals, bars, rule_id, profile, mode, *, equity0, max_pos,
                    per_stock_pct, daily_max, price_min, price_max,
                    slip_pct=0.0, fee_pct=FEE_PCT, hold_sessions=5, dates):
    """원화 자본곡선 시뮬레이션 1회(worst|best 단일 경로).

    signals: list[dict(stock, sig_idx, entry_date(Timestamp), entry_open, edge_score)]
             — (entry_date, stock) 정렬되어 있어야 한다(빌드 단계에서 보장).
    bars   : {stock: dict(dates[datetime64 ns], high, low, close, atr14)} — 날짜 오름차순.
    dates  : 거래일 축 list[Timestamp] (전체 유니버스의 거래일 합집합, 오름차순).

    반환: {"metrics": dict, "trades": list, "skips": dict, "curve": [(iso, eq), ...]}
    """
    rule_def = RULES[rule_id]
    ctx = {"cash": float(equity0), "slip_pct": float(slip_pct)}
    positions, trades, new_positions = [], [], []
    skips = {"price_band": 0, "dup": 0, "slots": 0, "daily_max": 0, "capital": 0}
    daily = {}
    held = set()
    curve = []
    by_date = {}
    for s in signals:
        by_date.setdefault(s["entry_date"], []).append(s)
    for ts in dates:
        d64 = ts.to_datetime64()
        # 1) 기존 보유의 세션 청산(그날의 빈 슬롯·현금이 곧장 신규 진입에 반영)
        for pos in positions[:]:
            i = _bar_at(bars, pos["stock"], d64)
            if i is None:                       # 이 종목 오늘 휴장 → 세션 없음(캐리)
                continue
            b = bars[pos["stock"]]
            _session(ctx, pos, b["high"][i], b["low"][i], b["close"][i], b["atr14"][i],
                     rule_def, mode, profile, hold_sessions)
            if pos["qty"] <= 0:
                positions.remove(pos)
                held.discard(pos["stock"])
                trades.append(_finalize(pos))
        # 2) 당일 신규 진입(체크 순서 고정: 가격대 → 중복 → 슬롯 → 일일건수 → 자본)
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
            budget = entry_budget(eq_now, ctx["cash"], len(positions), max_pos,
                                  per_stock_pct)
            px_eff = px * (1.0 + slip_pct / 100.0)
            qty = size_qty(px_eff, budget)
            if qty <= 0:
                skips["capital"] += 1
                continue
            cost = qty * px_eff
            ctx["cash"] -= cost
            pos = {"stock": sig["stock"], "sig_idx": sig["sig_idx"],
                   "entry_date": sig["entry_date"], "entry_price": px, "qty": qty,
                   "entry_cost": cost, "fee": cost * fee_pct / 100.0,
                   "proceeds": 0.0, "sessions": 0, "last_close": px, "exits": [],
                   "state": ({"activated": False, "watermark": px,
                              "stop_px": px * (1.0 - rule_def["stop_pct"] / 100.0)}
                             if rule_def["kind"] == "trail" else None)}
            positions.append(pos)
            held.add(sig["stock"])
            daily[ts] = daily.get(ts, 0) + 1
            new_positions.append(pos)
        # 3) 오늘 진입한 포지션의 진입봉 세션(진입 시가 → 그 봉의 고/저/종가)
        for pos in new_positions:
            i = _bar_at(bars, pos["stock"], d64)
            if i is None:
                continue                      # 진입봉이 항상 존재해야 정상 — 방어적 스킵
            b = bars[pos["stock"]]
            _session(ctx, pos, b["high"][i], b["low"][i], b["close"][i], b["atr14"][i],
                     rule_def, mode, profile, hold_sessions)
            if pos["qty"] <= 0:
                positions.remove(pos)
                held.discard(pos["stock"])
                trades.append(_finalize(pos))
        # 4) 일별 마감 자본곡선(현금 + 보유 평가액 — 미실현 포함)
        eq = ctx["cash"] + sum(p["qty"] * p["last_close"] for p in positions)
        curve.append((ts.date().isoformat(), eq))
    # 데이터 끝까지 못 닫은 포지션 → 마지막 알려진 종가로 강제 청산
    for pos in positions[:]:
        _sell(ctx, pos, pos["last_close"], pos["qty"], "end_of_data")
        positions.remove(pos)
        trades.append(_finalize(pos))
    metrics = _summarize(trades, curve, skips, float(equity0))
    return {"metrics": metrics, "trades": trades, "skips": skips, "curve": curve}


def _r4(x):
    """JSON 직렬화 + 결정적(고정 소수 4자리) 변환. None/NaN → None."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(v):
        return None
    return round(v, 4)


def _r2(x):
    """원화 소수 2자리 반올림."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(v):
        return None
    return round(v, 2)


def max_drawdown(curve):
    """자본곡선 피크 대비 최대 낙폭. curve: [(iso, equity)]. 반환: 크기(%) 양수 기록."""
    if len(curve) < 2:
        return None
    peak = curve[0][1]
    maxdd, peak_at, trough_at = 0.0, curve[0][1], curve[0][1]
    for _, eq in curve[1:]:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100.0 if peak > 0 else 0.0
        if dd > maxdd:
            maxdd, peak_at, trough_at = dd, peak, eq
    return {"maxdd_pct": maxdd, "peak": peak_at, "trough": trough_at}


def _summarize(trades, curve, skips, equity0):
    """단일 모드(worst|best) 성과 통계(스펙 산출 지표)."""
    out = {
        "trades": int(len(trades)), "win_pct": None, "avg_win_pct": None,
        "avg_loss_pct": None, "r_multiple": None, "expectancy_pct": None,
        "expectancy_krw": None, "total_pnl_krw": None, "equity_end_krw": None,
        "maxdd_pct": None, "maxdd_peak_krw": None, "maxdd_trough_krw": None,
        "skipped_capital": int(skips["capital"]),
        "skipped_price_band": int(skips["price_band"]),
        "skipped_slots": int(skips["slots"]),
        "skipped_daily_max": int(skips["daily_max"]),
        "skipped_dup": int(skips["dup"]),
        "ambiguous_pct": None, "avg_hold_sessions": None,
        "n_time_stop": 0, "n_eod": 0, "n_end_of_data": 0,
        "n_partial_exits": 0, "n_trail_exits": 0,
    }
    if not trades:
        out["equity_end_krw"] = _r2(float(equity0))
        dd = max_drawdown(curve)
        if dd is not None:
            out["maxdd_pct"] = _r4(dd["maxdd_pct"])
            out["maxdd_peak_krw"] = _r2(dd["peak"])
            out["maxdd_trough_krw"] = _r2(dd["trough"])
        return out
    pnls = np.array([t["pnl_krw"] for t in trades])
    rets = np.array([t["ret_pct"] for t in trades])
    wins, losses = pnls > 0, pnls < 0
    out["win_pct"] = _r4(wins.mean() * 100.0)
    out["avg_win_pct"] = _r4(rets[wins].mean()) if wins.any() else None
    out["avg_loss_pct"] = _r4((-rets[losses]).mean()) if losses.any() else None
    if out["avg_win_pct"] is not None and out["avg_loss_pct"] is not None:
        out["r_multiple"] = _r4(out["avg_win_pct"] / out["avg_loss_pct"])
    out["expectancy_pct"] = _r4(rets.mean())
    out["expectancy_krw"] = _r2(pnls.mean())
    out["total_pnl_krw"] = _r2(pnls.sum())
    out["equity_end_krw"] = _r2(float(equity0) + float(pnls.sum()))
    dd = max_drawdown(curve)
    if dd is not None:
        out["maxdd_pct"] = _r4(dd["maxdd_pct"])
        out["maxdd_peak_krw"] = _r2(dd["peak"])
        out["maxdd_trough_krw"] = _r2(dd["trough"])
    out["avg_hold_sessions"] = _r4(np.mean([t["sessions"] for t in trades]))
    for t in trades:
        for leg in t["exits"]:
            reason = leg[0]
            if reason == "time_stop":
                out["n_time_stop"] += 1
            elif reason == "eod":
                out["n_eod"] += 1
            elif reason == "end_of_data":
                out["n_end_of_data"] += 1
            elif reason == "partial_1r":
                out["n_partial_exits"] += 1
            elif reason == "trail":
                out["n_trail_exits"] += 1
    return out


def ambiguous_pct(worst_trades, best_trades):
    """worst/best 결과가 다른 거래 비율 % (다세션·부분익절로 일반화한 '애매일' 정의).

    한쪽에만 존재하는 거래도 '결과 다름'으로 센다(자본 경로 차이로 체결집합이 갈린 경우).
    """
    by_idx = {}
    for t in worst_trades:
        by_idx[t["sig_idx"]] = [t["pnl_krw"], None]
    for t in best_trades:
        rec = by_idx.get(t["sig_idx"])
        if rec is None:
            by_idx[t["sig_idx"]] = [None, t["pnl_krw"]]
        else:
            rec[1] = t["pnl_krw"]
    if not by_idx:
        return None
    amb = 0
    for w, b in by_idx.values():
        if w is None or b is None:
            if (w is None) != (b is None):
                amb += 1
        elif abs(w - b) > 1e-6:
            amb += 1
    return _r4(amb / len(by_idx) * 100.0)


# ---------------------------------------------------------------------- 데이터
def build_signals(df, start, end):
    """신호 정의(v1 동일) + t+1 진입 데이터 + edge_score(compute_edge import).

    반환: (raw_n, signals, bars, dates). signals 는 (entry_date, stock) 정렬.
    bars 는 엔진 입력 구조 {stock: dict(dates, high, low, close, atr14, open)}.
    """
    sig = df[(df["trade_date"] >= pd.Timestamp(start))
             & (df["trade_date"] <= pd.Timestamp(end))]
    raw = sig[(sig["chg"] >= 3.0) & (sig["vol_ratio"] >= 1.5)
              & (sig["trading_value"] >= 5e8)]
    ok = raw[(raw["nxt_open"] > 0) & (raw["nxt_high"] > 0)
             & (raw["nxt_low"] > 0) & (raw["nxt_close"] > 0)]
    # 진입일(nxt_date) 순서, 동일일 내 종목코드로 고정(결정적 — v1 MaxDD 관례와 동일)
    ok = ok.sort_values(["nxt_date", "stock_code"])

    bars = {}
    for code, fr in df.groupby("stock_code", sort=True):
        fr = fr.reset_index(drop=True)
        bars[str(code)] = {
            "dates": fr["trade_date"].to_numpy(dtype="datetime64[ns]"),
            "open": fr["open_price"].to_numpy(dtype=float),
            "high": fr["high_price"].to_numpy(dtype=float),
            "low": fr["low_price"].to_numpy(dtype=float),
            "close": fr["close_price"].to_numpy(dtype=float),
            "atr14": fr["atr14"].to_numpy(dtype=float),   # NaN = 이력 부족(트레일 갱신 보류)
            "_fr": fr,                                    # 엣지 윈도우용(엔진 미사용)
        }

    signals = []
    for row in ok.itertuples(index=False):
        stock = str(row.stock_code)
        b = bars[stock]
        i = int(np.searchsorted(b["dates"], row.trade_date.to_datetime64()))
        lo = max(0, i - 29)                     # compute_edge 는 30봉이면 충분(모듈 docstring)
        win = [{"open": r.open_price, "high": r.high_price, "low": r.low_price,
                "close": r.close_price, "volume": r.volume,
                "trading_value": r.trading_value}
               for r in b["_fr"].iloc[lo: i + 1].itertuples(index=False)]
        score = edge_stack.compute_edge(win)["edge_score"]   # 스펙: import 해서 계산
        signals.append({"sig_idx": len(signals), "stock": stock,
                        "sig_date": row.trade_date, "entry_date": row.nxt_date,
                        "entry_open": float(row.nxt_open), "edge_score": score})
    all_dates = np.unique(df["trade_date"].to_numpy(dtype="datetime64[ns]"))
    dates = [pd.Timestamp(d) for d in all_dates]
    return int(len(raw)), signals, bars, dates


# ------------------------------------------------------------------ 워크포워드
def _run_pair(signals, bars, dates, params, rule, profile):
    kw = dict(equity0=params["equity"], max_pos=params["max_pos"],
              per_stock_pct=params["per_stock_pct"], daily_max=params["daily_max"],
              price_min=params["price_min"], price_max=params["price_max"],
              slip_pct=params["slip_pct"], fee_pct=params["fee_pct"],
              hold_sessions=params["hold_sessions"])
    w = run_capital_sim(signals, bars, rule, profile, "worst", dates=dates, **kw)
    b = run_capital_sim(signals, bars, rule, profile, "best", dates=dates, **kw)
    return w, b


def _conditions(m):
    """추천 조건 4개(스펙 §워크포워드) — 모두 홀드아웃 worst 기준.

    스펙 문구: '①홀드아웃 기대값 > +0.10%/건 ②홀드아웃 총손익 > 0 ③표본 ≥100건
    ④MaxDD ≤ 15%' — ①② 는 홀드아웃 명시, ③④ 는 문맥상 홀드아웃 상속(코드에 고정).
    """
    return {
        "c1_holdout_ev_gt_010": bool(m["expectancy_pct"] is not None
                                     and m["expectancy_pct"] > REC_MIN_EV),
        "c2_holdout_pnl_gt_0": bool(m["total_pnl_krw"] is not None
                                    and m["total_pnl_krw"] > 0),
        "c3_holdout_n_ge_100": bool(m["trades"] >= MIN_HOLD_TRADES),
        "c4_holdout_maxdd_le_15": bool(m["maxdd_pct"] is not None
                                       and m["maxdd_pct"] <= REC_MAX_DD),
    }


def _rank_key(pair):
    """선택 구간 worst 기준 순위: 기대값% ↓ → 총손익 ↓ → 표본 ↓ (None 은 최하위)."""
    m = pair[0]["metrics"]
    return (-(m["expectancy_pct"] if m["expectancy_pct"] is not None else -np.inf),
            -(m["total_pnl_krw"] if m["total_pnl_krw"] is not None else -np.inf),
            -m["trades"])


def walkforward(signals, bars, dates, params, start, end):
    """v1 과 같은 분할. 선택 구간에서 규칙을 고르고 홀드아웃은 재조정 없이 적용.

    분할 기준은 **신호일**(v1 trade_date 와 동일). 스윙 포지션은 구간 경계를 넘어
    완주한다(거래는 진입한 구간에 귀속). 각 구간은 동일 시작자본으로 독립 실행.
    """
    combos = [(r, p) for r in RULE_IDS for p in params["profiles"]]
    sel_sigs = [s for s in signals if s["sig_date"] <= pd.Timestamp(SELECT_END)]
    hold_sigs = [s for s in signals if s["sig_date"] >= pd.Timestamp(HOLDOUT_START)]
    sel, hold = {}, {}
    for rule, prof in combos:
        sel[(rule, prof)] = _run_pair(sel_sigs, bars, dates, params, rule, prof)
        hold[(rule, prof)] = _run_pair(hold_sigs, bars, dates, params, rule, prof)
    ranked = sorted(combos, key=lambda c: _rank_key(sel[c]))
    picks = []
    for rank, c in enumerate(ranked[:3], 1):
        rule, prof = c
        hw = hold[c][0]["metrics"]
        picks.append({
            "pick_rank": rank, "rule": rule, "hold": prof,
            "select": {"worst": sel[c][0]["metrics"], "best": sel[c][1]["metrics"]},
            "holdout": {"worst": hold[c][0]["metrics"], "best": hold[c][1]["metrics"]},
            "conditions": _conditions(hw),
        })
    return {"select_range": {"start": start, "end": SELECT_END},
            "holdout_range": {"start": HOLDOUT_START, "end": end},
            "note": "분할은 신호일 기준 · 각 구간은 동일 시작자본으로 독립 실행 · "
                    "판정은 보수적 worst 기준",
            "picks": picks, "_ranked": ranked, "_sel": sel, "_hold": hold}


def recommend(wf):
    """추천 1개(스펙: 4조건 모두 만족하는 조합만). 없으면 없다고 정직하게 쓴다."""
    limits = [
        "일봉 순서 불확실성 — worst/best 밴드로 계산하며 판정은 보수적 worst",
        "슬리피지 0 가정(--slip-pct 기본 0) — 호가 갭·부분체결 미반영",
        "유동성 가정 — 수량 = floor(예산/시가) 전량 체결 가정(호가 잔량 무시)",
        "생존편향 — market_data 는 상장 유지 종목만 있어 과거 진입 가능성이 과대평가될 수 있음",
        "15:00~15:20 강제청산은 당일 종가 청산(same_day 프로파일)으로 대체한 프록시",
        "MaxDD 는 일별 마감 자본곡선(현금+보유 평가액) 피크 대비 — 장중 극값은 미반영",
        "홀드아웃 1회(2026-08-16~11-14) — 우연성 배제 못함",
    ]
    cond_map = {}
    chosen = None
    for c in wf["_ranked"]:
        hw = wf["_hold"][c][0]["metrics"]
        cd = _conditions(hw)
        cond_map[c] = cd
        if all(cd.values()):
            chosen = c
            break
    if chosen is None:
        top = wf["_ranked"][0]
        tw = wf["_sel"][top][0]["metrics"]
        hw = wf["_hold"][top][0]["metrics"]
        cd = cond_map[top]
        failed = "/".join(str(i + 1) for i, ok in enumerate(cd.values()) if not ok)
        why = ("추천 조건(①②③④ 모두)을 만족하는 조합 없음. 선택 구간 최고(보수 worst) "
               "조합 %s/%s(선택 EV %s%%/건·n=%d)는 홀드아웃 조건 %s 미충족"
               % (top[0], top[1],
                  ("%+.2f" % tw["expectancy_pct"]) if tw["expectancy_pct"] is not None
                  else "—", tw["trades"], failed))
        return {"found": False, "rule": None, "hold": None, "why": why,
                "limits": limits, "conditions": cd}
    rule, prof = chosen
    sw = wf["_sel"][chosen][0]["metrics"]
    hw = wf["_hold"][chosen][0]["metrics"]
    why = ("홀드아웃(보수 worst) 기대값 %+.2f%%/건(>+0.10) · 총손익 %s원(>0) · "
           "표본 %d(≥100) · MaxDD %.2f%%(≤15) · 선택 구간(보수 worst) 기대값 %+.2f%%/건"
           % (hw["expectancy_pct"], ("%s" % hw["total_pnl_krw"]),
              hw["trades"], hw["maxdd_pct"], sw["expectancy_pct"]))
    return {"found": True, "rule": rule, "hold": prof, "why": why, "limits": limits,
            "conditions": cond_map[chosen], "select": sw, "holdout": hw,
            "mode": "worst"}


# ---------------------------------------------------------------------- 보고서
def build_report(signals, bars, dates, params, start, end, cfg, n_loaded, raw_n):
    """전체 보고 dict(결정적 — 실행 시각 없음). cfg = {"json_out", "md_out"}."""
    combos = [(r, p) for r in RULE_IDS for p in params["profiles"]]
    results = []
    for rule, prof in combos:
        w, b = _run_pair(signals, bars, dates, params, rule, prof)
        amb = ambiguous_pct(w["trades"], b["trades"])
        w["metrics"]["ambiguous_pct"] = amb
        b["metrics"]["ambiguous_pct"] = amb
        results.append({"rule": rule, "hold": prof, "ambiguous_pct": amb,
                        "worst": w["metrics"], "best": b["metrics"]})
    wf = walkforward(signals, bars, dates, params, start, end)
    rec = recommend(wf)

    scored = [s for s in signals if s["edge_score"] is not None]
    dist = {str(i): int(sum(1 for s in scored if s["edge_score"] == i))
            for i in range(8)}
    dist["none"] = int(len(signals) - len(scored))
    over = int(sum(1 for s in signals if s["entry_open"] > params["price_max"]))
    under = int(sum(1 for s in signals if s["entry_open"] < params["price_min"]))

    report = {
        "data_range": {"start": start, "end": end,
                       "source": "market_data — close>=1000 & open>0 · 신호 chg>=3% & "
                                 "vol_ratio>=1.5 & 거래대금>=5억 · 진입 t+1 시가 · "
                                 "왕복비용 0.21%"},
        "params": {"equity": params["equity"], "max_pos": params["max_pos"],
                   "per_stock_pct": params["per_stock_pct"],
                   "daily_max": params["daily_max"],
                   "price_min": params["price_min"], "price_max": params["price_max"],
                   "slip_pct": params["slip_pct"], "fee_pct": params["fee_pct"],
                   "same_day_close": "same_day" in params["profiles"],
                   "hold_sessions": params["hold_sessions"]},
        "signals": {"raw": int(raw_n), "simulatable": int(len(signals)),
                    "edge_scored": int(len(scored)), "edge_score_dist": dist,
                    "entry_open_over_price_max": over,
                    "entry_open_under_price_min": under},
        "results": results,
        "walkforward": {"select_range": wf["select_range"],
                        "holdout_range": wf["holdout_range"], "note": wf["note"],
                        "picks": wf["picks"]},
        "recommendation": rec,
    }
    report["claim"] = {
        "runner": "capital_sim",
        "source_rows": {"market_data_fetched": int(n_loaded),
                        "signals_matched": int(raw_n)},
        "parsed_rows": {"signals_simulatable": int(len(signals)),
                        "edge_scored": int(len(scored)),
                        "full_window_runs": int(len(results) * 2),
                        "walkforward_runs": int(len(combos) * 4)},
        "persisted_rows": {"result_records": int(len(results)),
                           "json_file": 1, "md_file": 1,
                           "json_out": cfg["json_out"], "md_out": cfg["md_out"]},
        "limits": rec["limits"],
        "note": "DB 쓰기 금지(스펙 'DB 는 SELECT 만')라 dq_claim.record_claim 은 미사용 — "
                "출력 JSON claim 절에 소스 수신/파서 생성/실제 저장 3분리 자기신고를 내장"
                "(edge_stack_backtest 와 같은 관례). 실행 시각 미기록 → 같은 입력이면 "
                "같은 바이트(멱등). 재실행 시 파일 덮어쓰기.",
    }
    return report


def _f(v, spec="+.2f", unit="%"):
    return (("%" + spec) % v) + unit if v is not None else "—"


def _w(v):
    return ("{:,.0f}원".format(v)) if v is not None else "—"


def build_md(rep) -> str:
    """MD 보고서 — 실계좌 조건 → 규칙별 표(보수/낙관) → 워크포워드 → 추천 → 한계."""
    L = []  # noqa: E741
    a = L.append
    dr, par, sig = rep["data_range"], rep["params"], rep["signals"]
    a("# 자본 제약 시뮬레이터 — 실계좌 조건 그대로 + 부분익절/트레일링 (스펙 v2)")
    a("")
    a("- 데이터: `market_data` %s ~ %s · 신호 %d건(시뮬레이션 %d건) · 진입 t+1 시가 · "
      "왕복 비용 0.21%%(실체결 검증) · 슬리피지 %.1f%%"
      % (dr["start"], dr["end"], sig["raw"], sig["simulatable"], par["slip_pct"]))
    a("- **일봉 한계(첫 단락 명시)**: 장중 순서가 없다 → 매 거래를 두 극단으로 계산. "
      "worst=손절 우선(저가 먼저) · best=익절 우선(고가 먼저). **보수적 worst 를 기본 보고, "
      "best 는 참고**이며 두 극단의 차이가 곧 순서 불확실성이다.")
    a("")
    a("## 1. 실계좌 조건")
    a("")
    a("- 자본 %s원(스펙 고정) · 종목당 상한 1/3(≈%s원) · 동시 보유 ≤%d · 일 신규 진입 ≤%d · "
      "가격대 %s~%s원(진입가 기준)"
      % (_w(par["equity"]),
         _w(min(par["equity"] * par["per_stock_pct"], par["equity"] / par["max_pos"])),
         par["max_pos"], par["daily_max"], "%s" % par["price_min"], "%s" % par["price_max"]))
    a("- 실측: 096530 2026-11-16 종가 24,200원 → floor(81,811.67/24,200) = **3주** "
      "(market_data 조회, 스펙 인용값 교차확인 — 2026-11-18)")
    a("- 신호 %d건 중 edge_score 기록 %d건(분포 %s) · 진입가가 가격대를 벗어난 신호: "
      "상한 초과 %d건 / 하한 미만 %d건"
      % (sig["simulatable"], sig["edge_scored"],
         " ".join("%s:%d" % (k, sig["edge_score_dist"][k]) for k in
                  ["%d" % i for i in range(8)] + ["none"]),
         sig["entry_open_over_price_max"], sig["entry_open_under_price_min"]))
    a("")
    a("## 2. 규칙별 결과 (전체 기간, worst=보수 / best=낙관)")
    a("")
    a("| 규칙 | 프로파일 | 모드 | n | 승률% | 평균이익% | 평균손실% | R | 기대값%/건 | "
      "기대값원/건 | 총손익 | 최종자본 | MaxDD% | 보유세션 |")
    a("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rep["results"]:
        for mode in ("worst", "best"):
            m = r[mode]
            a("| %s | %s | %s | %d | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |"
              % (r["rule"], r["hold"], mode, m["trades"],
                 _f(m["win_pct"], ".1f"), _f(m["avg_win_pct"]), _f(m["avg_loss_pct"]),
                 _f(m["r_multiple"]), _f(m["expectancy_pct"]), _f(m["expectancy_krw"], ".1f", "원"),
                 _w(m["total_pnl_krw"]), _w(m["equity_end_krw"]),
                 _f(m["maxdd_pct"], ".2f"), _f(m["avg_hold_sessions"], ".1f", "세션")))
    a("")
    a("## 3. 진입 제약 (skipped — 못 들어간 신호)")
    a("")
    a("| 규칙 | 프로파일 | 모드 | 자본부족 | 가격대 | 슬롯초과 | 일일한도 | 중복보유 | 애매% |")
    a("|---|---|---|---|---|---|---|---|---|---|")
    for r in rep["results"]:
        for mode in ("worst", "best"):
            m = r[mode]
            a("| %s | %s | %s | %d | %d | %d | %d | %d | %s |"
              % (r["rule"], r["hold"], mode, m["skipped_capital"],
                 m["skipped_price_band"], m["skipped_slots"], m["skipped_daily_max"],
                 m["skipped_dup"], _f(m["ambiguous_pct"], ".1f")))
    a("")
    a("- 애매% = worst/best 결과(체결집합·손익)가 다른 거래 비율 — v1 의 '당일 both-touch'를 "
      "다세션·부분익절·트레일링으로 일반화한 정의.")
    a("")
    a("## 4. 워크포워드 (선택 %s~%s → 홀드아웃 %s~%s, 재조정 없음)"
      % (rep["walkforward"]["select_range"]["start"], rep["walkforward"]["select_range"]["end"],
         rep["walkforward"]["holdout_range"]["start"], rep["walkforward"]["holdout_range"]["end"]))
    a("")
    a("%s" % rep["walkforward"]["note"])
    a("")
    a("| # | 규칙 | 프로파일 | 선택n | 선택EV% | 선택총손익 | 홀드아웃n | 홀드아웃EV% | "
      "홀드아웃총손익 | 홀드아웃MaxDD% | ①②③④ |")
    a("|---|---|---|---|---|---|---|---|---|---|---|")
    for p in rep["walkforward"]["picks"]:
        sel, hold = p["select"]["worst"], p["holdout"]["worst"]
        cond = "".join("✓" if p["conditions"][k] else "✗" for k in
                       ("c1_holdout_ev_gt_010", "c2_holdout_pnl_gt_0",
                        "c3_holdout_n_ge_100", "c4_holdout_maxdd_le_15"))
        a("| %d | %s | %s | %d | %s | %s | %d | %s | %s | %s | %s |"
          % (p["pick_rank"], p["rule"], p["hold"], sel["trades"],
             _f(sel["expectancy_pct"]), _w(sel["total_pnl_krw"]),
             hold["trades"], _f(hold["expectancy_pct"]), _w(hold["total_pnl_krw"]),
             _f(hold["maxdd_pct"], ".2f"), cond))
    a("")
    a("## 5. 추천")
    a("")
    rec = rep["recommendation"]
    if rec["found"]:
        a("- **추천: 규칙 %s · 프로파일 %s (보수 worst 기준)**" % (rec["rule"], rec["hold"]))
        a("- 근거: %s" % rec["why"])
    else:
        a("- **추천 없음**: %s" % rec["why"])
    a("")
    a("## 6. 한계")
    a("")
    for li in rec["limits"]:
        a("- %s" % li)
    a("- 부분익절의 '절반'은 수량 floor(qty/2) — 홀수 수량이면 나머지가 잔량, 1주면 부분익절 없음.")
    a("- 트레일 스탑은 초기 손절가 아래로 내려가지 않는다(max(초기손절, 고점−1×ATR14)) — "
      "'손절 −1%' 계약 유지. ATR14 = TR 14일 단순평균(edge_stack 정본과 동일 정의).")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="자본 제약 시뮬레이터 (docs/spec_capital_sim.md)")
    ap.add_argument("--start", default=DEFAULT_START)
    ap.add_argument("--end", default=DEFAULT_END)
    ap.add_argument("--equity", type=float, default=245435.0)
    ap.add_argument("--max-pos", type=int, default=3)
    ap.add_argument("--per-stock-pct", type=float, default=0.3334)
    ap.add_argument("--daily-max", type=int, default=3)
    ap.add_argument("--price-min", type=float, default=1000.0)
    ap.add_argument("--price-max", type=float, default=50000.0)
    ap.add_argument("--slip-pct", type=float, default=0.0)
    ap.add_argument("--same-day-close", dest="same_day_close", action="store_true",
                    default=True, help="단타 프로파일(진입일 종가 강제청산) — 기본 on")
    ap.add_argument("--no-same-day-close", dest="same_day_close", action="store_false")
    ap.add_argument("--hold-sessions", type=int, default=5,
                    help="스윙 프로파일 최대 보유 세션(0=스윙 끔)")
    ap.add_argument("--json-out", default=str(REPO / "data/reports/capital_sim.json"))
    ap.add_argument("--md-out", default=str(REPO / "data/reports/capital_sim.md"))
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    if args.start >= args.end:
        ap.error("--start 는 --end 보다 앞이어야 합니다")
    profiles = (["same_day"] if args.same_day_close else []) \
        + (["swing%d" % args.hold_sessions] if args.hold_sessions > 0 else [])
    if not profiles:
        ap.error("프로파일이 모두 꺼져 있습니다(--same-day-close 와 --hold-sessions 확인)")

    warm = (date.fromisoformat(args.start) - timedelta(days=WARMUP_DAYS)).isoformat()
    load_end = (date.fromisoformat(args.end) + timedelta(days=BUFFER_DAYS)).isoformat()
    df = esb.load(warm, load_end)                      # v1 과 동일 SELECT 필터
    df = esb.compute_features(df)                      # chg·vol_ratio·nxt_*·atr14
    raw_n, signals, bars, dates = build_signals(df, args.start, args.end)
    params = {"equity": args.equity, "max_pos": args.max_pos,
              "per_stock_pct": args.per_stock_pct, "daily_max": args.daily_max,
              "price_min": args.price_min, "price_max": args.price_max,
              "slip_pct": args.slip_pct, "fee_pct": FEE_PCT,
              "hold_sessions": args.hold_sessions, "profiles": profiles}
    cfg = {"json_out": args.json_out, "md_out": args.md_out}
    rep = build_report(signals, bars, dates, params, args.start, args.end, cfg,
                       n_loaded=len(df), raw_n=raw_n)

    jtxt = json.dumps(rep, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    mdtxt = build_md(rep)
    Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.md_out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.json_out, "w", encoding="utf-8") as f:
        f.write(jtxt)
    with open(args.md_out, "w", encoding="utf-8") as f:
        f.write(mdtxt)

    if not args.quiet:
        print("[capital_sim] %s~%s 신호 %d → 시뮬레이션 %d · 유니버스 %d종목"
              % (args.start, args.end, rep["signals"]["raw"],
                 rep["signals"]["simulatable"], df["stock_code"].nunique()))
        print("[엣지 점수] " + " ".join("%s:%d" % (k, rep["signals"]["edge_score_dist"][k])
                                        for k in ["%d" % i for i in range(8)] + ["none"]))
        for r in rep["results"]:
            w = r["worst"]
            print("[결과] %s/%s worst: n=%d EV %+.2f%%(%s원) PnL %s MaxDD %s%% skip(cap=%d "
                  "band=%d) ambiguous %s%%" % (
                      r["rule"], r["hold"], w["trades"],
                      w["expectancy_pct"] if w["expectancy_pct"] is not None else float("nan"),
                      w["expectancy_krw"] if w["expectancy_krw"] is not None else float("nan"),
                      _w(w["total_pnl_krw"]),
                      ("%.2f" % w["maxdd_pct"]) if w["maxdd_pct"] is not None else "—",
                      w["skipped_capital"], w["skipped_price_band"],
                      ("%.1f" % w["ambiguous_pct"]) if w["ambiguous_pct"] is not None else "—"))
        for p in rep["walkforward"]["picks"]:
            sel, hold = p["select"]["worst"], p["holdout"]["worst"]
            print("[WF pick %d] %s/%s — 선택 EV %+.2f%%(n=%d) 홀드아웃 EV %+.2f%%(n=%d, "
                  "MaxDD %s%%)" % (p["pick_rank"], p["rule"], p["hold"],
                                   sel["expectancy_pct"] or 0.0, sel["trades"],
                                   hold["expectancy_pct"] or 0.0, hold["trades"],
                                   ("%.2f" % hold["maxdd_pct"])
                                   if hold["maxdd_pct"] is not None else "—"))
        rec = rep["recommendation"]
        print("[추천] " + (("%s/%s — %s" % (rec["rule"], rec["hold"], rec["why"]))
                           if rec["found"] else "없음 — %s" % rec["why"]))
        cl = rep["claim"]
        print("[저장] %s (%dB) / %s (%dB)" % (args.json_out, len(jtxt.encode()),
                                              args.md_out, len(mdtxt.encode())))
        print("[자기신고] source=%s parsed=%s persisted=%s" % (
            cl["source_rows"], cl["parsed_rows"], cl["persisted_rows"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

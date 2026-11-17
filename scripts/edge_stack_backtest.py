#!/usr/bin/env python3
"""엣지 스택 백테스트 — 확률 누적(edge_score 0..7) × 비대칭 손익비 그리드 실측.

계약 문서: docs/spec_edge_stack_backtest.md (2026-11-17). **측정 단계만** — 실주문 경로
변경 없음, DB 는 SELECT 전용(주문·브리지·트레이더 파일 무접근 — 스펙 금지 조항).

WHY (스펙 인용 실측 기준선 — 재측정 금지, 판정 근거로 인용만):
  - 실계좌 11건: 승률 22.2% · 평균이익 +1,028원 / 평균손실 −5,075원 · 기대값 −2,943원/건 ·
    profit factor 0.058. 손익비 = 1,028/5,075 = 0.203 → **역방향**(작게 벌고 크게 잃음).
  - 같은 신호 11,300건(OHLC 그리드, scripts/_stoploss_grid.py 와 동일 신호 정의):
    무손절 기준선 −1.328%·승률 36.0% / 7%·7% 중립 −0.822% → **청산 폭만으로는 +가 안 됨.
    병목은 진입 품질**.
  - 왕복 비용 0.21%(실체결 검증). 손익분기 승률(폐쇄형, 아래 유도): ±1% 60.5% · ±1.5% 57.0%.

실측 조회 명령(2026-11-17 실행, 결과는 주석에 고정 — 추측 금지):
  -- SELECT MIN(trade_date), MAX(trade_date), COUNT(*), COUNT(DISTINCT stock_code)
  -- FROM market_data;
  -- → 2023-01-02 ~ 2026-11-16 · 3,223,794행 · 3,980종목
  -- SELECT COUNT(*) FROM market_data WHERE trade_date BETWEEN '2026-04-01' AND '2026-11-14'
  --   AND close_price >= 1000 AND open_price > 0;
  -- → 548,792행

멱등: 출력(JSON/MD)에 실행 시각을 넣지 않는다. 같은 입력이면 같은 바이트(tests ⑥).

일봉 한계(기존 그리드와 같은 규율): 장중 **순서**가 없어서 매 거래를 두 극단으로 계산한다.
  · worst: 저가 먼저 닿음 → 손절 우선
  · best : 고가 먼저 닿음 → 익절 우선
  중립 = (worst+best)/2 로 보고한다. 두 극단의 차이가 곧 순서 불확실성이다.
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
FEE_PCT = 0.21                 # 왕복 비용 %(수수료+거래세, 실체결 검증 — 스펙 기준선)
WARMUP_DAYS = 120              # 피처 워밍업(MA20 5일 기울기 25행 + 공휴일 여유)
DEFAULT_START, DEFAULT_END = "2026-04-01", "2026-11-14"
SELECT_END, HOLDOUT_START = "2026-08-15", "2026-08-16"   # 워크포워드 분할(스펙 고정)
THRESHOLDS = [0, 3, 4, 5, 6, 7]                          # edge_score 문턱(스펙 고정)
FIXED_GRID = [(1.5, 4.5), (1.5, 6.0), (2.0, 6.0), (1.0, 3.0), (1.5, 2.5), (7.0, 7.0)]
# (1.5,2.5)=기존 지시서 대조군 · (7,7)=현행 대조군 — 스펙 고정. 사후 조정 금지.
ATR_GRID = [(1.0, 3.0), (1.5, 3.0)]                      # stop×ATR14, tp×ATR14
ATR_STOP_CLAMP = (0.5, 8.0)                              # stop 클램프(스펙: 0.5% ~ 8%)
MIN_SAMPLE = 100        # 이 미만이면 '표본 부족' — 판정 근거로 쓰지 않음(스펙)
REC_MIN_SAMPLE = 300    # 추천 조건 ②
REC_MIN_EV = 0.10       # 추천 조건 ① (중립 기대값 %/건)


# --------------------------------------------------------------------------- 조회
def load(start: str, end: str) -> pd.DataFrame:
    """market_data SELECT 전용 조회(쓰기 없음) + 종목별 t 시점 피처 계산.

    `start` 보다 WARMUP_DAYS 앞부터 적재해 4월 초 신호도 MA20/ATR 이 산출되게 한다
    (기존 그리드가 창 밖 이력 없이 rolling 하느라 초반 신호를 vol_ratio NaN 으로
    탈락시키는 것과의 차이 — 신호 **정의**는 동일하고 유효 신호만 늘어난다).
    """
    con = psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT", "5434")),
        user=os.environ["POSTGRES_USER"], password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"), connect_timeout=15)
    q = """
        SELECT stock_code, trade_date, open_price, high_price, low_price, close_price,
               volume, trading_value
        FROM market_data
        WHERE trade_date BETWEEN %s AND %s AND close_price >= 1000 AND open_price > 0
    """
    df = pd.read_sql(q, con, params=(start, end))
    con.close()
    for c in ["open_price", "high_price", "low_price", "close_price", "volume",
              "trading_value"]:
        df[c] = df[c].astype(float)
    # psycopg2 는 PostgreSQL date 를 datetime.date 로 돌려줌(object dtype) → 비교·정렬 위해 datetime64 로
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df.sort_values(["stock_code", "trade_date"]).reset_index(drop=True)


# ------------------------------------------------------------------ 피처(전부 t 이하)
def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """종목별 t 시점 피처 + t+1 진입 데이터(미래 정보 — 시뮬레이션 전용, 피처엔 미사용)."""
    df = df.copy()
    g = df.groupby("stock_code", sort=True)
    df["chg"] = g["close_price"].pct_change() * 100
    df["vol20"] = g["volume"].transform(lambda s: s.rolling(20).mean())
    df["vol_ratio"] = df["volume"] / df["vol20"]
    df["ma5"] = g["close_price"].transform(lambda s: s.rolling(5).mean())
    df["ma20"] = g["close_price"].transform(lambda s: s.rolling(20).mean())
    df["ma20_prev5"] = g["ma20"].shift(5)
    df["hhv20"] = g["high_price"].transform(lambda s: s.rolling(20).max())
    df["hhv20_prev"] = g["hhv20"].shift(1)
    df["body"] = (df["close_price"] - df["open_price"]).abs()
    df["rng"] = df["high_price"] - df["low_price"]
    df["close_pos"] = (df["close_price"] - df["low_price"]) / df["rng"]
    prev_close = g["close_price"].shift(1)
    df["tr"] = pd.concat([df["rng"], (df["high_price"] - prev_close).abs(),
                          (df["low_price"] - prev_close).abs()], axis=1).max(axis=1)
    df["atr14"] = g["tr"].transform(lambda s: s.rolling(14).mean())  # TR 14일 단순평균
    df["atr_pct"] = df["atr14"] / df["close_price"] * 100
    df["chg5"] = (df["close_price"] / g["close_price"].shift(5) - 1) * 100  # 5일 누적 %
    df["nxt_open"] = g["open_price"].shift(-1)
    df["nxt_high"] = g["high_price"].shift(-1)
    df["nxt_low"] = g["low_price"].shift(-1)
    df["nxt_close"] = g["close_price"].shift(-1)
    df["nxt_date"] = g["trade_date"].shift(-1)
    return df


def edge_factors(df: pd.DataFrame) -> pd.DataFrame:
    """엣지 스택 7요인(스펙 표 그대로, 전부 t 이하 데이터) + edge_score = 합계(0..7).

    f1 추세 방향     : close > MA20 AND MA5 > MA20
    f2 추세 기울기   : MA20_t > MA20_{t-5}
    f3 지지저항 돌파 : close >= 0.98×HHV20(20일 고점 2% 이내) OR close > HHV20_{t-1}
    f4 캔들 품질     : body/range >= 0.5 AND close 가 당일 범위 상위 1/3
    f5 거래량 확인   : vol_ratio >= 2.0
    f6 변동성 적합   : 1.0% <= ATR14/close <= 6.0%
    f7 과열 회피     : chg <= 12% AND 5일 누적 <= 25%
    """
    df = df.copy()
    df["f1"] = (df["close_price"] > df["ma20"]) & (df["ma5"] > df["ma20"])
    df["f2"] = df["ma20"] > df["ma20_prev5"]
    df["f3"] = (df["close_price"] >= 0.98 * df["hhv20"]) | (df["close_price"] > df["hhv20_prev"])
    df["f4"] = ((df["body"] / df["rng"]) >= 0.5) & (df["close_pos"] * 3 >= 2 - 1e-9)
    df["f5"] = df["vol_ratio"] >= 2.0
    df["f6"] = (df["atr_pct"] >= 1.0) & (df["atr_pct"] <= 6.0)
    df["f7"] = (df["chg"] <= 12.0) & (df["chg5"] <= 25.0)
    fc = ["f%d" % i for i in range(1, 8)]
    df["score"] = df[fc].sum(axis=1, min_count=7)  # 어느 요인이라도 NaN → score NaN
    return df


def signal_frame(df: pd.DataFrame, start: str, end: str):
    """신호 정의(기존 그리드와 동일): t 일 chg>=3% · vol_ratio>=1.5 · 거래대금>=5억.
    반환: (raw, simulatable). raw = 정의 일치 전건, simulatable = t+1 데이터·점수 유효분.
    """
    sig = df[(df["trade_date"] >= start) & (df["trade_date"] <= end)]
    raw = sig[(sig["chg"] >= 3.0) & (sig["vol_ratio"] >= 1.5)
              & (sig["trading_value"] >= 5e8)].copy()
    ok = raw[(raw["nxt_open"] > 0) & (raw["nxt_high"] > 0) & (raw["nxt_low"] > 0)
             & (raw["nxt_close"] > 0) & raw["score"].notna()].copy()
    # MaxDD 는 진입일(nxt_date) 순서로 계산 — 동일일 내 순서는 종목코드로 고정(결정적)
    ok = ok.sort_values(["nxt_date", "stock_code"]).reset_index(drop=True)
    return raw, ok


# ---------------------------------------------------------------------- 시뮬레이션
def breakeven_win_pct(stop_pct, tp_pct, fee_pct: float = FEE_PCT):
    """손익분기 승률 p* = (loss + c) / (loss + win) × 100.

    유도(비용 c 는 왕복 %; 승이면 +tp−c, 패면 −stop−c):
      p*(tp − c) − (1−p)*(stop + c) = 0
      → p*(tp + stop) = stop + c  →  p* = (stop + c)/(stop + tp)
    대칭(stop=tp=s)이면 p* = (s+c)/(2s) — 스펙 기준선 ±1% 60.5%·±1.5% 57.0% 와 일치 확인.
    stop/tp 가 배열(ATR 셀)이면 트레이드별 p* 배열을 반환한다.
    """
    s = np.atleast_1d(np.asarray(stop_pct, dtype=float))
    t = np.atleast_1d(np.asarray(tp_pct, dtype=float))
    return (s + fee_pct) / (s + t) * 100.0


def _r4(x):
    """JSON 직렬화 가능 + 결정적(고정 소수 4자리) 변환. NaN/None → None."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(v):
        return None
    return round(v, 4)


def _mid(a, b):
    if a is None or b is None:
        return None
    return round((a + b) / 2.0, 4)


def _stats(net, hit_tp, hit_sl) -> dict:
    """단일 모드(worst/best) 성과 통계. net 은 비용 차감 후 수익률(%) 단위."""
    out = {"n": int(len(net)), "win_pct": None, "avg_pct": None, "sum_pp": None,
           "avg_win_pct": None, "avg_loss_pct": None, "r": None, "profit_factor": None,
           "maxdd_pct": None, "tp_rate": None, "sl_rate": None, "ambiguous": None}
    n = out["n"]
    if n == 0:
        return out
    wins = net[net > 0]
    losses = net[net < 0]
    out["win_pct"] = _r4((net > 0).mean() * 100)
    out["avg_pct"] = _r4(net.mean() * 100)          # = 기대값 %/건
    out["sum_pp"] = _r4(net.sum() * 100)            # = 합계 %p
    if len(wins):
        out["avg_win_pct"] = _r4(wins.mean() * 100)
    if len(losses):
        out["avg_loss_pct"] = _r4((-losses).mean() * 100)
    if len(wins) and len(losses):
        out["r"] = _r4(wins.mean() / (-losses).mean())      # 손익비 R = 평균이익/평균손실
    wsum, lsum = float(wins.sum()), float(-losses.sum())
    if lsum > 0:
        out["profit_factor"] = _r4(wsum / lsum)
    elif wsum > 0:
        out["profit_factor"] = None                 # 손실 0건 → 측정 불가
    else:
        out["profit_factor"] = 0.0                  # 승 0건
    # MaxDD: 복리 자본곡선(∏(1+r)) 기준 — net 은 이미 분수(예: −0.0121)이고, 누적합(%p)은
    # 거래 수에 비례해 무한정 커지므로 표준 관례대로 고점 대비 하락률 % 로 보고한다
    # (거래 순서는 진입일 정렬).
    eq = np.cumprod(1.0 + net)
    out["maxdd_pct"] = _r4(-(eq / np.maximum.accumulate(eq) - 1.0).min() * 100)
    out["tp_rate"] = _r4(hit_tp.mean() * 100)
    out["sl_rate"] = _r4(hit_sl.mean() * 100)
    out["ambiguous"] = _r4((hit_tp & hit_sl).mean() * 100)
    return out


def simulate(entry, high, low, close, stop_pct, tp_pct, fee_pct: float = FEE_PCT) -> dict:
    """stop/tp 그리드 시뮬레이션(scripts/_stoploss_grid.py 와 동일 규율 — 참고만, 미수정).

    일봉엔 장중 순서가 없다 → 두 극단:
      worst: 저가 먼저 닿았다고 가정(손절 우선)
      best : 고가 먼저 닿았다고 가정(익절 우선)
    미터치 시 당일 종가 청산. stop_pct/tp_pct 는 스칼라(% )또는 트레이드별 배열(ATR 셀).
    """
    entry = np.atleast_1d(np.asarray(entry, dtype=float))
    high = np.atleast_1d(np.asarray(high, dtype=float))
    low = np.atleast_1d(np.asarray(low, dtype=float))
    close = np.atleast_1d(np.asarray(close, dtype=float))
    stop_pct = np.atleast_1d(np.asarray(stop_pct, dtype=float))
    tp_pct = np.atleast_1d(np.asarray(tp_pct, dtype=float))
    stp = entry * (1.0 - stop_pct / 100.0)
    tgt = entry * (1.0 + tp_pct / 100.0)
    hit_sl = low <= stp
    hit_tp = high >= tgt
    out = {}
    for name, sl_first in (("worst", True), ("best", False)):
        if sl_first:
            ret = np.where(hit_sl, -stop_pct / 100.0,
                           np.where(hit_tp, tp_pct / 100.0, close / entry - 1.0))
        else:
            ret = np.where(hit_tp, tp_pct / 100.0,
                           np.where(hit_sl, -stop_pct / 100.0, close / entry - 1.0))
        out[name] = _stats(ret - fee_pct / 100.0, hit_tp, hit_sl)
    return out


def _neutral(worst: dict, best: dict) -> dict:
    return {k: (_mid(worst[k], best[k]) if k != "n" else worst["n"]) for k in worst}


def _cell(sig: pd.DataFrame, threshold: int, stop_label: str, tp_label: str,
          stop_pct, tp_pct, rr: float) -> dict:
    e = sig["nxt_open"].to_numpy(dtype=float)
    hi = sig["nxt_high"].to_numpy(dtype=float)
    lo = sig["nxt_low"].to_numpy(dtype=float)
    cl = sig["nxt_close"].to_numpy(dtype=float)
    res = simulate(e, hi, lo, cl, stop_pct, tp_pct)
    worst, best = res["worst"], res["best"]
    neutral = _neutral(worst, best)
    pstar = breakeven_win_pct(stop_pct, tp_pct)
    cell = {
        "stop": stop_label, "tp": tp_label, "rr": _r4(rr), "threshold": threshold,
        "worst": worst, "best": best, "neutral": neutral,
        "breakeven_win_pct": _r4(pstar.mean()) if pstar.size else None,
        "n": worst["n"], "verdict": "",
    }
    if np.size(pstar) > 1:  # ATR 셀: 트레이드별 p* 범위 기록
        cell["breakeven_win_pct_min"] = _r4(pstar.min())
        cell["breakeven_win_pct_max"] = _r4(pstar.max())
    # 경험적 손익분기(측정 avg_loss/(avg_loss+avg_win)) — 스펙 기준선의 비대칭 인용값
    # (R:R 2 50.2% · R:R 3 43.8% · R:R 4 40.0%)은 폐쇄형과 불일치(출처 미확인)라 참고용으로만.
    if neutral["avg_loss_pct"] and neutral["avg_win_pct"]:
        cell["breakeven_win_pct_empirical"] = _r4(
            neutral["avg_loss_pct"] / (neutral["avg_loss_pct"] + neutral["avg_win_pct"]) * 100)
    else:
        cell["breakeven_win_pct_empirical"] = None
    return cell


def run_cells(sig: pd.DataFrame) -> list:
    """문턱 × 청산 조합 전체 그리드(스펙 표 그대로 — 사후 조정 금지)."""
    cells = []
    for th in THRESHOLDS:
        sub = sig[sig["score"] >= th]
        for stop, tp in FIXED_GRID:
            cells.append(_cell(sub, th, "%g%%" % stop, "%g%%" % tp, stop, tp, tp / stop))
        for sm, tm in ATR_GRID:
            atr = sub["atr_pct"].to_numpy(dtype=float)
            stop_arr = np.clip(atr * sm, ATR_STOP_CLAMP[0], ATR_STOP_CLAMP[1])
            cells.append(_cell(sub, th, "%g×ATR" % sm, "%g×ATR" % tm, stop_arr,
                               atr * tm, tm / sm))
    return cells


# ------------------------------------------------------------- 워크포워드·추천·판정
def _key(cell: dict):
    return (cell["threshold"], cell["stop"], cell["tp"])


def conditions(sel_cell: dict, hold_cell: dict) -> tuple:
    """추천 조건 4개(스펙): ①1구간 중립 EV>+0.10% ②1구간 표본≥300
    ③2구간(홀드아웃, 재조정 없음) 중립 EV>0 ④1구간 승률 ≥ 손익분기 승률."""
    sel_n, hold_n = sel_cell["neutral"], hold_cell["neutral"]
    c1 = sel_n["avg_pct"] is not None and sel_n["avg_pct"] > REC_MIN_EV
    c2 = sel_cell["n"] >= REC_MIN_SAMPLE
    c3 = hold_n["avg_pct"] is not None and hold_n["avg_pct"] > 0
    c4 = (sel_n["win_pct"] is not None and sel_cell["breakeven_win_pct"] is not None
          and sel_n["win_pct"] >= sel_cell["breakeven_win_pct"])
    return c1, c2, c3, c4


def _f(v, spec="+.2f"):
    return ("%" + spec) % v if v is not None else "—"


def compose_verdict(n, c1, c2, c3, c4, pick_rank=None, recommended=False,
                    sel_ev=None, sel_n=None, hold_ev=None, sel_win=None, pstar=None):
    """문턱별 판정 문자열. n < MIN_SAMPLE 이면 '표본 부족'(판정 근거 제외)."""
    if n < MIN_SAMPLE:
        return "표본 부족(n=%d<%d) — 판정 근거로 쓰지 않음" % (n, MIN_SAMPLE)
    mk = lambda c: "✓" if c else "✗"  # noqa: E731
    parts = [
        "①1구간EV %s%%/건%s" % (_f(sel_ev), mk(c1)),
        "②표본 %s%s" % (_f(sel_n, "d"), mk(c2)),
        "③2구간EV %s%%%s" % (_f(hold_ev), mk(c3)),
        "④승률 %s%%≥p* %s%%%s" % (_f(sel_win, ".1f"), _f(pstar, ".1f"), mk(c4)),
    ]
    if recommended:
        parts.append("→ 추천")
    elif pick_rank:
        parts.append("→ WF pick %d" % pick_rank)
    else:
        parts.append("→ 추천조건 미충족")
    return " | ".join(parts)


def walkforward(sig: pd.DataFrame, start: str, end: str) -> dict:
    """1구간(선택)에서 상위 3개 조합을 정하고 2구간(홀드아웃)에 재조정 없이 적용."""
    sig_sel = sig[(sig["trade_date"] <= SELECT_END)].reset_index(drop=True)
    sig_hold = sig[(sig["trade_date"] >= HOLDOUT_START)].reset_index(drop=True)
    sel_cells = run_cells(sig_sel)
    hold_cells = run_cells(sig_hold)
    hold_by_key = {_key(c): c for c in hold_cells}
    ranked = sorted(sel_cells, key=lambda c: (c["neutral"]["avg_pct"] is None,
                                              -(c["neutral"]["avg_pct"] or -np.inf)))
    picks = []
    for rank, c in enumerate(ranked[:3], 1):
        h = hold_by_key[_key(c)]
        c1, c2, c3, c4 = conditions(c, h)
        picks.append({
            "pick_rank": rank, "stop": c["stop"], "tp": c["tp"], "rr": c["rr"],
            "threshold": c["threshold"],
            "select": {"n": c["n"], "worst": c["worst"], "best": c["best"],
                       "neutral": c["neutral"],
                       "breakeven_win_pct": c["breakeven_win_pct"]},
            "holdout": {"n": h["n"], "worst": h["worst"], "best": h["best"],
                        "neutral": h["neutral"]},
            "conditions": {"c1_ev_gt_010": bool(c1), "c2_n_ge_300": bool(c2),
                           "c3_holdout_ev_gt_0": bool(c3),
                           "c4_win_ge_breakeven": bool(c4)},
        })
    return {"select_range": {"start": start, "end": SELECT_END},
            "holdout_range": {"start": HOLDOUT_START, "end": end},
            "picks": picks, "_sel_cells": sel_cells, "_hold_by_key": hold_by_key}


def recommend(wf: dict) -> dict:
    """추천 1개(스펙: 4조건 모두 만족하는 조합만). 없으면 없다고 정직하게 쓴다."""
    sel_cells, hold_by_key = wf["_sel_cells"], wf["_hold_by_key"]
    limits = [
        "일봉 순서 불확실성 — worst/best 밴드의 중립값이며 실제 경로는 밴드 안 어딘가",
        "왕복 비용 0.21%(실체결) 가정 — 슬리피지·호가 갭 미반영",
        "홀드아웃 1회(2026-08-16~11-14) — 우연성 배제 못함",
        "MaxDD 는 복리 자본곡선(∏(1+r)) 기준·진입일 순서 — 동일일 내 순서는 종목코드 정렬(임의)",
        "p* 는 스펙 계약식 (stop+0.21)/(stop+tp) — ATR 셀은 트레이드별 p* 평균",
    ]
    passed = []
    for c in sel_cells:
        c1, c2, c3, c4 = conditions(c, hold_by_key[_key(c)])
        if c1 and c2 and c3 and c4:
            passed.append((c, c1, c2, c3, c4))
    if not passed:
        best = max(sel_cells, key=lambda c: c["neutral"]["avg_pct"] or -np.inf)
        bc = conditions(best, hold_by_key[_key(best)])
        return {"found": False, "stop": None, "tp": None, "threshold": None,
                "why": ("추천 조건(①②③④ 모두)을 만족하는 조합 없음. "
                        "1구간 최고 EV 조합(%s/%s 문턱%d, 중립 %+.2f%%/건)은 조건 %s"
                        % (best["stop"], best["tp"], best["threshold"],
                           best["neutral"]["avg_pct"] or 0.0,
                           "/".join(str(i + 1) for i, v in enumerate(bc) if not v))),
                "limits": limits}
    c, c1, c2, c3, c4 = max(passed, key=lambda p: (p[0]["neutral"]["avg_pct"] or -np.inf,
                                                   p[0]["n"]))
    h = hold_by_key[_key(c)]
    why = ("1구간(선택) 중립 기대값 %+.2f%%/건(>+0.10) · 표본 %d(≥300) · "
           "2구간(홀드아웃) 중립 기대값 %+.2f%%/건(>0) · 1구간 승률 %.1f%% ≥ 손익분기 p* %.1f%%"
           % (c["neutral"]["avg_pct"], c["n"], h["neutral"]["avg_pct"],
              c["neutral"]["win_pct"], c["breakeven_win_pct"]))
    return {"found": True, "stop": c["stop"], "tp": c["tp"], "rr": c["rr"],
            "threshold": c["threshold"], "why": why, "limits": limits,
            "select": {"n": c["n"], "neutral": c["neutral"]},
            "holdout": {"n": h["n"], "neutral": h["neutral"]}}


# ------------------------------------------------------------------------ 보고서
def build_report(sig: pd.DataFrame, start: str, end: str, cfg: dict,
                 n_loaded: int, raw_n: int) -> dict:
    """전체 보고 dict(결정적 — 실행 시각 없음).

    sig = 시뮬레이션 가능 신호(정의 일치 + t+1 데이터·점수 유효). raw_n = 정의 일치 전건
    (t+1 데이터 무관). n_loaded = 조회 원본 행수(자기신고용).
    """
    cells = run_cells(sig)
    wf = walkforward(sig, start, end)
    rec = recommend(wf)
    pick_rank_by_key = {_key(p): p["pick_rank"] for p in wf["picks"]}
    rec_key = (_key(rec), ) if rec["found"] else ()
    sel_by_key = {_key(c): c for c in wf["_sel_cells"]}
    hold_by_key = wf["_hold_by_key"]
    for c in cells:
        k = _key(c)
        s, h = sel_by_key[k], hold_by_key[k]
        c1, c2, c3, c4 = conditions(s, h)
        c["verdict"] = compose_verdict(
            c["n"], c1, c2, c3, c4,
            pick_rank=pick_rank_by_key.get(k), recommended=(k in rec_key),
            sel_ev=s["neutral"]["avg_pct"], sel_n=s["n"], hold_ev=h["neutral"]["avg_pct"],
            sel_win=s["neutral"]["win_pct"], pstar=s["breakeven_win_pct"])
    dist = {str(i): int((sig["score"] == i).sum()) for i in range(8)}
    dist["total"] = int(len(sig))
    dist["thresholds"] = {str(t): int((sig["score"] >= t).sum()) for t in THRESHOLDS}
    baseline = {}
    for th in THRESHOLDS:
        sub = sig[sig["score"] >= th]
        net = (sub["nxt_close"] / sub["nxt_open"] - 1 - FEE_PCT / 100).to_numpy() * 100
        baseline[str(th)] = {"n": int(len(net)), "avg_pct": _r4(net.mean()) if len(net) else None,
                             "win_pct": _r4((net > 0).mean() * 100) if len(net) else None,
                             "sum_pp": _r4(net.sum()) if len(net) else None}
    report = {
        "data_range": {"start": start, "end": end, "source":
                       "market_data — close>=1000 & open>0 · 신호 chg>=3% & vol_ratio>=1.5"
                       " & 거래대금>=5억 · 진입 t+1 시가 · 왕복비용 0.21%"},
        "signals": {"raw": int(raw_n), "after_join": int(len(sig))},
        "edge_score_dist": dist,
        "baseline_no_exit": baseline,
        "grid": cells,
        "walkforward": {"select_range": wf["select_range"],
                        "holdout_range": wf["holdout_range"], "picks": wf["picks"]},
        "recommendation": rec,
    }
    report["claim"] = {
        "runner": "edge_stack_backtest",
        "source_rows": {"market_data_fetched": int(n_loaded),
                        "signals_matched": report["signals"]["raw"]},
        "parsed_rows": {"signals_simulatable": report["signals"]["after_join"],
                        "grid_cells": len(cells), "walkforward_picks": len(wf["picks"])},
        "persisted_rows": {"grid_records": len(cells), "json_file": 1, "md_file": 1,
                           "json_out": cfg["json_out"], "md_out": cfg["md_out"]},
        "limits": rec["limits"],
        "note": "DB 쓰기 금지(읽기 전용 계약, 스펙 'DB 는 SELECT 만')라 dq_claim.record_claim 은 "
                "미사용 — 출력 JSON claim 절에 소스 수신/파서 생성/실제 저장 3분리 자기신고를 "
                "내장(scripts/money_scoreboard.py 와 동일 관례). 실행 시각 미기록 → 같은 입력이면 "
                "같은 바이트(멱등). 재실행 시 파일 덮어쓰기.",
    }
    return report


def build_md(rep: dict) -> str:
    """MD 보고서 — 기준선 → 점수 분포 → 상위 조합 → 추천 → 한계(스펙 산출물 3)."""
    L = []  # noqa: E741
    a = L.append
    dr, sig, dist = rep["data_range"], rep["signals"], rep["edge_score_dist"]
    a("# 엣지 스택 백테스트 — 확률 누적(edge_score) × 비대칭 손익비")
    a("")
    a("- 데이터: `market_data` %s ~ %s · 신호 %d건(시뮬레이션 %d건) · 왕복 비용 0.21%%(실체결 검증)"
      % (dr["start"], dr["end"], sig["raw"], sig["after_join"]))
    a("- **일봉 한계(첫 단락 명시)**: 장중 순서가 없다 → 매 거래를 두 극단으로 계산. "
      "worst=손절 우선(저가 먼저) · best=익절 우선(고가 먼저). 중립=(worst+best)/2 이며 "
      "두 극단의 차이가 곧 순서 불확실성이다(기존 그리드와 같은 규율).")
    a("")
    a("## 1. 기준선")
    a("")
    a("- 실계좌 11건(스펙 인용, 재측정 금지): 승률 22.2% · 평균이익 +1,028원/평균손실 −5,075원 · "
      "기대값 −2,943원/건 · PF 0.058 · 손익비 0.203(역방향).")
    b0 = rep["baseline_no_exit"]["0"]
    g77 = next(c for c in rep["grid"] if c["threshold"] == 0 and c["stop"] == "7%")
    a("- 무손절(시가→종가, 문턱 0): 평균 %+.3f%% · 승률 %.1f%% (스펙 기준선 −1.328%%·36.0%%)"
      % (b0["avg_pct"], b0["win_pct"]))
    a("- 현행 대조군 7%%/7%% 문턱 0: 중립 %+.3f%% (스펙 기준선 −0.822%%) · 승률 %.1f%%"
      % (g77["neutral"]["avg_pct"], g77["neutral"]["win_pct"]))
    a("")
    a("## 2. 엣지 점수 분포 (0..7)")
    a("")
    a("| 점수 | 표본 | 비율 | | 문턱 | 표본 |")
    a("|---|---|---|---|---|---|")
    for i in range(8):
        a("| %d | %d | %.1f%% | | %s | %s |" % (
            i, dist[str(i)], dist[str(i)] / dist["total"] * 100,
            ("≥%d" % i) if i else "0",
            dist["thresholds"][str(i)] if i in THRESHOLDS else "—"))
    a("")
    for t in THRESHOLDS:
        n = dist["thresholds"][str(t)]
        if n < MIN_SAMPLE:
            a("- 문턱 %d: 표본 %d — **표본 부족(n<%d)** 판정 근거 제외." % (t, n, MIN_SAMPLE))
    a("")
    a("## 3. 1구간 상위 조합 (선택 2026-04-01~08-15 → 홀드아웃 08-16~11-14, 재조정 없음)")
    a("")
    a("| # | 손절/익절 | R:R | 문턱 | 선택n | 선택EV% | 승률% | R | MaxDD% | 홀드아웃EV% | ①②③④ |")
    a("|---|---|---|---|---|---|---|---|---|---|---|")
    for p in rep["walkforward"]["picks"]:
        sel, hold = p["select"], p["holdout"]
        cond = "".join("✓" if p["conditions"][k] else "✗"
                       for k in ("c1_ev_gt_010", "c2_n_ge_300",
                                 "c3_holdout_ev_gt_0", "c4_win_ge_breakeven"))
        a("| %d | %s/%s | %.2f | %d | %d | %+.2f | %.1f | %s | %.2f | %+.2f | %s |"
          % (p["pick_rank"], p["stop"], p["tp"], p["rr"], p["threshold"], sel["n"],
             sel["neutral"]["avg_pct"], sel["neutral"]["win_pct"],
             _f(sel["neutral"]["r"]), sel["neutral"]["maxdd_pct"],
             hold["neutral"]["avg_pct"], cond))
    a("")
    a("## 4. 추천")
    a("")
    rec = rep["recommendation"]
    if rec["found"]:
        a("- **추천: 손절 %s / 익절 %s (R:R %.2f) · edge_score 문턱 ≥%d**" % (
            rec["stop"], rec["tp"], rec["rr"], rec["threshold"]))
        a("- 근거: %s" % rec["why"])
    else:
        a("- **추천 없음**: %s" % rec["why"])
    a("")
    a("## 5. 한계")
    a("")
    for li in rec["limits"]:
        a("- %s" % li)
    a("- 신호 정의는 기존 그리드와 동일하나, 피처 워밍업(120일)으로 4월 초 신호도 유효해져 "
      "스펙 기준선 11,300건과 표본 수가 다를 수 있다(정의 불일치 아님).")
    a("- 손익분기 승률은 스펙 계약식 p*=(stop+0.21)/(stop+tp). 스펙 기준선의 비대칭 인용값"
      "(R:R2 50.2%·R:R3 43.8%·R:R4 40.0%)은 이 폐쇄형과 불일치(출처 미확인) — 판정은 계약식 기준,"
      "경험적 p*(avg_loss/(avg_loss+avg_win))는 JSON 에 참고 병기.")
    a("- ATR14 = TR 14일 단순평균(종가 대비 %%, Wilder 평활 아님 — 스펙이 정의하지 않아 단순평균 채택).")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="엣지 스택 백테스트 (docs/spec_edge_stack_backtest.md)")
    ap.add_argument("--start", default=DEFAULT_START)
    ap.add_argument("--end", default=DEFAULT_END)
    ap.add_argument("--json-out", default=str(REPO / "data/reports/edge_stack_backtest.json"))
    ap.add_argument("--md-out", default=str(REPO / "data/reports/edge_stack_backtest.md"))
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    if args.start >= args.end:
        ap.error("--start 는 --end 보다 앞이어야 합니다")

    warm = (date.fromisoformat(args.start) - timedelta(days=WARMUP_DAYS)).isoformat()
    df = load(warm, args.end)
    df = edge_factors(compute_features(df))
    raw, sig = signal_frame(df, args.start, args.end)
    cfg = {"json_out": args.json_out, "md_out": args.md_out}
    rep = build_report(sig, args.start, args.end, cfg, n_loaded=len(df),
                       raw_n=len(raw))

    jtxt = json.dumps(rep, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    mdtxt = build_md(rep)
    Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.md_out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.json_out, "w", encoding="utf-8") as f:
        f.write(jtxt)
    with open(args.md_out, "w", encoding="utf-8") as f:
        f.write(mdtxt)

    if not args.quiet:
        dist = rep["edge_score_dist"]
        print("[edge_stack_backtest] %s~%s 유니버스 %d종목 · 신호 %d → 시뮬레이션 %d"
              % (args.start, args.end, df["stock_code"].nunique(),
                 rep["signals"]["raw"], rep["signals"]["after_join"]))
        print("[엣지 점수] " + " ".join("%d:%d" % (i, dist[str(i)]) for i in range(8)))
        print("[문턱 표본] " + " ".join("%s:%d%s" % (
            t, dist["thresholds"][str(t)],
            "!" if dist["thresholds"][str(t)] < MIN_SAMPLE else "")
            for t in THRESHOLDS))
        b0 = rep["baseline_no_exit"]["0"]
        g77 = next(c for c in rep["grid"] if c["threshold"] == 0 and c["stop"] == "7%")
        print("[기준선] 무손절 평균 %+.3f%%·승률 %.1f%% · 7%%/7%% 중립 %+.3f%%"
              % (b0["avg_pct"], b0["win_pct"], g77["neutral"]["avg_pct"]))
        for p in rep["walkforward"]["picks"]:
            print("[WF pick %d] %s/%s 문턱%d — 선택 EV %+.2f%%(n=%d) 홀드아웃 EV %+.2f%%(n=%d)"
                  % (p["pick_rank"], p["stop"], p["tp"], p["threshold"],
                     p["select"]["neutral"]["avg_pct"], p["select"]["n"],
                     p["holdout"]["neutral"]["avg_pct"], p["holdout"]["n"]))
        rec = rep["recommendation"]
        print("[추천] " + (("%s/%s 문턱%d — %s" % (rec["stop"], rec["tp"],
                            rec["threshold"], rec["why"])) if rec["found"]
                           else "없음 — %s" % rec["why"]))
        cl = rep["claim"]
        print("[저장] %s (%dB) / %s (%dB)" % (args.json_out, len(jtxt.encode()),
                                              args.md_out, len(mdtxt.encode())))
        print("[자기신고] source=%s parsed=%s persisted=%s" % (
            cl["source_rows"], cl["parsed_rows"], cl["persisted_rows"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

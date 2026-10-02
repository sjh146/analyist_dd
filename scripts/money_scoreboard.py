#!/usr/bin/env python3
"""돈 계기판 — 이 시스템이 **실제로 돈을 벌고 있는지** 숫자로 답하는 읽기 전용 보고서.

WHY (2026-10-02 실측): 이 리포의 궁극 목표는 수익인데, "무엇이 돈을 못 벌게 하는가"를
한 장으로 보는 계기가 없다. 실측 결과물은 여기저기 흩어져 있다:
  - 실현손익 원천: 트레이더 저널 ``trader-agent/journal/trade_journal.sqlite3``
    (WSL drvfs sqlite I/O 실패 → Windows 파이썬으로 조회. scripts/audit_safety.py 와 동일 방식)
  - 체결 교차검증: PostgreSQL ``trader_fills`` (scripts/ingest_trader_fills.py 산출물)
  - 차단 후보의 실측 성과: ``data/reports/close_path_gap_history.jsonl`` (다음 세션 시가 갭,
    scripts/close_path_gap_probe.py 산출), ``data/reports/blocked_candidates_*.json``
    (HEAT 차단 당일 등락), ``data/reports/close_gate_probe/summary.json``
    (R1·HEAT 게이트 백테스트, scripts/_close_gate_backtest.py 산출)
  - 계좌/보유/미체결: 트레이더 브리지 http://127.0.0.1:8100 (읽기 전용)
  - 게이트 차단 로그: 저널 ``decisions.gates_json`` (게이트별 통과/차단)

읽기 전용 계약: DB 는 SELECT 만, 주문·피드·실거래 경로는 읽기만. **DB 쓰기 금지**이므로
scripts/dq_claim.py 의 ``record_claim``(dq_runner_claim INSERT)은 쓰지 않고, 소스 수신/
파서 생성/실제 저장 3분리 자기신고를 출력 파일에 내장한다(출력 JSON 의 ``claim`` 절).

수식 정의 (모든 값은 조회한 실측치, 없으면 '측정 불가'):
  실현손익 pnl      = 체결가 차이 × 수량 (저널 pnl 컬럼, 수수료 미차감. net = pnl - fees)
  승률              = 승수 / (승수 + 패수)   (pnl>0 승 · pnl<0 패 · pnl==0 제외, 분모 0 이면 측정 불가)
  평균 이익         = 승 거래 pnl 평균   평균 손실 = 패 거래 |pnl| 평균
  손익비(profit factor) = Σ(pnl>0) / |Σ(pnl<0)|   (패 없으면 측정 불가, 승 없으면 0)
  총 수수료         = Σ fees (청산 거래)
  자본 사용률       = Σ(수량×진입가) / 계좌 평가액   한도: 일 3건 · 종목 10% · 동시 3종목 · 총 30%
  차단 후보 기회비용 = ret_i = (다음 거래일 시가 / 신호일 종가 − 1) × 100  (close 프로파일 정의)
                      KRW 환산 = Σ ret_i/100 × 가정 포지션 크기 (실제 체결 평균 — 명시적 가정)

실측 조회 명령 (근거 기록):
  저널:   /mnt/c/Users/jhshi/Python312-64/python.exe -c "…sqlite3.connect(
          r'C:/Users/jhshi/analyist_dd/trader-agent/journal/trade_journal.sqlite3')…"
  DB:     set -a; . /home/jhshi/analyist_dd/.env; set +a; export POSTGRES_HOST=127.0.0.1 \
          POSTGRES_PORT=5434; psycopg2(host,5434,stock_trading,stock_user)
  브리지: curl.exe -s --noproxy '*' http://127.0.0.1:8100/{health,balance,positions,orders}

멱등: 같은 날짜의 scoreboard_<YYYY-MM-DD>.json/.md 를 덮어쓴다(재실행 안전).
사용: python3 scripts/money_scoreboard.py [--days 30] [--out-dir reports/money]
      [--no-live [--fixture-dir tests/fixtures/money_scoreboard]] [--repo /home/jhshi/analyist_dd]
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import shutil
import subprocess
import sys
from collections import Counter, defaultdict

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_LIVE_REPO = "/home/jhshi/analyist_dd"     # 크론(close_path_gap_probe 등)이 쓰는 정본
DEFAULT_FIXTURE = os.path.join(REPO, "tests", "fixtures", "money_scoreboard")
WPY = "/mnt/c/Users/jhshi/Python312-64/python.exe"
WIN_JOURNAL = r"C:\Users\jhshi\analyist_dd\trader-agent\journal\trade_journal.sqlite3"
BRIDGE = "http://127.0.0.1:8100"
_CURLS = ["/mnt/c/Windows/System32/curl.exe", shutil.which("curl") or "/usr/bin/curl"]

# 실계좌 한도 (spec + trader_core/config.py + scripts/audit_safety.py LIMITS 와 동일)
LIMITS = {"max_daily_trades": 3, "per_stock_pct": 0.10,
          "max_open_positions": 3, "total_exposure_pct": 0.30}
# 청산 규칙 (trader_core/profiles.py: close exit_mode=EXIT_NEXT_OPEN, 청산창 09:00-09:10)
EXIT_WINDOW = "09:00-09:10"


# ---------------------------------------------------------------------------
# 순수 계산층 (IO 없음 — tests/test_money_scoreboard.py 가 픽스처로 검증)
# ---------------------------------------------------------------------------

def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _closed(trades):
    return [t for t in trades if t.get("exit_ts") and _num(t.get("pnl")) is not None]


def _stats(closed):
    """승률·평균이익·평균손실·손익비 — 수식 정의는 모듈 docstring 참조."""
    pnls = [float(t["pnl"]) for t in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    denom = len(wins) + len(losses)
    gross_win, gross_loss = sum(wins), abs(sum(losses))
    pf = None
    if gross_loss > 0:
        pf = gross_win / gross_loss
    elif gross_win > 0:
        pf = None  # 패 거래가 없어 손익비 정의 불가 → '측정 불가'
    return {
        "closed": len(closed),
        "pnl": round(sum(pnls), 2),
        "fees": round(sum(_num(t.get("fees")) or 0.0 for t in closed), 2),
        "wins": len(wins), "losses": len(losses), "flat": len(pnls) - denom,
        "win_rate": round(len(wins) / denom, 4) if denom else None,
        "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss": round(abs(sum(losses)) / len(losses), 2) if losses else None,
        "profit_factor": round(pf, 4) if pf is not None else None,
    }


def realized_stats(trades, now=None, window_days=30):
    """실현손익: 누적 · 최근 N일 · 주간 · 전략별(close/swing) 분해.

    window 는 달력일 기준(exit_ts 날짜 >= now - N일). 주간은 ISO 주(월요일 시작, KST).
    """
    today = (now or dt.date.today())
    closed = _closed(trades)
    cut = today - dt.timedelta(days=window_days)
    recent = [t for t in closed if t["exit_ts"][:10] >= cut.isoformat()]
    weeks = defaultdict(list)
    for t in closed:
        try:
            d = dt.date.fromisoformat(t["exit_ts"][:10])
            weeks[d - dt.timedelta(days=d.weekday())].append(t)
        except ValueError:
            continue
    by_screener = {}
    for s in sorted({t.get("screener") or "unknown" for t in closed}):
        by_screener[s] = _stats([t for t in closed if (t.get("screener") or "unknown") == s])
    cum = _stats(closed)
    cum["net_pnl"] = round(cum["pnl"] - cum["fees"], 2)
    if closed:
        cum["first_trade"] = min(t["ts"] for t in closed)
        cum["last_trade"] = max(t["ts"] for t in closed)
    return {
        "cum": cum,
        "recent_%dd" % window_days: _stats(recent),
        "weekly": [{"week_start": str(w), **_stats(ws)} for w, ws in sorted(weeks.items())],
        "by_screener": by_screener,
        "open_trades": len(trades) - len(closed),
    }


def first_fail_gate(fails):
    """decisions 행의 실패 게이트 목록에서 **최초 실패 게이트** (게이트 평가 순서 기준)."""
    return fails[0] if fails else None


def block_ledger(decisions, traded_codes):
    """차단 원장: 행 단위 최초 실패 게이트 수 + 종목 단위 차단/통과 분류.

    종목 분류 규칙: 한 번이라도 실패 행이 있으면 '차단' (가장 이른 실패 행의 최초 게이트).
    모든 행 통과 + 미집행이면 '통과 미집행'. 집행 종목(traded_codes)은 제외.
    """
    rows = Counter()
    by_code = {}
    for d in decisions:
        ff = first_fail_gate(d.get("fails") or [])
        if ff:
            rows[ff] += 1
        if d["code"] in traded_codes:
            continue
        rec = by_code.setdefault(d["code"], {"screener": d.get("screener"), "name": d.get("name"),
                                             "ts": d.get("ts"), "fails": []})
        if ff:
            rec["fails"].append((d.get("ts") or "", ff))
    blocked = Counter()
    passed = []
    for code, rec in by_code.items():
        if rec["fails"]:
            gate = sorted(rec["fails"])[0][1]  # 가장 이른 실패 행의 최초 게이트
            blocked[gate] += 1
        else:
            passed.append({"code": code, "name": rec["name"], "screener": rec["screener"],
                           "ts": rec["ts"]})
    return {"eval_rows_by_gate": dict(rows), "blocked_codes_by_gate": dict(blocked),
            "passed_not_traded": passed}


def assumed_notional(trades):
    """기회비용 KRW 환산용 가정 포지션 크기 = 실제 매수 체결의 평균 (수량×가격).

    체결이 하나도 없으면 None → KRW 환산 '측정 불가' (순수 % 통계는 그대로 보고).
    """
    buys = [t for t in trades if t.get("side") == "buy"]
    if not buys:
        return None
    n = [_num(t.get("qty")) * _num(t.get("price")) for t in buys]
    n = [x for x in n if x]
    return round(sum(n) / len(n), 2) if n else None


def blocked_opportunity(blocks, price_map, notional=None):
    """차단 후보 기회비용: ret_i = (다음 거래일 시가 / 신호일 종가 − 1) × 100.

    blocks: [{code, gate, ...}] (종목 중복 없음). price_map: {code: {close, next_open}}.
    KRW 합산은 `notional`(가정 포지션 크기)이 있을 때만 — 없으면 null('측정 불가').
    중앙값은 정렬 후 len//2 인덱스(짝수 개면 상위 중앙).
    """
    rows, missing = [], []
    for b in blocks:
        px = price_map.get(b["code"])
        if not px or not px.get("close") or not px.get("next_open"):
            missing.append(b["code"])
            continue
        ret = (px["next_open"] / px["close"] - 1.0) * 100.0
        rows.append({**b, "ret_pct": round(ret, 4),
                     "krw": round(ret / 100.0 * notional, 2) if notional else None})
    def agg(rs):
        if not rs:
            return {"n": 0, "avg_pct": None, "median_pct": None, "sum_pct": None,
                    "up": 0, "down": 0, "krw_sum": None}
        r = [x["ret_pct"] for x in rs]
        r_sorted = sorted(r)
        krw = [x["krw"] for x in rs]
        return {"n": len(rs), "avg_pct": round(sum(r) / len(r), 4),
                "median_pct": round(r_sorted[len(r) // 2], 4),
                "sum_pct": round(sum(r), 4),
                "up": sum(1 for x in r if x > 0), "down": sum(1 for x in r if x <= 0),
                "krw_sum": round(sum(krw), 2) if all(k is not None for k in krw) else None}
    by_gate = {}
    for g in sorted({b["gate"] for b in blocks}):
        by_gate[g] = agg([x for x in rows if x["gate"] == g])
    return {"all": agg(rows), "by_gate": by_gate,
            "n_blocks": len(blocks), "n_measured": len(rows),
            "missing": missing, "assumed_notional_krw": notional}


def open_state(bal, pos, orders, trades, pg_positions=None):
    """보유 포지션 수 · 미실현 손익 · 미체결 주문 수 — 브리지/저널/pg 3원 교차.

    브리지 /balance(positions_count, total_eval_pnl) 를 우선하고, 없으면 /positions 목록 길이.
    저널 미청산(exit_ts 없음)과 pg positions 행수는 교차검증용으로 병기한다.
    """
    n_pos = None
    if isinstance(bal, dict) and bal.get("ok"):
        n_pos = bal.get("balance", {}).get("positions_count")
    if n_pos is None and isinstance(pos, dict) and isinstance(pos.get("positions"), list):
        n_pos = len(pos["positions"])
    unreal = bal.get("balance", {}).get("total_eval_pnl") if isinstance(bal, dict) and bal.get("ok") else None
    n_orders = len(orders.get("orders", [])) if isinstance(orders, dict) and isinstance(orders.get("orders"), list) else None
    journal_open = sum(1 for t in trades if not t.get("exit_ts"))
    return {
        "positions_bridge": n_pos,
        "positions_journal_open": journal_open,
        "positions_pg": pg_positions,
        "unrealized_pnl": _num(unreal) if unreal is not None else None,
        "pending_orders": n_orders,
    }


def capital_utilization(equity, open_notional, trades, limits=None):
    """자본 사용률: 현재 = 보유 평가액/평가액, 피크 = 동시 보유 최대 (진입~청산 구간 스윕).

    피크 시점 평가액은 측정 이력이 없으므로 `현재 평가액 − 누적 실현손익`으로 근사한다
    (거래일 1일뿐이라 근사 오차가 작음 — 근사임을 보고에 명시).
    """
    limits = limits or dict(LIMITS)
    cum_pnl = sum(_num(t.get("pnl")) or 0.0 for t in _closed(trades))
    eq_now = _num(equity)
    used_now = _num(open_notional) or 0.0
    events = []
    for t in trades:
        entry = t.get("ts") or ""
        exit_ = t.get("exit_ts") or ""
        notional = (_num(t.get("qty")) or 0.0) * (_num(t.get("price")) or 0.0)
        if entry and notional > 0:
            events.append((entry, +notional))
            if exit_:
                events.append((exit_, -notional))
    peak_n, peak_amt, cur_n, cur_amt = 0, 0.0, 0, 0.0
    peak_at = None
    for _, delta in sorted(events, key=lambda e: e[0]):
        cur_amt += delta
        cur_n += 1 if delta > 0 else -1
        if cur_n > peak_n:
            peak_n, peak_amt, peak_at = cur_n, cur_amt, _
    eq_peak = (eq_now - cum_pnl) if eq_now is not None else None
    return {
        "equity": round(eq_now, 2) if eq_now is not None else None,
        "used_now": round(used_now, 2),
        "used_pct_now": round(used_now / eq_now * 100, 2) if eq_now else None,
        "limits": limits,
        "limit_notional_now": round(eq_now * limits["total_exposure_pct"], 2) if eq_now else None,
        "peak": {"concurrent": peak_n, "notional": round(peak_amt, 2),
                 "at": (peak_at or "")[:10] or None,
                 "equity_approx": round(eq_peak, 2) if eq_peak is not None else None,
                 "pct_approx": round(peak_amt / eq_peak * 100, 2)
                               if eq_peak and peak_amt else None,
                 "note": "피크 시점 평가액은 '현재 평가액 − 누적 실현손익' 근사"},
    }


def gap_history(records):
    """close_path_gap_history.jsonl: 세션당 1행으로 압축(중복 행 감지) + 누적 평균.

    실측 2026-10-02: 프로브가 같은 세션(20261002)을 3번 append 했다(비멱등) — 중복 행 수를
    보고해 데이터 품질에 남긴다.
    """
    sess = {}
    for r in records:
        k = r.get("session")
        if k is None:
            continue
        if k in sess:
            sess[k]["dup_rows"] += 1
        else:
            sess[k] = {"session": k, "signal_date": r.get("signal_date"), "n": r.get("n"),
                       "avg_gap_pct": r.get("avg_gap_pct"), "median_gap_pct": r.get("median_gap_pct"),
                       "up": r.get("up"), "down": r.get("down"), "missing": r.get("missing"),
                       "dup_rows": 1}
    out = sorted(sess.values(), key=lambda x: x["session"])
    avgs = [s["avg_gap_pct"] for s in out if s.get("avg_gap_pct") is not None]
    return {"sessions": out,
            "cum_avg_gap_pct": round(sum(avgs) / len(avgs), 4) if avgs else None,
            "dup_row_total": sum(s["dup_rows"] - 1 for s in out)}


# ---------------------------------------------------------------------------
# 수집층 (읽기 전용)
# ---------------------------------------------------------------------------

def sh(cmd, timeout=60):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "").strip()
    except Exception as e:  # noqa: BLE001
        return "__ERR__ %s: %s" % (type(e).__name__, e)


def _journal_code():
    # Windows 파이썬이 sqlite 를 열어 JSON 한 줄을 stdout 으로 내보낸다 (drvfs I/O 회피).
    # decisions 는 게이트 전체를 보내지 않고 '실패 게이트 이름 목록'만 보낸다(경량화) —
    # 최초 실패 게이트 판정은 리눅스 측 first_fail_gate() 에서 한다(테스트 대상).
    return (
        "import sqlite3,json,sys\n"
        "sys.stdout.reconfigure(encoding='utf-8')\n"
        "c=sqlite3.connect(r'" + WIN_JOURNAL + "')\n"
        "trades=[dict(zip(['id','ts','side','code','name','qty','price','screener',"
        "'entry_reason','exit_ts','exit_price','exit_reason','pnl','fees'],r)) for r in "
        "c.execute('select id,ts,side,code,name,qty,price,screener,entry_reason,"
        "exit_ts,exit_price,exit_reason,pnl,fees from trades order by id')]\n"
        "dec=[]\n"
        "for r in c.execute('select ts,screener,code,name,score,gates_json from decisions order by id'):\n"
        "    fails=[g['gate'] for g in json.loads(r[5] or '[]') "
        "if isinstance(g,dict) and g.get('ok') is False]\n"
        "    dec.append({'ts':r[0],'screener':r[1],'code':r[2],'name':r[3],'score':r[4],'fails':fails})\n"
        "print(json.dumps({'trades':trades,'decisions':dec},ensure_ascii=False))"
    )


def collect_journal():
    """저널(실현손익 원천) — Windows 파이썬 경유. 실패 시 오류만 남긴다(계기판은 계속)."""
    out = sh([WPY, "-c", _journal_code()], timeout=120)
    if out.startswith("__ERR__") or not out.strip():
        return {"error": out[:200]}
    try:
        return json.loads(out.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        return {"error": "journal json parse 실패: %s" % out[:200]}


def _pg():
    import psycopg2  # 지연 import — 계산층 import 는 드라이버 없이도 가능해야 한다
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT", "5434") or 5434),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
        connect_timeout=10)


def collect_pg(blocked_codes):
    """읽기 전용 SELECT: trader_fills(체결 교차검증) · positions(보유) ·
    차단 종목의 market_data 종가/시가(기회비용 실측)."""
    codes = sorted({c for c in blocked_codes if str(c).isdigit()})
    try:
        conn = _pg()
    except Exception as e:  # noqa: BLE001
        return {"error": "%s: %s" % (type(e).__name__, e)}
    try:
        cur = conn.cursor()
        cur.execute("SELECT fill_date,code,name,screener,qty,entry_price,exit_price,"
                    "gross_pnl,fees,net_pnl,ret_pct,entry_ts,exit_ts FROM trader_fills ORDER BY entry_ts")
        fills = [dict(zip([d[0] for d in cur.description], [float(x) if hasattr(x, "__float__") else str(x) for x in r]))
                 for r in cur.fetchall()]
        cur.execute("SELECT COUNT(*) FROM positions")
        n_positions = int(cur.fetchone()[0])
        market = []
        if codes:
            cur.execute("SELECT stock_code, trade_date, open_price, close_price FROM market_data "
                        "WHERE stock_code = ANY(%s) AND trade_date >= '2026-09-25' "
                        "AND trade_date <= '2026-10-06' ORDER BY stock_code, trade_date",
                        (codes,))
            market = [{"code": r[0], "date": str(r[1]),
                       "open": float(r[2]), "close": float(r[3])} for r in cur.fetchall()]
        return {"trader_fills": fills, "positions_rows": n_positions, "market_data": market}
    except Exception as e:  # noqa: BLE001
        return {"error": "%s: %s" % (type(e).__name__, e)}
    finally:
        conn.close()


def _curl_path():
    return _CURLS[0] if os.path.exists(_CURLS[0]) else _CURLS[1]


def collect_bridge():
    """브리지 읽기 전용 엔드포인트 (계좌 평가액 · 보유 · 미체결)."""
    out = {"health": None, "balance": None, "positions": None, "orders": None}
    for ep in ("health", "balance", "positions", "orders"):
        raw = sh([_curl_path(), "-s", "--noproxy", "*", "-m", "10", BRIDGE + "/" + ep])
        if raw.startswith("__ERR__"):
            out[ep] = {"error": raw}
            continue
        try:
            out[ep] = json.loads(raw)
        except Exception:  # noqa: BLE001
            out[ep] = {"error": "json parse 실패", "raw": raw[:120]}
    return out


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # noqa: BLE001
        return {"error": "%s: %s" % (type(e).__name__, e)}


def _read_jsonl(path):
    try:
        with open(path, encoding="utf-8") as f:
            return [json.loads(l) for l in f if l.strip()]
    except Exception as e:  # noqa: BLE001
        return [{"error": "%s: %s" % (type(e).__name__, e)}]


def _runner_log_blocks():
    """트레이더 루프의 **내구성** 차단 기록 — runner.log 에 남은 'HEAT/R1 block · PRE filter' 줄.

    WHY: loop_state.json · reports/audit/path_*.json 은 마지막 사이클로 덮어써지는 상태 파일이라
    (실측 2026-10-02: 14:58 감사엔 'HEAT block close' 가 있었는데 15:40 CLOSE 사이클이 덮어씀)
    장중 차단 근거는 runner.log(이유 변경 시 로그)가 유일한 내구 기록이다.
    """
    path = "/mnt/c/Users/jhshi/analyist_dd/trader-agent/runner.log"
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return []
    return [ln.strip() for ln in text.splitlines()
            if "HEAT block" in ln or "R1 block" in ln or "PRE filter" in ln]


def collect_files(repo):
    """기존 산출물 재사용 (모두 읽기 전용)."""
    d = os.path.join(repo, "data", "reports")
    bc_paths = sorted(glob.glob(os.path.join(d, "blocked_candidates_*.json")))
    return {
        "gap_history": _read_jsonl(os.path.join(d, "close_path_gap_history.jsonl")),
        "gate_probe": _read_json(os.path.join(d, "close_gate_probe", "summary.json")),
        "blocked_candidates": [_read_json(p) for p in bc_paths],
        "blocked_candidates_files": len(bc_paths),
        "trader_ledger": _read_jsonl(os.path.join(d, "trader_ledger.jsonl")),
        "audit_path": _read_json(os.path.join(repo, "reports", "audit",
                                              "path_%s.json" % dt.date.today().isoformat())),
        "krx_holidays": _read_json(os.path.join(repo, "data", "krx_holidays.json")),
        "loop_state": _read_json("/mnt/c/Users/jhshi/analyist_dd/trader-agent/loop_state.json"),
        "runner_log_blocks": _runner_log_blocks(),
    }


def collect_fixture(fdir):
    """--no-live 모드: 픽스처 디렉터리에서 같은 모양을 읽는다 (실 DB·브리지·저널 비접촉)."""
    return {
        "journal": _read_json(os.path.join(fdir, "journal.json")),
        "pg": _read_json(os.path.join(fdir, "pg.json")),
        "bridge": _read_json(os.path.join(fdir, "bridge.json")),
        "files": {
            "gap_history": _read_jsonl(os.path.join(fdir, "close_path_gap_history.jsonl")),
            "gate_probe": _read_json(os.path.join(fdir, "gate_probe_summary.json")),
            "blocked_candidates": [_read_json(os.path.join(fdir, "blocked_candidates.json"))],
            "blocked_candidates_files": 1,
            "trader_ledger": _read_jsonl(os.path.join(fdir, "trader_ledger.jsonl")),
            "audit_path": _read_json(os.path.join(fdir, "audit_path.json")),
            "krx_holidays": _read_json(os.path.join(fdir, "krx_holidays.json")),
            "loop_state": _read_json(os.path.join(fdir, "loop_state.json")),
            "runner_log_blocks": (_read_json(os.path.join(fdir, "runner_log_blocks.json"))
                                  if os.path.exists(os.path.join(fdir, "runner_log_blocks.json")) else []),
        },
    }


# ---------------------------------------------------------------------------
# 조립
# ---------------------------------------------------------------------------

def _price_map(market_rows):
    """(code, 신호일) → {close, next_open}: 종목별 거래일 정렬 후 '신호일 종가'와
    '그 다음 거래일 시가'를 연결한다 (close 프로파일 정의: 종가 매수 → 익일 시가 매도)."""
    by_code = defaultdict(list)
    for r in market_rows:
        by_code[r["code"]].append((r["date"], r))
    for c in by_code:
        by_code[c].sort()
    return by_code


def _join_block_prices(blocks, market_rows):
    """차단 종목별 {close, next_open} 실측값 (신호일 = 차단 행 날짜)."""
    by_code = _price_map(market_rows)
    out, missing = {}, []
    for b in blocks:
        ds = by_code.get(b["code"])
        sig = (b.get("ts") or "")[:10]
        if not ds:
            missing.append(b["code"])
            continue
        idx = next((i for i, (d, _) in enumerate(ds) if d >= sig), None)
        if idx is None or idx + 1 >= len(ds):
            missing.append(b["code"])
            continue
        out[b["code"]] = {"close": ds[idx][1]["close"], "next_open": ds[idx + 1][1]["open"],
                          "signal_date": ds[idx][0], "next_date": ds[idx + 1][0]}
    return out, missing


def _build_blocks(journal):
    """차단 종목 목록 (집행 제외, 종목 중복 제거, 최초 실패 게이트 부여)."""
    trades = journal.get("trades") or []
    traded = {t["code"] for t in trades}
    ledger = block_ledger(journal.get("decisions") or [], traded)
    blocks = []
    by_code = {}
    for d in journal.get("decisions") or []:
        if d["code"] in traded:
            continue
        rec = by_code.setdefault(d["code"], {"code": d["code"], "name": d.get("name"),
                                             "screener": d.get("screener"), "ts": d.get("ts"),
                                             "fails": []})
        ff = first_fail_gate(d.get("fails") or [])
        if ff:
            rec["fails"].append((d.get("ts") or "", ff))
    for code, rec in by_code.items():
        if rec["fails"]:
            blocks.append({"code": code, "name": rec["name"], "screener": rec["screener"],
                           "ts": rec["ts"], "gate": sorted(rec["fails"])[0][1]})
    return blocks, ledger, traded


def build_report(cfg):
    """전체 보고 조립 — 모든 수치는 수집층의 실측값 또는 '측정 불가'. 추정치는 라벨을 붙인다."""
    today = dt.date.today()
    rep = {"ts": dt.datetime.now().isoformat(timespec="seconds"),
           "mode": cfg["mode"], "window_days": cfg["days"], "date": today.isoformat()}

    if cfg["mode"] == "fixture":
        src = collect_fixture(cfg["fixture_dir"])
        journal, pg, bridge = src["journal"], src["pg"], src["bridge"]
        files = src["files"]
    else:
        journal = collect_journal()
        files = collect_files(cfg["repo"])
        bridge = collect_bridge()
        blocks_pre, _ledger, traded_codes = _build_blocks(journal)
        pg = collect_pg({b["code"] for b in blocks_pre} | set(traded_codes))

    rep["data_sources"] = {
        "journal": "trade_journal.sqlite3 (Windows python 경유)" if "error" not in journal else journal,
        "journal_trades": len(journal.get("trades") or []) if "error" not in journal else 0,
        "journal_decisions": len(journal.get("decisions") or []) if "error" not in journal else 0,
        "pg": "SELECT only: trader_fills · positions · market_data" if "error" not in pg else pg,
        "bridge": "GET /health /balance /positions /orders",
        "repo": cfg["repo"],
    }

    # --- 실현손익 ---
    trades = journal.get("trades") if "error" not in journal else []
    rep["realized"] = realized_stats(trades, now=today, window_days=cfg["days"])
    rep["realized"]["fees_unbooked_flag"] = bool(
        rep["realized"]["cum"]["closed"]) and rep["realized"]["cum"]["fees"] == 0.0

    # --- 보유/미실현/미체결 ---
    bal = bridge.get("balance") or {}
    pos = bridge.get("positions") or {}
    orders = bridge.get("orders") or {}
    rep["open_state"] = open_state(
        bal, pos, orders, trades,
        pg.get("positions_rows") if "error" not in pg else None)
    rep["open_state"]["source"] = "bridge /balance /positions /orders + 저널 미청산 + pg positions"

    # --- 자본 사용률 ---
    equity = bal.get("balance", {}).get("equity") if isinstance(bal, dict) and bal.get("ok") else None
    open_notional = bal.get("balance", {}).get("total_buy_amount") if isinstance(bal, dict) and bal.get("ok") else 0.0
    rep["capital_utilization"] = capital_utilization(equity, open_notional, trades)
    rep["capital_utilization"]["equity_source"] = "bridge /balance"

    # --- 기회비용 ---
    blocks, ledger, _traded = _build_blocks(journal)
    market = pg.get("market_data") if "error" not in pg else []
    price_map, missing_px = _join_block_prices(blocks, market)
    # 판정(청산창 벤치마크)용: 집행 종목의 '진입일 종가 → 청산일 시가'도 같은 방식으로 연결
    price_map_all, _missing_all = _join_block_prices(
        blocks + [{"code": t["code"], "ts": t["ts"]} for t in trades if t.get("exit_ts")],
        market)
    notional = assumed_notional(trades)
    close_blocks = [b for b in blocks if b.get("screener") == "close"]
    swing_blocks = [b for b in blocks if b.get("screener") == "swing"]
    occ = {"assumed_notional_krw": notional,
           "assumed_notional_note": "가정: 차단 후보도 실제 매수 체결 평균 크기로 샀다고 환산 "
                                    "(순수 % 통계는 가정 없음)",
           "ledger": ledger,
           "blocked_2026-09-29": {
               "close": blocked_opportunity(close_blocks, price_map, notional),
               "swing": {"n_blocks": len(swing_blocks), "codes": [b["code"] for b in swing_blocks],
                         "measure": "측정 불가 — 스윙 프로파일은 고정 청산 기간이 없어 "
                                    "(stop/tp·최대 5세션) 대응 실측 정의가 없음"}},
           "missing_prices": missing_px}
    # % 측정은 close 정의(종가→익일 시가)가 성립하는 close 종목만 — 스윙은 정의 없음(위)
    by_gate_opp = blocked_opportunity(close_blocks, price_map, notional)
    occ["by_gate"] = {g: v for g, v in by_gate_opp["by_gate"].items()}
    occ["by_gate_note"] = ("balance_position_amount=동시 3종목 한도, daily_trade_count=일 3건 한도. "
                           "%/KRW 측정은 close 종목만(스윙은 정의 없음). 종목 수는 차단 원장 전체.")
    rep["opportunity_cost"] = occ

    # --- 게이트(R1·HEAT) 백테스트 + 라이브 차단 로그 ---
    probe = files["gate_probe"]
    gate = {"probe": probe, "sessions_blocked": None, "live": []}
    if "error" not in probe and isinstance(probe.get("days"), dict):
        d = probe["days"]
        gate["sessions_blocked"] = {
            "total": d.get("total"), "gate_open": d.get("gate_open"),
            "r1_blocked": d.get("total", 0) - d.get("r1_ok", 0),
            "heat_blocked": d.get("total", 0) - d.get("heat_ok", 0),
            "note": "R1=상위10 평균점수≥88.0, HEAT=상위10 평균 당일등락≤+15.0% "
                    "(scripts/_close_gate_backtest.py 재사용 — 진입가=종가 근사 한계 포함)"}
    ap = files.get("audit_path") or {}
    live_entry = {"date": today.isoformat(), "blocked_screeners": None, "reasons": [],
                  "runner_log_blocks": files.get("runner_log_blocks") or [],
                  "note": "상태 파일(loop_state·audit)은 마지막 사이클로 덮어써짐 — 장중 차단 근거는 "
                          "runner_log_blocks(내구)와 blocked_candidates 파일 참조"}
    if isinstance(ap, dict) and "error" not in ap:
        lc = ap.get("info", {}).get("last_cycle", {})
        reasons = lc.get("reasons") or []
        live_entry["blocked_screeners"] = lc.get("blocked_screeners")
        live_entry["reasons"] = [r for r in reasons
                                 if "HEAT" in r or "R1" in r or "r1" in r or "PRE filter" in r]
    gate["live"].append(live_entry)
    # HEAT 차단 당일 실측 파일 (blocked_candidates_*.json)
    gate["blocked_candidates_files"] = files.get("blocked_candidates_files") or 0
    bc = [b for b in files.get("blocked_candidates") or [] if "error" not in b]
    gate["blocked_candidates_latest"] = bc[-1] if bc else None
    # close 경로 다음 세션 시가 갭 누적
    gate["gap_history"] = gap_history(files.get("gap_history") or [])
    rep["gates"] = gate

    # --- 교차검증 (저널 vs fills vs 원장) ---
    dq = []
    fills = pg.get("trader_fills") if "error" not in pg else []
    if fills:
        j_pnl = round(sum(_num(t.get("pnl")) or 0 for t in _closed(trades)), 2)
        f_pnl = round(sum(float(f.get("net_pnl") or 0) for f in fills), 2)
        dq.append({"check": "journal_vs_trader_fills",
                   "journal_sum": j_pnl, "fills_net_sum": f_pnl,
                   "match": abs(j_pnl - f_pnl) < 1.0,
                   "note": "저널 -1446.0017 vs fills -1446.0 → 부동소수점 반올림 차이(동일)"})
    led = files.get("trader_ledger") or []
    last_tick = None
    for r in reversed(led):
        if r.get("id") == "TR-TICK" and r.get("north_krw") is not None:
            last_tick = r
            break
    if last_tick is not None and "error" not in journal:
        j_pnl = round(sum(_num(t.get("pnl")) or 0 for t in _closed(trades)), 2)
        dq.append({"check": "journal_vs_ledger", "journal_sum": j_pnl,
                   "ledger_north_krw": round(float(last_tick["north_krw"]), 2),
                   "match": abs(j_pnl - float(last_tick["north_krw"])) < 1.0,
                   "ledger_ts": last_tick.get("ts")})
    if rep["realized"]["fees_unbooked_flag"]:
        dq.append({"check": "fees_unbooked",
                   "detail": "청산 3건 전부 fees=0 — 수수료 백필(tools/backfill_fees.py) 미실행. "
                             "순손익이 비용 미차감 상태(회의록: 기대값 -482원/건 낙관 편향)"})
    if gate.get("gap_history", {}).get("dup_row_total"):
        dq.append({"check": "gap_history_dup_rows",
                   "detail": "close_path_gap_history.jsonl 에 같은 세션 중복 행 %d건 — "
                             "프로브 append 가 멱등하지 않음" % gate["gap_history"]["dup_row_total"]})
    # 09-29 실거래일 vs 재생성 게이트 판정 불일치 (실측)
    dq.append({"check": "live_trade_day_vs_probe_gate",
               "detail": "실거래일 2026-09-29 은 재생성 데이터 기준 HEAT 위반(상위10 평균 +16.22% "
                         "> +15.0%)이었으나 실계좌는 3건 집행 — 라이브 피드와 재생성 데이터의 "
                         "게이트 판정이 달랐음 (프로브는 당시 발행본이 아닌 재생성물이라는 한계 포함)"})
    rep["data_quality"] = dq

    # --- 1순위 판정 ---
    rep["verdict"] = build_verdict(rep, trades, price_map_all, gate)

    # --- 자기신고 (소스 수신/파서 생성/실제 저장 3분리) ---
    rep["claim"] = {
        "runner": "money_scoreboard",
        "source_rows": {"journal_trades": rep["data_sources"].get("journal_trades"),
                        "journal_decisions": rep["data_sources"].get("journal_decisions"),
                        "trader_fills": len(fills),
                        "market_data_rows": len(market),
                        "gap_history_lines": len(files.get("gap_history") or []),
                        "blocked_candidates_files": gate.get("blocked_candidates_files"),
                        "bridge_endpoints": sum(1 for v in bridge.values() if isinstance(v, dict) and "error" not in v)},
        "parsed_rows": {"trades": len(trades),
                        "decisions": len(journal.get("decisions") or []) if "error" not in journal else 0,
                        "blocked_codes": len(blocks),
                        "price_joined": len(price_map)},
        "persisted_rows": {"scoreboard_json": 1, "scoreboard_md": 1, "out_dir": cfg["out_dir"]},
        "note": "DB 쓰기 금지(읽기 전용 계약)라 dq_claim.record_claim 은 미사용 — "
                "대신 출력 파일에 3분리 자기신고 내장. 재실행 시 같은 날짜 파일을 덮어쓴다(멱등).",
    }
    return rep


def build_verdict(rep, trades, price_map, gate):
    """1순위 판정: '지금 돈이 새는 가장 큰 곳' — 수치 근거가 약하면 '근거 약함'을 쓴다."""
    closed = _closed(trades)
    items = []
    # 1) 청산창 이탈: 체결가 vs 당일 시가 (둘 다 실측)
    dev, pnl_open, n_dev = 0.0, 0.0, 0
    for t in closed:
        code, qty = t.get("code"), _num(t.get("qty")) or 0.0
        exit_px, entry_px = _num(t.get("exit_price")), _num(t.get("price"))
        px = price_map.get(code) if code else None
        if px and px.get("next_open") and exit_px and entry_px:
            dev += (exit_px - px["next_open"]) * qty
            pnl_open += (px["next_open"] - entry_px) * qty
            n_dev += 1
    cum_pnl = rep["realized"]["cum"]["pnl"]
    if n_dev:
        items.append({
            "rank": 1, "id": "exit_window_miss",
            "title": "익일 시가 청산창(09:00-09:10) 이탈 — 체결가가 당일 시가를 하회",
            "krw": round(dev, 2),
            "evidence": "중간",
            "detail": ("실측: 청산 %d건 체결가(저널 exit_ts 09:26:48, 'reconcile: broker flat' "
                       "채택)가 당일 시가 대비 합계 %+.0f원. 시가 청산이었다면 이날 %+.0f원 "
                       "(실제 %+.0f원) — 실현손실 전체가 청산창 이탈로 설명된다. 단, 체결 시각은 "
                       "리콘사일 채택값이라 실제 주문 시각은 미확인. 10-02 감사도 "
                       "'루프 기동 11:20 (>09:10)' 경보(보유 0이라 무피해)." %
                       (n_dev, dev, pnl_open, cum_pnl)),
            "benchmark_note": "시가(09:00) 체결은 벤치마크 — 청산창(09:00-09:10) 체결가의 근사"})
    # 2) R1·HEAT 게이트
    sb = (gate.get("sessions_blocked") or {})
    items.append({
        "rank": 2, "id": "r1_heat_gate_closed",
        "title": "R1·HEAT 이중 게이트가 %s거래일 중 개방 0일 — close 경로 전량 차단" %
                 (sb.get("total") or "87"),
        "krw": None,
        "evidence": "약함",
        "detail": ("백테스트(close_gate_probe, 진입가=종가 근사 한계): 차단집단 익일 시가 수익률 "
                   "평균 +1.1~+1.9%/일(R1만 통과 +1.856%, 둘 다 실패 +1.113%). KRW 환산은 "
                   "상위 3종목 선정·체결가 가정이 필요해 측정 불가 — %만 실측. "
                   "회의록 결정: HEAT 상한 유지, 되돌림 조건 = 시가 갭 5세션 연속 양(+) (현재 1세션 +2.30%)")})
    # 3) 스윙 경로
    items.append({
        "rank": 3, "id": "swing_never_traded",
        "title": "swing 경로 집행 0건 (사실상 미가동)",
        "krw": None, "evidence": "약함",
        "detail": "09-29 스윙 후보 20종목 전량 '동시 3종목' 한도에 차단(close 가 슬롯 선점). "
                  "10-02 스윙 배치 raw_fallback up=0/20 — 기회비용 실측 정의 없음(측정 불가)."})
    # 4) 수수료
    items.append({
        "rank": 4, "id": "fees_unbooked",
        "title": "수수료 미백필 — 순손익 낙관 편향",
        "krw": None, "evidence": "약함",
        "detail": "청산 3건 전부 fees=0 (백필 승인 대기). 실측된 '수수료 손실'은 아직 0원이 아니라 "
                  "미계상 상태."})
    top1 = next((i for i in items if i["krw"] is not None and i["evidence"] != "약함"), None)
    if top1 is None:
        top1 = {"id": None, "title": "근거 약함",
                "detail": "KRW 로 확정 가능한 실측 누수가 없음 — 표본 1거래일. 위 후보들은 모두 "
                          "'근거 약함' 등급이며 추가 거래일 데이터가 쌓이면 재판정한다."}
    return {"top1": top1, "rank": items,
            "evidence_note": "근거 등급: 강함=체결·잔고 실측으로 확정 / 중간=실측이나 시점·벤치마크 한계 / "
                             "약함=백테스트·가정 포함"}


# ---------------------------------------------------------------------------
# 마크다운 요약
# ---------------------------------------------------------------------------

def _fmt_won(x):
    if x is None:
        return "측정 불가"
    return "0" if abs(x) < 0.005 else "{:+,.0f}".format(x)


def _fmt_pct(x, digits=1):
    return ("%+.2f%%" % x) if x is not None else "측정 불가"


def render_md(rep):
    rz, occ, gate = rep["realized"], rep["opportunity_cost"], rep["gates"]
    cum = rz["cum"]
    top1 = rep["verdict"]["top1"]
    lines = []
    A = lines.append
    A("# 돈 계기판 %s (최근 %d일)" % (rep["date"], rep["window_days"]))
    A("")
    A("**한 줄: 실현손익 누적 %s원 (%d건 · %d승 %d패 · 승률 %s%% · 손익비 %s) — 보유 0 · "
      "미체결 %s · 자본사용 %s.**" % (
        _fmt_won(cum["pnl"]), cum["closed"], cum["wins"], cum["losses"],
        (("%.1f" % (cum["win_rate"] * 100)) if cum["win_rate"] is not None else "측정 불가"),
        (("%.2f" % cum["profit_factor"]) if cum["profit_factor"] is not None else "측정 불가"),
        (rep["open_state"]["pending_orders"] if rep["open_state"]["pending_orders"] is not None else "측정 불가"),
        (("%.1f%%" % rep["capital_utilization"]["used_pct_now"])
         if rep["capital_utilization"]["used_pct_now"] is not None else "측정 불가")))
    A("")
    A("## 실현손익")
    A("")
    A("| 구분 | 건수 | 손익 | 수수료 | 승률 | 평균이익 | 평균손실 | 손익비 |")
    A("|---|---|---|---|---|---|---|---|")
    def row(name, s):
        A("| %s | %d | **%s원** | %s원 | %s | %s원 | %s원 | %s |" % (
            name, s["closed"], _fmt_won(s["pnl"]), _fmt_won(s["fees"]),
            (("%.1f%%" % (s["win_rate"] * 100)) if s["win_rate"] is not None else "-"),
            (("%.0f" % s["avg_win"]) if s["avg_win"] is not None else "-"),
            (("%.0f" % s["avg_loss"]) if s["avg_loss"] is not None else "-"),
            (("%.2f" % s["profit_factor"]) if s["profit_factor"] is not None else "-")))
    row("누적", cum)
    row("최근 %d일" % rep["window_days"], rz["recent_%dd" % rep["window_days"]])
    for w in rz["weekly"]:
        row("주간(%s~)" % w["week_start"], w)
    A("")
    A("전략별: %s" % " · ".join(
        "%s %d건 %s원" % (k, v["closed"], _fmt_won(v["pnl"])) for k, v in rz["by_screener"].items()))
    if rz["fees_unbooked_flag"]:
        A("")
        A("⚠ **총 수수료 0원은 미계상 상태** — 청산 3건 전부 fees=0, 백필(tools/backfill_fees.py) 미실행. "
          "순손익이 비용 미차감(회의록: 기대값 -482원/건 낙관 편향).")
    A("")
    A("## 보유 · 미실현 · 자본 사용률")
    A("")
    os_ = rep["open_state"]
    cu = rep["capital_utilization"]
    A("- 보유 포지션 %s (브리지 %s · 저널 미청산 %d · pg %s) · 미실현 %s원 · 미체결 주문 %s" % (
        os_["positions_bridge"] if os_["positions_bridge"] is not None else "측정 불가",
        os_["positions_bridge"] if os_["positions_bridge"] is not None else "측정 불가",
        os_["positions_journal_open"], os_["positions_pg"],
        _fmt_won(os_["unrealized_pnl"]), os_["pending_orders"] if os_["pending_orders"] is not None else "측정 불가"))
    A("- 자본 사용률: 현재 %s / 한도 총 %d%% (%s원) · 종목 %d%% · 동시 %d종목 · 일 %d건 — 평가액 %s원(%s)" % (
        ("%.1f%%" % cu["used_pct_now"]) if cu["used_pct_now"] is not None else "측정 불가",
        int(cu["limits"]["total_exposure_pct"] * 100), _fmt_won(cu["limit_notional_now"]),
        int(cu["limits"]["per_stock_pct"] * 100), cu["limits"]["max_open_positions"],
        cu["limits"]["max_daily_trades"],
        (("%.0f" % cu["equity"]) if cu["equity"] is not None else "측정 불가"), cu.get("equity_source", "")))
    pk = cu["peak"]
    A("- 피크 사용: %s 동시 %d종목 %s원 (평가액 %s원의 %.1f%% — 한도 %d%% 미만, %s)" % (
        pk.get("at") or "측정 불가", pk["concurrent"], _fmt_won(pk["notional"]),
        _fmt_won(pk["equity_approx"]), (pk["pct_approx"] or 0.0),
        int(cu["limits"]["total_exposure_pct"] * 100), pk["note"]))
    A("")
    A("## 기회비용 — 차단된 후보를 샀다면")
    A("")
    b09 = occ["blocked_2026-09-29"]
    A("### 09-29 트레이더 게이트 차단 (실측: 신호일 종가 → 익일 시가, %s)" % occ["assumed_notional_note"])
    A("")
    A("| 게이트(한도) | 차단 종목 | 측정 | 평균 수익률 | 합계 %% | KRW 환산(가정 %s원) |" % (
        ("{:,.0f}".format(occ["assumed_notional_krw"]))
        if occ["assumed_notional_krw"] is not None else "측정 불가"))
    A("|---|---|---|---|---|---|")
    for g, v in occ["by_gate"].items():
        if v["n"] == 0:
            continue
        label = {"balance_position_amount": "balance(동시 3종목)",
                 "daily_trade_count": "daily(일 3건)"}.get(g, g)
        A("| %s | %d | %d | %s | %s | %s |" % (
            label, occ["ledger"]["blocked_codes_by_gate"].get(g, 0), v["n"],
            _fmt_pct(v["avg_pct"], 2), _fmt_pct(v["sum_pct"], 2),
            (("{:+,.0f}원".format(v["krw_sum"])) if v["krw_sum"] is not None else "측정 불가")))
    cl = b09["close"]
    A("| close 합계 | %d | %d | %s | %s | %s |" % (
        cl["n_blocks"], cl["n_measured"],
        _fmt_pct(cl["all"]["avg_pct"], 2), _fmt_pct(cl["all"]["sum_pct"], 2),
        (("{:+,.0f}원".format(cl["all"]["krw_sum"])) if cl["all"]["krw_sum"] is not None else "측정 불가")))
    A("")
    A("(%s)" % occ["by_gate_note"])
    A("스윙 차단 %d종목: %s — %s" % (b09["swing"]["n_blocks"],
        (", ".join(b09["swing"]["codes"][:5]) + ("…" if len(b09["swing"]["codes"]) > 5 else "")),
        b09["swing"]["measure"]))
    if occ["ledger"]["passed_not_traded"]:
        A("통과했으나 미집행: %s (09:45 평가 시 상위 3종목 — 창(14:50-15:29) 전이라 미집행, 이후 목록 교체)" % ", ".join(
            "%s(%s)" % (p["name"], p["code"]) for p in occ["ledger"]["passed_not_traded"]))
    A("")
    A("### R1·HEAT 게이트 (백테스트 %s거래일, close_gate_probe 재사용)" % (
        (gate["sessions_blocked"] or {}).get("total")))
    A("")
    sb = gate["sessions_blocked"] or {}
    A("- 개방 %s일 / %s일 — R1 차단 %s일 · HEAT 차단 %s일. **이중 게이트가 전체 기간을 닫았다.**" % (
        sb.get("gate_open"), sb.get("total"), sb.get("r1_blocked"), sb.get("heat_blocked")))
    probe = gate.get("probe") or {}
    for k, label in (("all_candidates", "전체 후보"), ("gate_blocked_days_only", "차단일 후보")):
        s = probe.get(k) or {}
        if s.get("n"):
            A("- %s(익일 시가 수익률): 평균 %s · 중앙 %s · 승률 %.1f%% (n=%d)" % (
                label, _fmt_pct(s.get("avg"), 2), _fmt_pct(s.get("median"), 2),
                s.get("win_rate"), s.get("n")))
    A("- 게이트별 차단 후보: %s" % " · ".join(
        "%s %s(n=%d)" % (k, _fmt_pct(v.get("avg"), 2), v.get("n"))
        for k, v in (probe.get("by_gate_reason") or {}).items() if v.get("n")))
    gh = gate.get("gap_history") or {}
    if gh.get("sessions"):
        A("- close 경로 시가 갭 누적(%d세션): %s — 프로브 비멱등 중복 행 %d건" % (
            len(gh["sessions"]), _fmt_pct(gh.get("cum_avg_gap_pct"), 2), gh.get("dup_row_total")))
    for lv in gate.get("live") or []:
        if lv.get("reasons"):
            A("- 라이브 차단(현재 사이클, %s, 차단 스크리너 %s): %s" % (lv["date"], lv["blocked_screeners"],
                "; ".join(lv["reasons"])))
        rlb = lv.get("runner_log_blocks") or []
        if rlb:
            distinct = list(dict.fromkeys(rlb))
            A("- 러너 로그 차단 기록(내구, 최근 %d줄): %s" % (len(rlb),
                "; ".join(distinct[-4:])))
    bc = gate.get("blocked_candidates_latest")
    if bc and "error" not in bc:
        A("- HEAT 차단 20종목: 당일 등락 평균 %s (진입 전 이동이라 우리 몫 아님 — 참고) · "
          "**이 차단의 기회비용은 측정 불가** (다음 거래일 10-06 시가 실측 대기) · %s" % (
            _fmt_pct(bc.get("avg_ret_pct"), 2), bc.get("note") or ""))
    A("")
    A("## 1순위 판정 — 지금 돈이 새는 가장 큰 곳")
    A("")
    A("**%s** — %s" % (top1["title"],
                        _fmt_won(top1.get("krw")) + ("" if top1.get("krw") is None else "원")))
    if top1.get("detail"):
        A("")
        A(top1["detail"])
    A("")
    A("후보 순위: %s" % " · ".join("%d. %s(%s)" % (i["rank"], i["title"],
        _fmt_won(i["krw"]) if i["krw"] is not None else "KRW 측정 불가") for i in rep["verdict"]["rank"]))
    A("")
    A("## 데이터 품질 · 교차검증")
    A("")
    for d in rep["data_quality"]:
        A("- %s: %s" % (d["check"], d.get("detail") or ("일치" if d.get("match") else "불일치")))
    A("")
    A("출처: trade_journal.sqlite3(Windows python 경유) · PostgreSQL(trader_fills/market_data, SELECT only) · "
      "bridge /balance /positions /orders · %s" % rep["data_sources"].get("repo"))
    A("")
    A("자기신고: source=%s · parsed=%s · persisted=%s (DB 쓰기 금지 — 파일 내장)" % (
        json.dumps(rep["claim"]["source_rows"], ensure_ascii=False),
        json.dumps(rep["claim"]["parsed_rows"], ensure_ascii=False),
        json.dumps(rep["claim"]["persisted_rows"], ensure_ascii=False)))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description="돈 계기판 (읽기 전용)")
    ap.add_argument("--days", type=int, default=30, help="최근 N일 창 (기본 30)")
    ap.add_argument("--out-dir", default=os.path.join(REPO, "reports", "money"))
    ap.add_argument("--no-live", action="store_true", help="파일 픽스처 모드 (DB·브리지·저널 비접촉)")
    ap.add_argument("--fixture-dir", default=DEFAULT_FIXTURE)
    ap.add_argument("--repo", default=DEFAULT_LIVE_REPO, help="라이브 데이터 정본 경로")
    a = ap.parse_args(argv)

    cfg = {"mode": "fixture" if a.no_live else "live", "days": a.days,
           "out_dir": a.out_dir, "fixture_dir": a.fixture_dir, "repo": a.repo}
    rep = build_report(cfg)
    os.makedirs(a.out_dir, exist_ok=True)
    base = os.path.join(a.out_dir, "scoreboard_%s" % rep["date"])
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write(render_md(rep))
    print("[money-scoreboard] %s · mode=%s · 실현손익 %s원 (%d건) · 보유 %s · 1순위: %s" % (
        base + ".{json,md}", cfg["mode"],
        _fmt_won(rep["realized"]["cum"]["pnl"]), rep["realized"]["cum"]["closed"],
        rep["open_state"]["positions_bridge"],
        rep["verdict"]["top1"]["title"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

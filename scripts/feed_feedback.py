#!/usr/bin/env python3
"""실매매 → analyist_dd 피드백 계측 — docs/spec_feed_feedback.md 계약 구현.

WHY (2026-11-18, docs/spec_feed_feedback.md — 사용자 지시 "계속 analyist_dd를 피드백해서 개선시킬
수 있도록 할 수 있게 해줘"): 실계좌 체결 결과를 매일 자동으로 피드(스크리너·엣지 스코어·ML) 개선
입력으로 되돌리는 산출물이 없어서, 엣지 게이트가 실측 손익과 연결되는지 사람이 손으로만 확인했다.
이 스크립트는 저널을 읽어 (a) 스크리너/edge_score/진입 시각대/보유 분/edge_factors/stop.model
차원별 기대값·승률·비용 비중을 자동 집계하고, (b) edge_score 버킷 기대값이 갈리면
min_edge_score 제안값을 **제안 파일에만** 쓰고, (c) --register 시 부정 버킷을 개선 큐에
등록한다. **자동 주문 파라미터 변경은 하지 않는다** — 돈이 걸린 값(edge_params.json, 트레이더
config)은 사람 승인 절차.

  비목표: 주문 경로·게이트·파라미터 변경 없음(trader-agent/** 무수정), edge_params.json
  무수정(data/state/edge_params_suggested.json 만 쓴다), DB 쓰기 없음(minute_close_bars·
  market_data 는 SELECT 전용 — 과제 지시), 저널 쓰기 없음(file:...?mode=ro), 크론 등록 없음
  (등록은 오케스트레이터), 실행 시각 미기록(같은 입력 → 같은 바이트 = 멱등).

실측 근거 (2026-11-18, 아래 조회 명령으로 확인 — 수치를 이 문서만 믿지 말 것):
  # 저널 전체 행 — WSL drvfs+WAL 함정 회피: .sqlite3/-wal/-shm 을 /tmp 로 복사 후 mode=ro
  # (reentry_after_exit.py _journal_open 과 같은 방식. 직접 열면 disk I/O error 실측)
  #   python3 -c "import sqlite3,tempfile,shutil,os; \
  #     J='/mnt/c/Users/jhshi/analyist_dd/trader-agent/journal/trade_journal.sqlite3'; \
  #     t=tempfile.mkdtemp(); \
  #     [shutil.copyfile(J+s, os.path.join(t, os.path.basename(J)+s)) for s in ('','-wal','-shm') if os.path.exists(J+s)]; \
  #     c=sqlite3.connect('file:'+os.path.join(t, os.path.basename(J))+'?mode=ro', uri=True); \
  #     [print(r) for r in c.execute('select id,ts,code,screener,exit_ts,pnl,fees,exit_reason from trades order by id')]"
  #   -> trades 15행. 2026-11-18 청산 4건: id=12(033100, -3142.8원) id=13(096530, -2100.7원)
  #      id=14(021320, +29.0원, 30.2분) id=15(143240, -41.0원, 34.6분).
  #   -> 창(최근 14일, exit_ts >= 2026-11-04) 청산 11건(id 5~15). 그중 비포지션 3건:
  #      id=5 'reconcile: broker flat'(-5390원) · id=8 'unfilled: ...'(0원) ·
  #      id=9 'reconcile: broker flat'(0원) -> reentry_after_exit.py 관용구대로 제외,
  #      계측 대상 8건(id 6,7,10,11,12,13,14,15). 스펙 검증의 "4청산건(오늘)" = id 12~15 이고
  #      이 4건은 계측 대상에 포함된다.
  #   -> edge_score 는 id=14(5.0)·id=15(4.0)만 기록(단타 2건). edge_factors 는 전건 미기록 —
  #      진입 요인 기록 배선(커밋 d3c4938) 이후 진입부터 채워진다. stop.model 은 전건 'fixed'
  #      (id=7만 None — manual_exit 행).
  #   -> pnl 은 이미 비용 차감 후 순손익(reentry_after_exit.py 실측: id=11 -5185-146=-5331)
  #      -> 이 스크립트는 pnl 을 그대로 쓰고 fees 를 또 빼지 않는다.
  # Postgres 127.0.0.1:POSTGRES_HOST_PORT (SELECT 전용, 접속정보는 프로세스 환경변수):
  #   select count(*) from minute_close_bars where stock_code=%s and trade_date=%s
  #   -> 창 내 8건 중 분봉 보유는 096530(11-17) 375행뿐. 11-18 단타 2건(021320·143240)은
  #      분봉 0행(당일 미적재) -> '보유 중 최대 변동'은 null + 사유 no_minute_bars.
  #   select open_price,high_price,low_price,close_price from market_data where ...
  #   -> 021320·143240 의 11-18 행 없음(당일 미적재) -> '진입일 OHLC' null + 사유
  #      no_market_data. 나머지 6건은 진입일 OHLC 존재.
  # 현행 min_edge_score = 4: trader-agent/trader_core/config.py:246 `min_edge_score: int = 4`
  #   (조회: grep -n min_edge_score trader_core/config.py — 2026-11-18 기준). 리포 밖
  #   실주문 경로라 런타임 읽기 대신 기본값 4 + --current-edge-score 오버라이드.

사용:
  python3 scripts/feed_feedback.py [--days 14] [--min-n 10] [--json-out PATH] [--md-out PATH]
                                   [--suggested-out PATH] [--register] [--current-edge-score 4]
                                   [--journal PATH] [--quiet]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import proj  # noqa: E402

KST = timezone(timedelta(hours=9))
JOURNAL_DEFAULT = "/mnt/c/Users/jhshi/analyist_dd/trader-agent/journal/trade_journal.sqlite3"
JSON_OUT = proj("data", "reports", "feed_feedback.json")
MD_OUT = proj("data", "reports", "feed_feedback.md")
SUGGESTED_OUT = proj("data", "state", "edge_params_suggested.json")
IMPROVE_QUEUE_CLI = proj("scripts", "improve_queue.py")
EXCLUDE_PREFIXES = ("unfilled:", "reconcile: broker flat")
# edge_score 버킷(스펙 계약 0-2/3/4/5/6-7/None). 순서 = 출력 순서.
EDGE_BUCKETS = (("0-2", 0), ("3", 3), ("4", 4), ("5", 5), ("6-7", 6))
HOUR_LABELS = ("09-10", "10-11", "11-12", "12-13", "13-14", "14-15")
HOLD_LABELS = ("≤30", "31-60", "61-90", ">90")


# ── 공통 헬퍼 (reentry_after_exit.py 관용구 재사용) ───────────────────────────
def _parse_ts(s):
    """ISO8601(KST 오프셋) 문자열 -> aware datetime. 실패 시 None."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s))
        return dt if dt.tzinfo else dt.replace(tzinfo=KST)
    except ValueError:
        return None


def _fmt_num(x):
    """JSON 출력용: 정수면 int, 아니면 소수 2자리."""
    if x is None:
        return None
    f = float(x)
    return int(f) if f.is_integer() else round(f, 2)


def _atomic_write(path, text):
    """tmp 쓰기 + os.replace 원자적 교체 — 중간에 죽어도 산출물이 깨지지 않는다."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


# ── 소스 수신 (1/3) — 저널 읽기 ────────────────────────────────────────────────
def _journal_open(journal):
    """저널을 /tmp 로 복사해 읽기 전용으로 연다. (conn, tmpdir) 또는 (None, 사유).

    WHY: /mnt/c 는 drvfs 고 저널은 WAL 모드라 직접 mode=ro 로 열면 disk I/O error 가
    나거나 -wal 에만 있는 최신 행이 안 보인다(실측 2026-09-28, trader_cycle.py 와 동일).
    원본은 shutil.copyfile(읽기)로만 건드리고 복사본을 file:...?mode=ro 로 연다.
    """
    if not os.path.exists(journal):
        return None, "journal 없음: %s" % journal
    tmp = tempfile.mkdtemp(prefix="feedfb_journal_")
    try:
        for suffix in ("", "-wal", "-shm"):
            src = journal + suffix
            if os.path.exists(src):
                shutil.copyfile(src, os.path.join(tmp, os.path.basename(journal) + suffix))
        dst = os.path.join(tmp, os.path.basename(journal))
        conn = sqlite3.connect("file:%s?mode=ro" % dst, uri=True, timeout=10)
        return conn, tmp
    except Exception as exc:  # noqa: BLE001 - 사이클은 어떤 경우에도 죽지 않는다
        shutil.rmtree(tmp, ignore_errors=True)
        return None, "journal 복사/열기 실패: %s" % exc


def _load_trades(conn):
    """trades 전체를 읽어 ts/exit_ts 를 aware datetime 으로 파싱해 돌려준다."""
    cur = conn.execute(
        "select id, ts, side, code, name, qty, price, order_type, order_resp_json, "
        "entry_reason, screener, hwm_price, algo_json, stop_pct, tp_pct, kelly_f, "
        "exit_ts, exit_price, exit_reason, pnl, fees from trades order by id"
    )
    cols = [d[0] for d in cur.description]
    rows = []
    for r in cur.fetchall():
        d = dict(zip(cols, r))
        d["ts_dt"] = _parse_ts(d.get("ts"))
        d["exit_dt"] = _parse_ts(d.get("exit_ts"))
        rows.append(d)
    return rows


# ── 소스 수신 (1/3) — Postgres SELECT 전용 보강 ────────────────────────────────
def _db_ctx_loader(trades):
    """minute_close_bars(보유 중 분봉)·market_data(진입일 OHLC) 를 SELECT 로 읽는다.

    WHY: 스펙 입력 목록. 둘 다 없어도 계측은 계속된다 — 결측은 null + 사유(0으로 메우지
    않는다). DB 오류도 fail-open(예외를 삼키고 {"error": ...} 반환).
    """
    ctx = {"market": {}, "minute": {}, "error": None}
    try:
        import psycopg2  # 지연 import — 드라이버 없는 환경에서도 모듈 import 는 성공해야 한다
        from dq_claim import _conn_target  # noqa: PLC0415 - 컨테이너 좌표 되돌림 관용구 재사용

        host, port = _conn_target()
        conn = psycopg2.connect(
            host=host, port=port,
            dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
            user=os.environ.get("POSTGRES_USER", "stock_user"),
            password=os.environ.get("POSTGRES_PASSWORD", ""),
            connect_timeout=10,
        )
        conn.autocommit = True
        cur = conn.cursor()
        try:
            for t in trades:
                code, entry_day = t["code"], t["ts_dt"].date()
                if (code, entry_day) not in ctx["market"]:
                    cur.execute(
                        "select open_price, high_price, low_price, close_price "
                        "from market_data where stock_code=%s and trade_date=%s",
                        (code, entry_day),
                    )
                    r = cur.fetchone()
                    ctx["market"][(code, entry_day)] = (
                        {"open": float(r[0]), "high": float(r[1]),
                         "low": float(r[2]), "close": float(r[3])} if r else None)
                cur.execute(
                    "select trade_date, time, close_price from minute_close_bars "
                    "where stock_code=%s and trade_date between %s and %s "
                    "order by trade_date, time",
                    (code, entry_day, t["exit_dt"].date()),
                )
                bars = []
                for d, tm, px in cur.fetchall():
                    tm = str(tm).zfill(4)
                    bts = datetime(d.year, d.month, d.day, int(tm[:2]), int(tm[2:4]),
                                   tzinfo=KST)
                    if t["ts_dt"] <= bts <= t["exit_dt"]:
                        bars.append((bts, float(px)))
                ctx["minute"][t["id"]] = bars
        finally:
            cur.close()
            conn.close()
    except Exception as exc:  # noqa: BLE001 - 보강 실패는 계측을 깨지 않는다
        ctx["error"] = "%s: %s" % (type(exc).__name__, exc)
    return ctx


# ── 파서 생성 (2/3) — 창 필터·보강·차원 분해 ───────────────────────────────────
def _in_window(rows, days, now):
    """창(최근 days 일, exit_ts 기준) 안의 청산 거래. (included, basis)

    비포지션 행('unfilled:'/'reconcile: broker flat' 시작 exit_reason)은 제외한다 —
    reentry_after_exit.py 관용구와 동일(실측: id=5,8,9). pnl 이 None 인 청산 행도
    측정 불가이므로 제외(결측을 0으로 메우지 않는다).
    """
    cutoff = now - timedelta(days=days)
    exits = [r for r in rows if r.get("exit_dt") is not None and r["exit_dt"] >= cutoff]
    exits.sort(key=lambda r: r["exit_dt"])
    included, excluded = [], []
    for r in exits:
        reason = r.get("exit_reason") or ""
        if reason.startswith(EXCLUDE_PREFIXES):
            excluded.append({"id": r["id"], "exit_ts": r["exit_ts"], "reason": "non_position",
                             "exit_reason": reason})
        elif r.get("pnl") is None:
            excluded.append({"id": r["id"], "exit_ts": r["exit_ts"], "reason": "pnl_missing",
                             "exit_reason": reason})
        else:
            included.append(r)
    basis = {"window_exits": len(exits), "excluded": excluded,
             "cutoff": cutoff.isoformat(timespec="seconds")}
    return included, basis


def _enrich(t, ctx):
    """저널 행 + DB 보강 -> 계측용 거래 dict. 측정 불가 값은 null + 사유."""
    algo = {}
    try:
        algo = json.loads(t.get("algo_json") or "{}")
    except (ValueError, TypeError):
        pass
    cand = algo.get("candidate") or {}
    price = float(t["price"]) if t.get("price") is not None else None
    qty = float(t["qty"]) if t.get("qty") is not None else None
    notional = round(qty * price) if price is not None and qty is not None else None
    pnl = float(t["pnl"])
    fees = float(t.get("fees") or 0)
    hold_min = round((t["exit_dt"] - t["ts_dt"]).total_seconds() / 60.0, 1)
    hour = t["ts_dt"].hour if 9 <= t["ts_dt"].hour <= 14 else None

    entry_day = t["ts_dt"].date()
    ohlc, ohlc_reason = ctx["market"].get((t["code"], entry_day)), None
    if ohlc is None:
        ohlc_reason = "no_market_data" if ctx["error"] is None else "db_error"
    bars = ctx["minute"].get(t["id"], [])
    exit_px = float(t["exit_price"]) if t.get("exit_price") is not None else price
    max_move = None
    if bars:
        prices = [px for _, px in bars] + [price, exit_px]
        max_move = {"max_up_pct": _fmt_num((max(prices) - price) / price * 100),
                    "max_down_pct": _fmt_num((min(prices) - price) / price * 100),
                    "n_bars": len(bars)}

    edge_score = cand.get("edge_score")
    if not isinstance(edge_score, (int, float)) or isinstance(edge_score, bool):
        edge_score = None
    return {
        "id": t["id"], "code": t["code"], "ts": t["ts"], "exit_ts": t["exit_ts"],
        "screener": t.get("screener") or "none",
        "pnl": pnl, "fees": fees, "notional": notional,
        "pct": _fmt_num(pnl / notional * 100) if notional else None,
        "hold_min": hold_min, "hour": hour,
        "edge_score": edge_score, "edge_factors": cand.get("edge_factors"),
        "stop_model": (algo.get("stop") or {}).get("model"),
        "ohlc": ohlc, "ohlc_reason": ohlc_reason, "max_move": max_move,
        "exit_reason": t.get("exit_reason"),
    }


def _stats(trades):
    """버킷 통계(스펙 계약 8필드). 표본 없는 평균은 null(0으로 메우지 않는다)."""
    n = len(trades)
    pnls = [x["pnl"] for x in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_abs = sum(abs(p + x["fees"]) for p, x in zip(pnls, trades))
    fees_sum = sum(x["fees"] for x in trades)
    return {
        "n": n,
        "win_pct": _fmt_num(len(wins) / n * 100) if n else None,
        "expectancy_krw": _fmt_num(sum(pnls) / n) if n else None,
        "expectancy_pct": _fmt_num(sum(x["pct"] for x in trades if x["pct"] is not None) / n)
                          if n else None,
        "avg_win": _fmt_num(sum(wins) / len(wins)) if wins else None,
        "avg_loss": _fmt_num(sum(losses) / len(losses)) if losses else None,
        "fee_share_pct": _fmt_num(fees_sum / gross_abs * 100) if gross_abs else None,
        "avg_hold_min": _fmt_num(sum(x["hold_min"] for x in trades) / n) if n else None,
    }


def _edge_bucket(score):
    """edge_score -> 스펙 버킷(0-2/3/4/5/6-7/None). 범위 밖·비수치는 None(미측정)."""
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return "None"
    s = float(score)
    if s < 0 or s > 7:
        return "None"
    for label, lo in EDGE_BUCKETS:
        if s == lo or (label == "0-2" and lo <= s <= 2) or (label == "6-7" and s >= 6):
            return label
    return "None"


def _hold_bucket(hold_min):
    """보유 분 -> ≤30/31-60/61-90/>90 (경계 30/60/90)."""
    if hold_min <= 30:
        return "≤30"
    if hold_min <= 60:
        return "31-60"
    if hold_min <= 90:
        return "61-90"
    return ">90"


def _hour_bucket(hour):
    """진입 시각(hour, KST) -> 09-10..14-15. 범위 밖은 None(제외)."""
    if hour is None or not 9 <= hour <= 14:
        return None
    return HOUR_LABELS[hour - 9]


def _grouped(trades, keyfn):
    """(bucket_label, min_score_or_None, trades) 목록. 정렬: 호출부가 정한다."""
    groups = {}
    for t in trades:
        groups.setdefault(keyfn(t), []).append(t)
    return groups


def _by_screener(trades):
    return [{"screener": k, **_stats(v)}
            for k, v in sorted(_grouped(trades, lambda t: t["screener"]).items())]


def _by_edge_score(trades):
    out = []
    for label, lo in EDGE_BUCKETS:
        v = [t for t in trades if _edge_bucket(t["edge_score"]) == label]
        if v:
            out.append({"bucket": label, "min_score": lo, **_stats(v)})
    v = [t for t in trades if _edge_bucket(t["edge_score"]) == "None"]
    if v:
        out.append({"bucket": "None", "min_score": None, **_stats(v)})
    return out


def _by_hour(trades):
    groups = {k: v for k, v in _grouped(trades, lambda t: _hour_bucket(t["hour"])).items()
              if k is not None}
    return [{"hour": k, **_stats(v)} for k, v in sorted(groups.items())]


def _by_hold(trades):
    order = {label: i for i, label in enumerate(HOLD_LABELS)}
    return [{"hold": k, **_stats(v)}
            for k, v in sorted(_grouped(trades, lambda t: _hold_bucket(t["hold_min"])).items(),
                               key=lambda kv: order[kv[0]])]


def _by_stop_model(trades):
    return [{"stop_model": k, **_stats(v)}
            for k, v in sorted(_grouped(trades,
                                        lambda t: t["stop_model"] or "None").items())]


def _by_factor(trades):
    """edge_factors 개별 요인의 on/off 기대값 차이(있으면).

    요인 dict 가 없는 거래는 해당 요인의 on/off 어느 쪽에도 세지 않는다(결측을 0으로
    메우지 않는다). 한쪽 표본이 0이면 그쪽은 null + 사유.
    """
    factors = sorted({f for t in trades
                      for f in (t["edge_factors"] or {}).keys()
                      if isinstance(t.get("edge_factors"), dict)})
    out = []
    for f in factors:
        on = [t for t in trades if isinstance(t.get("edge_factors"), dict)
              and bool(t["edge_factors"].get(f))]
        off = [t for t in trades if isinstance(t.get("edge_factors"), dict)
               and f in t["edge_factors"] and not bool(t["edge_factors"][f])]
        entry = {"factor": f,
                 "on": _stats(on) if on else None,
                 "off": _stats(off) if off else None,
                 "delta_krw": None, "delta_pct": None, "reason": None}
        if on and off:
            entry["delta_krw"] = _fmt_num(entry["on"]["expectancy_krw"]
                                          - entry["off"]["expectancy_krw"])
            entry["delta_pct"] = _fmt_num(entry["on"]["expectancy_pct"]
                                          - entry["off"]["expectancy_pct"])
        else:
            entry["reason"] = "on/off 표본 부족: on=%d off=%d" % (len(on), len(off))
        out.append(entry)
    return out


# ── 파서 생성 (2/3) — 제안·큐 등록 규칙(고정, 결정적) ──────────────────────────
def _suggest(edge_buckets, min_n, current):
    """제안 규칙(고정): n≥min_n 버킷 중 기대값 최대 버킷의 최소 점수를 min_edge_score
    제안값으로 낸다. 동률/최고≤0/현행과 동일/표본부족이면 null + 사유(동률이면 현행 유지).

    "유의하게 갈리면"의 구현: 동률이 아니고(최고가 유일) 최고 기대값 > 0. 추가 통계검정은
    하지 않는다 — 소표본에서 분산 추정은 과적합을 부른다.
    """
    eligible = [b for b in edge_buckets
                if b["min_score"] is not None and b["n"] >= min_n]
    if not eligible:
        return None, ("표본 부족: n>=%d 버킷 없음 (버킷별 n: %s)"
                      % (min_n, ", ".join("%s:%d" % (b["bucket"], b["n"])
                                          for b in edge_buckets))), None
    best = max(eligible, key=lambda b: b["expectancy_krw"])
    if best["expectancy_krw"] <= 0:
        return None, "최고 기대값도 음수(비용 포함) — 문턱 상향 제안 없음", best["n"]
    ties = [b for b in eligible if b["expectancy_krw"] == best["expectancy_krw"]]
    if len(ties) > 1:
        return None, "기대값 동률 — 현행 유지", best["n"]
    if best["min_score"] == current:
        return None, "최적 버킷이 현행 문턱(%d)과 같음 — 유지" % current, best["n"]
    return best["min_score"], ("기대값 최대 버킷 %s (n=%d, 기대값 %s원/건) — "
                               "그 최소 점수 %d 를 제안"
                               % (best["bucket"], best["n"],
                                  _fmt_num(best["expectancy_krw"]), best["min_score"])), best["n"]


def _negative_buckets(dims, min_n):
    """부정 버킷(n≥min_n, 기대값 < 0, 비용 포함) -> 등록 후보 목록.

    등록 차원 = screener·edge_score·hour·hold 4차원. stop.model·요인 델타는 제외 —
    문턱/진입 규칙에 직결되지 않는다. 제목은 n 을 넣지 않아 **날짜가 지나도 안정적**이다
    (같은 버킷이 계속 음수여도 큐에 1건 — 스펙의 멱등 계약).
    """
    out = []
    for dim, key, label, st in dims:
        if st["n"] >= min_n and st["expectancy_krw"] < 0:
            out.append({
                "title": "feed_feedback 부정 버킷: %s=%s" % (dim, label),
                "why": ("feed_feedback 측정: %s 버킷 %s n=%d 기대값 %s원/건 승률 %s%% "
                        "(비용 포함 음수). 자동 등록."
                        % (dim, label, st["n"], _fmt_num(st["expectancy_krw"]),
                           _fmt_num(st["win_pct"]))),
                "acceptance": "사람 승인 후 해당 버킷 진입 제한·파라미터 변경 + trader 테스트 통과",
                "priority": 2,
            })
    return out


def _queue_path():
    return os.environ.get("IMPROVE_QUEUE") or proj("data", "state", "improve_queue.json")


def _queue_load():
    try:
        with open(_queue_path(), encoding="utf-8") as f:
            d = json.load(f)
        return d.get("tasks") if isinstance(d, dict) else []
    except (OSError, ValueError):
        return []


def _register(negs):
    """부정 버킷을 improve_queue CLI 로 등록(멱등: 같은 제목이 큐에 있으면 건너뛴다).

    스펙 계약 그대로 `python3 scripts/improve_queue.py add --title ... --why ...
    --acceptance ... --priority <int>` 를 서브프로세스로 호출한다(IMPROVE_QUEUE 환경변수
    상속 — 테스트·격리 가능). 같은 제목이 **어떤 상태로든** 큐에 있으면 건너뛴다(재등록
    부활 금지). CLI 실패는 계측을 깨지 않는다(결과에 error 로 남긴다).
    """
    results = []
    for neg in negs:
        if any((t.get("title") or "").strip() == neg["title"].strip() for t in _queue_load()):
            results.append({"title": neg["title"], "priority": neg["priority"],
                            "status": "exists"})
            continue
        cmd = [sys.executable, IMPROVE_QUEUE_CLI, "add",
               "--title", neg["title"], "--why", neg["why"],
               "--acceptance", neg["acceptance"],
               "--priority", str(int(neg["priority"]))]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                                  env=os.environ.copy())
            if proc.returncode != 0:
                results.append({"title": neg["title"], "priority": neg["priority"],
                                "status": "error",
                                "err": (proc.stderr or proc.stdout or "").strip()[:200]})
            else:
                tid = (json.loads(proc.stdout) or {}).get("id")
                results.append({"title": neg["title"], "priority": neg["priority"],
                                "status": "added", "id": tid})
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            results.append({"title": neg["title"], "priority": neg["priority"],
                            "status": "error", "err": "%s: %s" % (type(exc).__name__, exc)})
    return results


# ── 실제 저장 (3/3) — JSON/MD/제안 파일(전부 멱등: 실행 시각 미기록) ─────────────
def _md(report, args):
    """결정적 마크다운(같은 입력 → 같은 바이트). 실행 시각 없음."""
    b = report["basis"]
    ov = report["overall"]
    c = report["cost"]
    lines = [
        "# 피드 피드백 — 실매매 → analyist_dd (자동 측정)",
        "",
        "docs/spec_feed_feedback.md 계약 산출물. **자동 파라미터 변경 없음** — 측정·제안·백로그 등록만.",
        "",
        "## 요약",
        "",
        "- 창: 최근 %d일 (%s 이후 청산) · 계측 %d건 (창 내 청산 %d − 제외 %d)"
        % (args.days, b["cutoff"], report["trades"], b["window_exits"], len(b["excluded"])),
        "- 전체: n=%s 승률=%s%% 기대값=%s원/건 기대값%%=%s 평균보유=%s분"
        % (ov["n"], ov["win_pct"], ov["expectancy_krw"], ov["expectancy_pct"],
           ov["avg_hold_min"]),
        "- 비용: 평균 거래금액=%s원 수수료 비중=%s%%"
        % (c["avg_trade_krw"], c["fee_share_pct"]),
        "",
    ]
    if b["excluded"]:
        lines += ["- 제외(비포지션·측정 불가): %s"
                  % ", ".join("id=%s(%s)" % (e["id"], e["reason"]) for e in b["excluded"]),
                  ""]

    def _table(title, rows, head):
        lines = ["### " + title, "",
                 "| " + " | ".join(head) + " |",
                 "|" + "|".join(["---"] * len(head)) + "|"]
        for r in rows:
            lines.append("| " + " | ".join(str(r[h]) if r[h] is not None else "null"
                                           for h in head) + " |")
        lines.append("")
        return lines

    stat_head = ("n", "win_pct", "expectancy_krw", "expectancy_pct", "avg_win",
                 "avg_loss", "fee_share_pct", "avg_hold_min")
    lines += _table("screener",
                    [{"screener": x["screener"], **x} for x in report["by_screener"]],
                    ("screener",) + stat_head)
    lines += _table("edge_score (0-2/3/4/5/6-7/None)",
                    [{"bucket": x["bucket"], **x} for x in report["by_edge_score"]],
                    ("bucket",) + stat_head)
    lines += _table("진입 시각대",
                    [{"hour": x["hour"], **x} for x in report["by_hour"]],
                    ("hour",) + stat_head)
    lines += _table("보유 분 (≤30/31-60/61-90/>90)",
                    [{"hold": x["hold"], **x} for x in report["by_hold"]],
                    ("hold",) + stat_head)
    lines += _table("stop.model",
                    [{"stop_model": x["stop_model"], **x} for x in report["by_stop_model"]],
                    ("stop_model",) + stat_head)

    lines += ["### edge_factors (on/off 기대값 차이)", ""]
    if report["by_factor"]:
        rows = [{"factor": x["factor"], "on_n": x["on"]["n"] if x["on"] else None,
                 "on_exp": x["on"]["expectancy_krw"] if x["on"] else None,
                 "off_n": x["off"]["n"] if x["off"] else None,
                 "off_exp": x["off"]["expectancy_krw"] if x["off"] else None,
                 "delta_krw": x["delta_krw"], "delta_pct": x["delta_pct"],
                 "reason": x["reason"]} for x in report["by_factor"]]
        lines += ["| factor | on_n | on 기대값 | off_n | off 기대값 | delta_krw | delta_pct | reason |",
                  "|---|---|---|---|---|---|---|---|"]
        for r in rows:
            lines.append("| %s | %s | %s | %s | %s | %s | %s | %s |"
                         % tuple("null" if v is None else v for v in r.values()))
    else:
        lines += ["- 없음 — 창 내 거래 전부 edge_factors 미기록. 진입 요인 기록 배선(커밋 "
                  "d3c4938) 이후 진입부터 채워진다.", ""]

    lines += ["## 제안 (min_edge_score)", ""]
    if report["suggestions"]:
        for s in report["suggestions"]:
            lines += ["- 현행 %s → 제안 %s (n=%s): %s"
                      % (s["current"], s["suggested"], s["n"], s["why"])]
    else:
        lines += ["- 제안 없음: %s" % report["suggest_reason"]]
    lines += [""]

    lines += ["## 큐 등록 (%s)" % ("--register" if args.register else "dry — --register 없음"), ""]
    if report["queued"]:
        for q in report["queued"]:
            lines += ["- [%s] %s (priority=%s)"
                      % (q["status"], q["title"], q["priority"])]
    else:
        lines += ["- 등록 없음 (부정 버킷 없음 또는 n < min-n)"]
    lines += [""]

    lines += ["## 거래별 상세", "",
              "| id | 코드 | 진입 | 청산 | screener | pnl(원) | 수수료 | 보유(분) | edge_score | "
              "stop.model | 진입일 OHLC | 보유 중 최대 변동 |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for t in sorted(report["detail"], key=lambda x: x["id"]):
        ohlc = ("%s/%s/%s/%s" % (t["ohlc"]["open"], t["ohlc"]["high"], t["ohlc"]["low"],
                                 t["ohlc"]["close"])) if t["ohlc"] else t["ohlc_reason"]
        mv = ("+%s%%/%s%%" % (t["max_move"]["max_up_pct"], t["max_move"]["max_down_pct"])
              if t["max_move"] else "no_minute_bars")
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |"
                     % (t["id"], t["code"], t["ts"][:16], t["exit_ts"][:16], t["screener"],
                        _fmt_num(t["pnl"]), _fmt_num(t["fees"]), t["hold_min"],
                        t["edge_score"], t["stop_model"] or "null", ohlc, mv))
    lines += [""]

    lines += ["## 결측 사유 (0으로 메우지 않음)", ""]
    if report["notes"]:
        lines += ["- %s" % n for n in report["notes"]]
    else:
        lines += ["- 없음"]
    lines += [""]
    return "\n".join(lines)


def _persist(report, args):
    """실제 저장: feed_feedback.json/.md + edge_params_suggested.json (원자적 교체).

    세 파일 모두 실행 시각 미기록 — 같은 입력이면 같은 바이트(재실행 안전 = 멱등).
    """
    _atomic_write(args.json_out, json.dumps(report["json"], ensure_ascii=False, indent=2) + "\n")
    _atomic_write(args.md_out, report["md"])

    sug = {"suggested": report["suggested"], "reason": report["suggest_reason"],
           "current": args.current_edge_score,
           "suggestions": report["suggestions"],
           "source": os.path.relpath(args.json_out, proj())}
    _atomic_write(args.suggested_out, json.dumps(sug, ensure_ascii=False, indent=2) + "\n")


def run(args, now=None, ctx_loader=None):
    """계측 파이프라인 전체. (report_dict, summary_line). 실패는 예외로 올린다.

    now/ctx_loader 는 테스트 주입용(창 경계 고정·DB 격리). 기본은 KST 현재 시각과
    Postgres SELECT 보강.
    """
    now = now or datetime.now(KST)
    if not os.path.exists(args.journal):
        return None, "저널이 없습니다: %s" % args.journal
    conn, tmp = _journal_open(args.journal)
    if conn is None:
        return None, "저널을 열 수 없습니다: %s (%s)" % (args.journal, tmp)
    try:
        rows = _load_trades(conn)  # 소스 수신 (1/3)
    finally:
        conn.close()
        shutil.rmtree(tmp, ignore_errors=True)

    included, basis = _in_window(rows, args.days, now)
    ctx = (ctx_loader or _db_ctx_loader)(included)
    trades = [_enrich(t, ctx) for t in included]  # 파서 생성 (2/3)

    by_edge_score = _by_edge_score(trades)
    suggested, why, best_n = _suggest(by_edge_score, args.min_n, args.current_edge_score)
    suggestions = ([{"param": "min_edge_score", "current": args.current_edge_score,
                     "suggested": suggested, "why": why, "n": best_n}]
                   if suggested is not None else [])

    dims = ([("screener", "screener", x["screener"], x) for x in _by_screener(trades)]
            + [("edge_score", "edge_score", x["bucket"], x) for x in by_edge_score]
            + [("hour", "hour", x["hour"], x) for x in _by_hour(trades)]
            + [("hold", "hold", x["hold"], x) for x in _by_hold(trades)])
    queued = _register(_negative_buckets(dims, args.min_n)) if args.register else []

    notes = []
    if ctx.get("error"):
        notes.append("DB 보강 실패(%s) — 보유 중 분봉·진입일 OHLC 는 null 로 남는다" % ctx["error"])
    if not any(t.get("edge_factors") for t in trades):
        notes.append("edge_factors 미기록: 창 내 %d건 모두 candidate.edge_factors 없음 — "
                     "진입 요인 기록 배선(d3c4938) 이후 진입부터 채워진다 (실측 2026-11-18)"
                     % len(trades))
    n_bars = sum(1 for t in trades if t["max_move"])
    if n_bars < len(trades):
        notes.append("보유 중 최대 변동: 분봉 미적재로 %d/%d건 null(no_minute_bars)"
                     % (len(trades) - n_bars, len(trades)))
    n_ohlc = sum(1 for t in trades if t["ohlc"])
    if n_ohlc < len(trades):
        notes.append("진입일 OHLC: %d/%d건 null(%s)"
                     % (len(trades) - n_ohlc, len(trades),
                        "no_market_data" if ctx["error"] is None else "db_error"))
    n_hour_out = sum(1 for t in trades if t["hour"] is None)
    if n_hour_out:
        notes.append("진입 시각이 09:00–15:00 밖인 %d건은 by_hour 에서 제외(스펙 버킷 밖)" % n_hour_out)

    overall = _stats(trades)
    cost = {"fee_share_pct": overall["fee_share_pct"],
            "avg_trade_krw": _fmt_num(sum(t["notional"] for t in trades
                                          if t["notional"]) / len(trades))}
    j = {"days": args.days, "trades": len(trades), "overall": overall,
         "by_screener": _by_screener(trades), "by_edge_score": by_edge_score,
         "by_hour": _by_hour(trades), "by_hold": _by_hold(trades),
         "by_stop_model": _by_stop_model(trades), "by_factor": _by_factor(trades),
         "cost": cost, "suggestions": suggestions, "queued": queued,
         "basis": {"window_exits": basis["window_exits"], "excluded": basis["excluded"],
                   "cutoff": basis["cutoff"]},
         "claim": {"runner": "feed_feedback", "source_rows": len(rows),
                   "claimed_rows": len(trades), "persisted_rows": len(trades),
                   "dq": "not_called: DB 127.0.0.1:5434 는 SELECT 전용(과제 지시) — "
                         "dq_runner_claim INSERT 를 하지 않고 소스/파서/저장 3값을 여기 남긴다"},
         "notes": notes}

    report = {"json": j,
              "md": _md({**j, "detail": trades, "suggested": suggested,
                         "suggest_reason": why}, args),
              "trades": len(trades), "overall": overall, "cost": cost,
              "suggestions": suggestions, "suggested": suggested,
              "suggest_reason": why, "queued": queued,
              "basis": basis, "notes": notes}
    _persist(report, args)  # 실제 저장 (3/3)

    summary = ("feed_feedback days=%d trades=%d expectancy_krw=%s win_pct=%s "
               "suggested=%s queued=%d"
               % (args.days, len(trades), overall["expectancy_krw"], overall["win_pct"],
                  suggested, len(queued)))
    return report, summary


def main(argv=None):
    ap = argparse.ArgumentParser(description="실매매 → 피드 피드백 계측(읽기 전용 + 제안 파일)")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--min-n", type=int, default=10)
    ap.add_argument("--json-out", default=JSON_OUT)
    ap.add_argument("--md-out", default=MD_OUT)
    ap.add_argument("--suggested-out", default=SUGGESTED_OUT)
    ap.add_argument("--register", action="store_true",
                    help="부정 버킷을 improve_queue 에 등록(기본 dry)")
    ap.add_argument("--current-edge-score", type=int, default=4,
                    help="현행 min_edge_score (trader_core/config.py:246, 기본 4)")
    ap.add_argument("--journal",
                    default=os.environ.get("TRADER_JOURNAL") or JOURNAL_DEFAULT)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    report, summary = run(args)
    if report is None:
        print(summary, file=sys.stderr)
        return 1
    if not args.quiet:
        print(summary)
        if report["suggest_reason"]:
            print("제안 없음 사유: %s" % report["suggest_reason"])
        for q in report["queued"]:
            print("[queue] %s %s" % (q["status"], q["title"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

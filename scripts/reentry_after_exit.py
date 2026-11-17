#!/usr/bin/env python3
"""청산 후 재투입 계측 — docs/spec_reentry_after_exit.md 계약 구현 (읽기 전용).

WHY (2026-11-17, data/state/ceo_priority.json day=2026-11-16 1순위 레버):
  "청산이 노출을 줄이지 않는다" — 440110 스톱(-5,331원) 3초 뒤 033100 2주 74,400원 재진입으로
  노출이 59.0%→68.72% 로 늘었다. 청산 1건과 그 직후 창(기본 10분) 안의 신규 진입을 한 거래
  단위로 묶는 계측 산출물이 없어 이 인과가 사람 손으로만 확인됐다. 이 스크립트는 저널만 읽어
  (a) 창 내 신규 진입 건수·금액(수량×체결가), (b) 그 진입이 쓴 피드 날짜, (c) 두 건 합산
  순손익(비용 포함)을 자동 산출하고, 세션 누적 순손익이 음(-)인지에 따라 판정을 원장 한 줄로
  출력한다(스펙 expectation: 11-18 까지 원장 한 줄 자동 출력).

  비목표: 주문 경로·한도·게이트·전략 파라미터 변경 없음(trader-agent/** 무수정), 재투입
  차단 없음(차단은 사람 승인), 저널 쓰기 없음(file:...?mode=ro 로만 연다), 통계적 유의성
  주장 없음(표본 3건 미만이면 판정 문구를 "표본 부족"으로 고정).

실측 근거 (2026-11-17, 아래 조회 명령으로 확인 — 수치를 이 문서만 믿지 말 것):
  # 저널 전체 행 — WSL drvfs+WAL 함정 회피: .sqlite3/-wal/-shm 을 /tmp 로 복사 후 mode=ro
  # (trader_cycle.py _journal_copy 와 같은 방식. 직접 열면 disk I/O error 실측)
  #   python3 -c "import sqlite3,tempfile,shutil,os; \
  #     J='/mnt/c/Users/jhshi/analyist_dd/trader-agent/journal/trade_journal.sqlite3'; \
  #     t=tempfile.mkdtemp(); \
  #     [shutil.copyfile(J+s, os.path.join(t, os.path.basename(J)+s)) for s in ('','-wal','-shm') if os.path.exists(J+s)]; \
  #     c=sqlite3.connect('file:'+os.path.join(t, os.path.basename(J))+'?mode=ro', uri=True); \
  #     [print(r) for r in c.execute('select id,ts,side,code,name,qty,price,exit_ts,exit_price,exit_reason,pnl,fees,screener from trades order by id')]"
  #   -> trades 13행 전부 side='buy'. 포지션 1개 = buy 행 1개, 청산되면 같은 행의
  #      exit_ts/exit_price/exit_reason/pnl 이 채워진다.
  # - pnl 은 이미 비용 차감 후 순손익: id=11 (34050-36642.5)*2 = -5185, -5185-146(fees) = -5331 = pnl
  #   -> 순손익 = pnl (fees 를 또 빼지 않는다).
  # - 기준선(2026-11-17 시점):
  #     id=11 440110 청산 2026-11-16T10:05:27.094281+09:00 (pnl -5331, fees 146)
  #       -> id=12 033100 진입 2026-11-16T10:05:30.947190+09:00, 2주×37200 = 74,400, 간격 3.9초
  #     id=10 290720 청산 2026-11-17T09:04:31.661733+09:00 (pnl -6831)
  #       -> id=13 096530 진입 2026-11-17T09:05:17.389720+09:00, 3주×24103.3333 = 72,310, 간격 45.7초
  #   (스펙 기준선의 "3주×24200=72,600" 은 order_resp_json 의 limit 주문가 기준이다. 저널 price
  #    컬럼은 체결 VWAP 24103.3333 이고 계약상 금액=수량×체결가 이므로 본 스크립트는 72,310 을 쓴다.)
  # - 피드 날짜(feed_generated_at)는 저널 decisions/algo_json 어디에도 없다(실측 2026-11-17).
  #   -> 로컬 스냅샷(data/feed/**/screener_latest_*.json) 또는 feed_asof_history.jsonl 관측으로만
  #      해소하고, 없으면 null + feed_date_source="unrecorded". 현재 피드값을 과거 진입에 소급
  #      대입하지 않는다(관측 시점 = generated_at, 그보다 이전 진입에는 결코 매칭되지 않음).
  # - exit_reason 이 'unfilled:'/'reconcile: broker flat' 로 시작하는 행은 포지션이 아니었으므로
  #   제외. 실측 6건: id 1,2,3,5,9(reconcile: broker flat) + id 8(unfilled: ...).
  # - --days 60(기본) 창 안 청산 11건 -> 제외 6건 -> pair 5건(2026-10-08, 11-10, 11-11, 11-16,
  #   11-17). 판정 규칙(고정)상 전체 pair ≥3 이고 음수 세션이 있으므로 verdict 는
  #   "재투입 킬 후보"가 나온다(스펙 예시 줄의 verdict=표본 부족 은 형식 예시이며 실제 값은
  #   데이터가 결정한다 — 실측 5쌍 전 세션 음수).
  # 피드 현재 스냅샷:
  #   python3 -c "import json; print(json.load(open('data/feed/screener_latest.json'))['generated_at'])"
  #   -> 2026-11-17T09:25:36+09:00 (매 실행 시 feed_asof_history.jsonl 에 중복 없이 append = 멱등)

사용:
  python3 scripts/reentry_after_exit.py [--journal PATH] [--window-min 10] [--days N]
                                        [--json-out PATH] [--jsonl-out PATH] [--quiet]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import proj  # noqa: E402

KST = timezone(timedelta(hours=9))
JOURNAL_DEFAULT = "/mnt/c/Users/jhshi/analyist_dd/trader-agent/journal/trade_journal.sqlite3"
FEED_CURRENT = proj("data", "feed", "screener_latest.json")
FEED_ASOF = proj("data", "reports", "feed_asof_history.jsonl")
JSONL_OUT = proj("data", "reports", "reentry_after_exit.jsonl")
JSON_OUT = proj("data", "reports", "reentry_after_exit.json")
EXCLUDE_PREFIXES = ("unfilled:", "reconcile: broker flat")


# ── 공통 헬퍼 ────────────────────────────────────────────────────────────────
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
    """JSON/원장 출력용: 정수면 int, 아니면 소수 2자리."""
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


# ── 소스 수신 (1/3) — 저널 읽기 ──────────────────────────────────────────────
def _journal_open(journal):
    """저널을 /tmp 로 복사해 읽기 전용으로 연다. (conn, tmpdir) 또는 (None, 사유).

    WHY: /mnt/c 는 drvfs 고 저널은 WAL 모드라 직접 mode=ro 로 열면 disk I/O error 가
    나거나 -wal 에만 있는 최신 행이 안 보인다(실측 2026-09-28, trader_cycle.py 와 동일).
    원본은 shutil.copyfile(읽기)로만 건드리고 복사본을 file:...?mode=ro 로 연다.
    """
    if not os.path.exists(journal):
        return None, "journal 없음: %s" % journal
    tmp = tempfile.mkdtemp(prefix="reentry_journal_")
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


# ── 피드 날짜 해소 (스펙 규칙 1~3) ────────────────────────────────────────────
def _feed_asof_records():
    """feed_asof_history.jsonl 의 기존 관측 목록(깨진 줄은 무시)."""
    if not os.path.exists(FEED_ASOF):
        return []
    recs = []
    with open(FEED_ASOF, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                recs.append(json.loads(line))
            except ValueError:
                continue
    return recs


def _record_feed_asof():
    """규칙 3: 현재 피드의 generated_at 을 feed_asof_history.jsonl 에 기록(멱등).

    같은 generated_at 은 중복 append 하지 않는다(재실행 안전). 이 관측은 이후 실행의
    규칙 1 해소용 증거가 된다(evidence_ts = generated_at — 그보다 이전 진입에는 결코
    매칭되지 않으므로 과거 진입에 소급 대입되지 않는다).
    """
    if not os.path.exists(FEED_CURRENT):
        return {"status": "no_feed_file"}
    try:
        with open(FEED_CURRENT, encoding="utf-8") as f:
            gen = json.load(f).get("generated_at")
    except (OSError, ValueError) as exc:
        return {"status": "read_fail", "err": str(exc)}
    if not gen or _parse_ts(gen) is None:
        return {"status": "no_generated_at"}
    if any(rec.get("generated_at") == gen for rec in _feed_asof_records()):
        return {"status": "already_recorded", "generated_at": gen}
    rec = {
        "generated_at": gen,
        "file_mtime": datetime.fromtimestamp(
            os.stat(FEED_CURRENT).st_mtime, KST).isoformat(timespec="seconds"),
        "recorded_at": datetime.now(KST).isoformat(timespec="seconds"),
    }
    os.makedirs(os.path.dirname(FEED_ASOF), exist_ok=True)
    with open(FEED_ASOF, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {"status": "appended", "generated_at": gen}


def _feed_evidence():
    """규칙 1 증거 모음: 과거 스냅샷 파일 + feed_asof_history 관측.

    각 항목 = (evidence_ts, generated_at, source). evidence_ts 는 그 피드가 유효했던
    시각(스냅샷·관측 모두 generated_at)이다.
    """
    ev = []
    for p in sorted(glob.glob(proj("data", "feed", "**", "screener_latest_*.json"),
                              recursive=True)):
        try:
            with open(p, encoding="utf-8") as f:
                gen = json.load(f).get("generated_at")
        except (OSError, ValueError):
            continue
        ts = _parse_ts(gen)
        if ts:
            ev.append((ts, gen, "snapshot:%s" % os.path.relpath(p, proj())))
    for rec in _feed_asof_records():
        gen = rec.get("generated_at")
        ts = _parse_ts(gen)
        if ts:
            ev.append((ts, gen, "asof_history"))
    return ev


def _resolve_feed(entry_ts, ev):
    """진입 ts 이하의 가장 최근 증거의 generated_at. 없으면 (None, "unrecorded")."""
    cand = [(ts, gen, src) for ts, gen, src in ev if ts <= entry_ts]
    if not cand:
        return None, "unrecorded"
    ts, gen, src = max(cand, key=lambda x: x[0])
    return gen, src


# ── 파서 생성 (2/3) — 청산→재투입 쌍 ──────────────────────────────────────────
def _build_pairs(rows, window_min, days):
    """청산 1건 + 직후 창 신규 buy 를 한 쌍으로 묶는다. (pairs, excluded_ids, n_exits)

    판정 규칙(고정): 대상 청산 = --days 창 안의 exit_ts IS NOT NULL buy 행에서
    'unfilled:'/'reconcile: broker flat' 시작 exit_reason 제외. 재투입 = 같은 저널의
    buy 행 중 ts > exit_ts 이고 ts <= exit_ts + window_min 인 것(종목 무관, 창 내
    신규 노출 전부 — 스펙의 기계적 정의 그대로).
    """
    now = datetime.now(KST)
    cutoff = now - timedelta(days=days)
    exits = [r for r in rows
             if r.get("side") == "buy" and r["exit_dt"] is not None and r["exit_dt"] >= cutoff]
    exits.sort(key=lambda r: r["exit_dt"])
    included, excluded_ids = [], []
    for r in exits:
        if (r.get("exit_reason") or "").startswith(EXCLUDE_PREFIXES):
            excluded_ids.append(r["id"])
        else:
            included.append(r)

    ev = _feed_evidence()
    pairs = []
    for ex in included:
        w_end = ex["exit_dt"] + timedelta(minutes=window_min)
        rees = [r for r in rows
                if r.get("side") == "buy" and r["ts_dt"] is not None
                and ex["exit_dt"] < r["ts_dt"] <= w_end]
        rees.sort(key=lambda r: r["ts_dt"])
        reentries = []
        for r in rees:
            reentries.append({
                "trade_id": r["id"],
                "ts": r["ts"],
                "code": r["code"],
                "qty": r["qty"],
                "price": r["price"],
                "notional": _fmt_num(round((r["qty"] or 0) * (r["price"] or 0))),
                "screener": r.get("screener"),
                "gap_sec": round((r["ts_dt"] - ex["exit_dt"]).total_seconds(), 1),
                "pnl_if_closed": _fmt_num(r.get("pnl")) if r.get("pnl") is not None else None,
            })
        closed_sum = sum(x["pnl_if_closed"] for x in reentries
                         if x["pnl_if_closed"] is not None)
        open_notional = sum(x["notional"] for x in reentries
                            if x["pnl_if_closed"] is None)
        # 쌍 수준 피드 날짜는 가장 이른 재투입(첫 신규 노출) 기준으로 해소한다.
        if reentries:
            feed_ts, feed_src = _resolve_feed(_parse_ts(reentries[0]["ts"]), ev)
        else:
            feed_ts, feed_src = None, "no_reentry"
        pairs.append({
            "exit_trade_id": ex["id"],
            "exit_ts": ex["exit_ts"],
            "code": ex["code"],
            "name": ex["name"],
            "screener": ex.get("screener"),
            "exit_reason": ex.get("exit_reason"),
            "pnl_exit": _fmt_num(ex.get("pnl")),
            "fees_exit": _fmt_num(ex.get("fees")),
            "window_min": _fmt_num(window_min),
            "n_reentries": len(reentries),
            "reentry_notional_krw": sum(x["notional"] for x in reentries),
            "reentries": reentries,
            "pair_net_krw": _fmt_num((ex.get("pnl") or 0) + closed_sum),
            "pair_status": ("open" if any(x["pnl_if_closed"] is None for x in reentries)
                            else "closed"),
            "open_notional_krw": _fmt_num(open_notional),
            "feed_generated_at": feed_ts,
            "feed_date_source": feed_src,
        })
    return pairs, excluded_ids, len(exits)


# ── 판정·출력 ────────────────────────────────────────────────────────────────
def _sessions(pairs):
    """세션(day = exit_ts[:10]) 누적."""
    agg = {}
    for p in pairs:
        a = agg.setdefault(p["exit_ts"][:10],
                           {"n_pairs": 0, "reentry": 0, "net": 0, "open": 0})
        a["n_pairs"] += 1
        a["reentry"] += p["reentry_notional_krw"]
        a["net"] += p["pair_net_krw"]
        a["open"] += p["open_notional_krw"]
    return [{"day": d,
             "n_pairs": v["n_pairs"],
             "reentry_notional_krw": _fmt_num(v["reentry"]),
             "pair_net_krw": _fmt_num(v["net"]),
             "open_notional_krw": _fmt_num(v["open"])}
            for d, v in sorted(agg.items())]


def _ledger_line(day, n_pairs, reentry_krw, pair_net, verdict):
    return ("reentry_after_exit day=%s pairs=%d reentry_krw=%d pair_net_krw=%d verdict=%s"
            % (day, n_pairs, int(round(reentry_krw)), int(round(pair_net)), verdict))


def _judge(sessions):
    """판정 규칙(고정): pair<3 → 표본 부족 / 음수 세션≥1 && pair≥3 → 킬 후보 / 그 외 유지."""
    n_pairs = sum(s["n_pairs"] for s in sessions)
    n_neg = sum(1 for s in sessions if s["pair_net_krw"] < 0)
    if n_pairs < 3:
        verdict = "표본 부족"
    elif n_neg >= 1:
        verdict = "재투입 킬 후보"
    else:
        verdict = "재투입 유지"
    return verdict, n_pairs, n_neg


# ── 자기신고 3분리 (소스 수신 / 파서 생성 / 실제 저장) ────────────────────────
# WHY dq_claim 을 쓰지 않는가: 이 계측 스크립트는 스펙상 **네트워크·DB 호출 금지**다
# (docs/spec_reentry_after_exit.md '금지'). dq_claim._open_conn() 은 Postgres(127.0.0.1:5434)
# 에 붙으므로 여기서는 3값을 요약 json 에만 남긴다 — 크론 환경에서 적재가 필요하면
# 오케스트레이터가 dq_claim 을 별도로 호출한다.


# ── 실제 저장 (3/3) ──────────────────────────────────────────────────────────
def _persist(pairs, sessions, verdict, n_pairs, n_neg, excluded_ids, n_exits,
             args, rows, asof):
    total_reentry = _fmt_num(sum(s["reentry_notional_krw"] for s in sessions))
    total_net = _fmt_num(sum(s["pair_net_krw"] for s in sessions))
    day_list = ",".join(s["day"] for s in sessions)
    global_line = _ledger_line(day_list, n_pairs, total_reentry, total_net, verdict)

    basis = ("days=%d window=%smin exits_in_window=%d excluded_not_position=%d(ids %s) "
             "pairs=%d sessions=%d negative_sessions=%d"
             % (args.days, _fmt_num(args.window_min), n_exits, len(excluded_ids),
                ",".join(str(i) for i in excluded_ids) or "-",
                n_pairs, len(sessions), n_neg))

    jsonl_text = "".join(json.dumps(p, ensure_ascii=False) + "\n" for p in pairs)
    _atomic_write(args.jsonl_out, jsonl_text)  # 최신 실행 시 전량 재작성(append 아님)

    summary = {
        "ts": datetime.now(KST).isoformat(timespec="seconds"),
        "journal": args.journal,
        "window_min": _fmt_num(args.window_min),
        "sessions": sessions,
        "judge": {"verdict": verdict, "n_pairs": n_pairs, "basis": basis,
                  "line": global_line},
        "claim": {"source_rows": len(rows), "claimed_rows": len(pairs),
                  "persisted_rows": len(pairs),
                  "dq": "not_called: 스펙상 네트워크·DB 호출 금지 — 3값은 이 json 에 남는다"},
        "feed_asof": asof,
    }
    _atomic_write(args.json_out, json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    return global_line, [s for s in sessions]


def main(argv=None):
    ap = argparse.ArgumentParser(description="청산 후 재투입 계측(읽기 전용)")
    ap.add_argument("--journal",
                    default=os.environ.get("TRADER_JOURNAL") or JOURNAL_DEFAULT)
    ap.add_argument("--window-min", type=float, default=10.0)
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--json-out", default=JSON_OUT)
    ap.add_argument("--jsonl-out", default=JSONL_OUT)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if not os.path.exists(args.journal):
        print("저널이 없습니다: %s" % args.journal, file=sys.stderr)
        return 1

    conn, tmp = _journal_open(args.journal)
    if conn is None:
        print("저널을 열 수 없습니다: %s (%s)" % (args.journal, tmp), file=sys.stderr)
        return 1
    try:
        rows = _load_trades(conn)  # 소스 수신 (1/3)
    finally:
        conn.close()
        shutil.rmtree(tmp, ignore_errors=True)

    asof = _record_feed_asof()  # 규칙 3: 이후 실행의 규칙 1 해소용 관측 기록(멱등)
    pairs, excluded_ids, n_exits = _build_pairs(rows, args.window_min, args.days)  # 파서 (2/3)
    sessions = _sessions(pairs)
    verdict, n_pairs, n_neg = _judge(sessions)

    global_line, out_sessions = _persist(pairs, sessions, verdict, n_pairs, n_neg,
                                         excluded_ids, n_exits, args, rows, asof)

    if not args.quiet:
        for s in out_sessions:  # 세션별 세부 줄(같은 형식)
            print(_ledger_line(s["day"], s["n_pairs"], s["reentry_notional_krw"],
                               s["pair_net_krw"], verdict))
        print(global_line)  # 원장 한 줄 = stdout 마지막 줄
    return 0


if __name__ == "__main__":
    sys.exit(main())

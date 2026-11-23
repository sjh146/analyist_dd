#!/usr/bin/env python3
"""Role/market watcher while the owner is away (read-only probes).

Appends one JSON line per poll to /home/jhshi/cron/logs/hermes_watch_<date>.jsonl
and prints only anomalies to stdout (redirected to .log by the launcher).

Watches the three roles + the trading path:
  trader   - bridge /health + /balance, run_market_loop.py process, loop_state.json
  engineer/researcher - today's ledger entries (they are gated off 09:00-15:30,
             so during market hours idle is by design; the 15:35 sequence is the
             thing to look for)
  path     - tunnel 204 probe, feed freshness
No writes outside its own log: it never touches the strategy, orders or repo.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import time

CRON = "/home/jhshi/cron"
LOGDIR = os.path.join(CRON, "logs")
REPO = "/home/jhshi/analyist_dd"
TA = "/mnt/c/Users/jhshi/analyist_dd/trader-agent"
WIN_CURL = "/mnt/c/Windows/System32/curl.exe"
PS = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
POLL_S = 300
HOURS = float(os.environ.get("WATCH_HOURS", "9"))  # 9h day watch, 17h to cover the night ticks
ANOM = []

# Night role ticks (Hermes cron): engineer hourly at :00 (16:00-07:00), researcher
# every 2h (16,18,20,22,00,02,04,06). Both are refused during market hours
# (weekday 09:00-15:30), so a gap there is by design, not a failure.
NIGHT_GAP_HOURS = 3.0
# Per-role gap thresholds: the tick cadence is not the ledger cadence — a tick
# that finds nothing runnable adds no row, and the researcher's items take hours
# (measured 2026-09-30: rows at 06:01, 16:02, 20:12 ⇒ a 3 h threshold cried wolf).
ROLE_GAP_HOURS = {"model_engineer": 3.0, "researcher": 5.0}


def sh(args, timeout=60):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except Exception as exc:  # noqa: BLE001
        return 99, "", str(exc)


def kst():
    return dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S+09:00")


def bridge(path, timeout=25):
    rc, out, _ = sh([WIN_CURL, "-s", "--noproxy", "*", "-m", str(timeout),
                     "http://127.0.0.1:8100" + path], timeout=timeout + 15)
    if rc != 0 or not out.strip():
        return None
    try:
        return json.loads(out)
    except Exception:  # noqa: BLE001
        return None


def loop_procs():
    rc, out, _ = sh([PS, "-NoProfile", "-Command",
                     "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                     "ForEach-Object { $_.ProcessId.ToString() + ' ' + $_.CommandLine }"],
                    timeout=90)
    procs = {"bridge": [], "loop": []}
    for line in (out or "").splitlines():
        if "run_bridge.py" in line:
            procs["bridge"].append(line.split()[0])
        elif "run_market_loop.py" in line:
            procs["loop"].append(line.split()[0])
    return procs


def loop_state():
    try:
        with open(os.path.join(TA, "loop_state.json"), encoding="utf-8") as fh:
            d = json.load(fh)
    except Exception:  # noqa: BLE001
        return {}
    loop = d.get("loop") or {}
    cyc = d.get("last_cycle") or {}
    daily = d.get("daily") or {}
    return {
        "halt": loop.get("halt"), "halt_reason": loop.get("halt_reason"),
        "day": daily.get("date"), "entries": daily.get("entries"),
        "start_equity": daily.get("start_equity"),
        "cycle_ts": cyc.get("ts"), "phase": cyc.get("phase"),
        "decisions": cyc.get("decisions"), "exits": cyc.get("exits"),
        "acted": cyc.get("acted"), "blocked": cyc.get("blocked_screeners"),
        "failures": cyc.get("consecutive_failures"),
        "reasons": (cyc.get("reasons") or [])[:4],
    }


def tunnel():
    rc, out, _ = sh([WIN_CURL, "--noproxy", "*", "-s", "-o", "/dev/null",
                     "-w", "%{http_code}", "-m", "12",
                     "http://www.gstatic.com/generate_204"], timeout=40)
    return (out or "").strip()


def feed():
    rc, out, _ = sh(["curl", "-s", "--noproxy", "*", "-m", "10",
                     "http://127.0.0.1:8090/health"], timeout=30)
    try:
        d = json.loads(out)
        return {"age_s": round(d.get("age_seconds", -1)), "items": d.get("items"),
                "generated_at": d.get("generated_at")}
    except Exception:  # noqa: BLE001
        return {}


def ledger_today(name):
    path = os.path.join(REPO, "data/reports", name + "_ledger.jsonl")
    try:
        rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    except Exception:  # noqa: BLE001
        return {"n_today": None, "last": None}
    today = dt.datetime.now().strftime("%Y-%m-%d")
    t = [r for r in rows if str(r.get("ts", ""))[:10] == today]
    last = rows[-1] if rows else None
    return {"n_today": len(t), "last": None if not last else
            {"ts": last.get("ts"), "id": last.get("id"), "rc": last.get("rc"),
             "verdict": last.get("verdict"), "title": str(last.get("title"))[:80]}}


def snapshot():
    snap = {"ts": kst()}
    procs = loop_procs()
    snap["procs"] = procs
    snap["state"] = loop_state()
    snap["tunnel_204"] = tunnel()
    snap["feed"] = feed()
    snap["ledgers"] = {r: ledger_today(r) for r in
                       ("researcher", "model_engineer", "trader")}
    h = bridge("/health")
    b = bridge("/balance")
    snap["bridge"] = {"health": h}
    if b and b.get("ok"):
        bal = b["balance"]
        snap["account"] = {"equity": bal.get("equity"), "cash": bal.get("cash"),
                           "positions": bal.get("positions_count"),
                           "buy": bal.get("total_buy_amount"),
                           "eval": bal.get("total_eval_amount"),
                           "pnl": bal.get("total_eval_pnl"),
                           "buyable_error": str(bal.get("buyable_error"))[:60]}
    snap["cron_ticks"] = cron_recent(6)
    return snap


PUBLISH_TIMES = ((8, 40), (14, 40), (20, 30))   # weekday feed publishes (KST)


def latest_publish_before(now):
    """Latest scheduled feed publish at or before *now* (weekdays), else None."""
    if now.weekday() >= 5:
        return None
    best = None
    for hh, mm in PUBLISH_TIMES:
        cand = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if cand <= now and (best is None or cand > best):
            best = cand
    return best


def _engineer_busy():
    """True while the engineer's driver has a run in flight (running.pid alive).

    A tick that starts a multi-hour experiment writes its ledger row only when the
    run finishes, so a missing row then is the normal state, not a stalled role.
    """
    path = os.path.join(REPO, "data/reports/me_cycle/running.pid")
    try:
        with open(path, encoding="utf-8") as fh:
            pid = int((fh.read() or "0").strip())
    except Exception:  # noqa: BLE001
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def ledger_gap_anomalies(ledgers, now):
    """Flag a role whose ledger has not moved for NIGHT_GAP_HOURS.

    Only meaningful outside the market-hours guard (weekday 09:00-15:30), where
    each role is expected to tick (engineer hourly, researcher every 2h).
    """
    out = []
    weekday = now.weekday() < 5
    in_guard = weekday and 9 <= now.hour < 15 or (weekday and now.hour == 15 and now.minute < 30)
    if in_guard:
        return out
    for role, keys in (("model_engineer", "engineer"), ("researcher", "researcher")):
        limit = ROLE_GAP_HOURS.get(role, NIGHT_GAP_HOURS)
        if role == "model_engineer" and _engineer_busy():
            continue   # an experiment is running; a missing ledger row is expected
        last = (ledgers.get(role) or {}).get("last") or {}
        ts = str(last.get("ts") or "")
        if not ts:
            continue
        try:
            when = dt.datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            continue
        gap = (now.replace(tzinfo=None) - when).total_seconds() / 3600.0
        if gap > limit:
            out.append("%s 틱 %0.1f시간 없음 (마지막 %s %s)"
                       % (keys, gap, last.get("id"), ts[11:16]))
    return out


# --- Hermes 크론 실행 원장(=틱이 끝까지 갔는가) 감시 ------------------------- #
# 왜: 역할 틱은 '역할 원장에 행을 남기는가' 와 별개로 '실행이 정상 종료·전달됐는가' 가 있다.
# 실측(2026-10-01): 엔지니어/리서처 틱 4건이 "Interrupted by shutdown before terminal
# completion" 으로 끝나 delivery=None → 작업은 원장·커밋에 남았지만 보고는 사용자에게
# 가지 않았다. 원장 공백 감시로는 안 잡히는 패턴이라 실행 원장을 직접 본다(읽기 전용).
CRON_DB = os.path.expanduser("~/.hermes/cron/executions.db")
CRON_JOB_NAMES = {
    "d4070d508732": "engineer-overnight", "866357026fdc": "engineer-daily",
    "ab0889242419": "researcher-monitor", "783d066890eb": "researcher-daily",
    "4841d139d6ce": "trader-preopen", "5773c10b7147": "trader-postclose",
    "5fe32a08500f": "trader-tick", "43e18dca7279": "postclose-ticks",
    "6585f88504a7": "review-board",
}
# 전달이 정상인 결과값 — 그 밖(None·failed·not_configured·delivery_failed)은
# '보고가 어디에도 안 갔다'로 본다. no_agent 잡(위생·알림)은 의도적 침묵이라 제외한다.
CRON_OK_DELIVERY = {"delivered", "suppressed", "silent"}
_CRON_SEEN = None


def cron_recent(limit=6):
    try:
        import sqlite3
        con = sqlite3.connect("file:%s?mode=ro" % CRON_DB, uri=True, timeout=5)
    except Exception:  # noqa: BLE001
        return []
    try:
        rows = con.execute(
            "SELECT rowid, job_id, status, delivery_outcome, "
            "substr(COALESCE(error,''),1,60), started_at FROM executions "
            "ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
    except Exception:  # noqa: BLE001
        return []
    finally:
        con.close()
    out = []
    for rid, jid, status, delivery, err, started in reversed(rows):
        if jid not in CRON_JOB_NAMES:
            continue
        out.append({"rowid": rid, "job": CRON_JOB_NAMES[jid], "status": status,
                    "delivery": delivery, "started": str(started)[11:16],
                    "error": err or ""})
    return out


def cron_tick_anomalies(snap):
    """새로 끝난 역할 틱 중 비정상 종료·미전달만 이상으로 남긴다(첫 폴링은 기준선만)."""
    global _CRON_SEEN
    rows = (snap or {}).get("cron_ticks") or []
    if not rows:
        return []
    hi = max(r["rowid"] for r in rows)
    if _CRON_SEEN is None:
        _CRON_SEEN = hi
        return []
    out = []
    for r in rows:
        if r["rowid"] <= _CRON_SEEN:
            continue
        dl = r.get("delivery") or ""
        if r.get("status") != "completed" or dl not in CRON_OK_DELIVERY:
            out.append("틱 %s %s: status=%s delivery=%s %s" % (
                r["job"], r.get("started"), r.get("status"), dl or "-",
                r.get("error") or ""))
    _CRON_SEEN = hi
    return out


def anomalies(snap, prev):
    a = []
    now = dt.datetime.now()
    a.extend(cron_tick_anomalies(snap))
    # The loop only exists in session (the supervisor starts it ~08:30 and its
    # day-close latch ends it after 15:35) — flagging its absence off-session is
    # noise that hides real failures in the night log.
    weekday = now.weekday() < 5
    mins = now.hour * 60 + now.minute
    in_session = weekday and 510 <= mins <= 935   # 08:30-15:35 on a weekday
    if in_session and not snap["procs"]["loop"]:
        a.append("LOOP 프로세스 없음 (run_market_loop.py 0개)")
    if not snap["procs"]["bridge"]:
        a.append("BRIDGE 프로세스 없음 (run_bridge.py 0개)")
    if not (snap.get("bridge", {}).get("health") or {}).get("ok"):
        a.append("BRIDGE /health 실패/미연결")
    if snap["state"].get("halt"):
        a.append("LOOP halt=true: %s" % snap["state"].get("halt_reason"))
    if (snap["state"].get("failures") or 0) > 0:
        a.append("연속 실패 %s회" % snap["state"]["failures"])
    if snap.get("tunnel_204") != "204":
        a.append("터널 204 프로브 = %s" % snap.get("tunnel_204"))
    age = (snap.get("feed") or {}).get("age_s")
    gen = (snap.get("feed") or {}).get("generated_at") or ""
    # In session the feed must match the last SCHEDULED publish (08:40 / 14:40 / 20:30
    # on weekdays — the 08:40 list is the morning list and stays valid until 14:40),
    # so a fixed 1 h threshold cries wolf for six hours every morning.
    if in_session:
        want = latest_publish_before(now)
        behind = None
        if want and gen:
            try:
                got = dt.datetime.fromisoformat(gen)
                behind = (want - got).total_seconds() / 60.0
            except ValueError:
                behind = None
        if behind is None or behind > 20:
            a.append("피드가 예정 발행(%s)보다 %s분 뒤처짐(age_s=%s)" % (
                want.strftime("%H:%M") if want else "-",
                "?" if behind is None else "%0.0f" % behind, age))
    elif age is None or age > 6 * 3600:
        a.append("피드 6시간+ 미갱신(age_s=%s, 20:30 재발행 실패?)" % age)
    st = snap.get("state") or {}
    start, eq = st.get("start_equity"), (snap.get("account") or {}).get("equity")
    if start and eq:
        dd = (eq - start) / start
        if dd <= -0.02:
            a.append("당일 자산 %+.2f%% (R4 -3%% 자동정지 임박)" % (dd * 100))
    prev_ts = (prev or {}).get("state", {}).get("cycle_ts")
    if in_session and prev_ts and st.get("cycle_ts") and prev_ts == st["cycle_ts"]:
        a.append("LOOP 사이클 정지(같은 last_cycle.ts %s)" % st["cycle_ts"])
    if st.get("decisions"):
        a.append("신규 결정 %s건 (plans/acted 확인 필요)" % st["decisions"])
    if st.get("exits"):
        a.append("청산 %s건 발생 (exit_reasons 확인 필요)" % st["exits"])
    a.extend(ledger_gap_anomalies(snap.get("ledgers") or {}, now))
    return a


def main():
    os.makedirs(LOGDIR, exist_ok=True)
    day = dt.datetime.now().strftime("%Y%m%d")
    jpath = os.path.join(LOGDIR, "hermes_watch_%s.jsonl" % day)
    print("[%s] watcher start -> %s (poll %ss, %shh)" % (kst(), jpath, POLL_S, HOURS),
          flush=True)
    end = time.time() + HOURS * 3600
    prev = None
    while time.time() < end:
        snap = snapshot()
        with open(jpath, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(snap, ensure_ascii=False) + "\n")
        for line in anomalies(snap, prev):
            print("[%s] ANOMALY %s" % (kst(), line), flush=True)
        st = snap.get("state") or {}
        led = snap.get("ledgers") or {}
        print("[%s] loop=%s cycle=%s phase=%s dec=%s exits=%s | bridge=%s eq=%s | "
              "tunnel=%s feed_age=%s | R=%s E=%s T=%s" % (
                  kst(), len(snap["procs"]["loop"]), (st.get("cycle_ts") or "")[11:19],
                  st.get("phase"), st.get("decisions"), st.get("exits"),
                  bool((snap.get("bridge", {}).get("health") or {}).get("ok")),
                  (snap.get("account") or {}).get("equity"), snap.get("tunnel_204"),
                  (snap.get("feed") or {}).get("age_s"),
                  (led.get("researcher") or {}).get("n_today"),
                  (led.get("model_engineer") or {}).get("n_today"),
                  (led.get("trader") or {}).get("n_today")), flush=True)
        prev = snap
        time.sleep(POLL_S)
    print("[%s] watcher stop" % kst(), flush=True)


if __name__ == "__main__":
    main()

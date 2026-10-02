#!/usr/bin/env python3
"""모드 A 경로 감사 — 루프·청산창·브리지·피드가 '실제로 돌았나' (읽기 전용).

역할: quant-auditor (docs/QUANT_ROLE_PLAN_V2.md §5.3 모드 A).
실측 근거: 2026-09-30 청산 창(09:00–09:10)에 루프가 없었고(첫 기동 09:19) 체결은 외부에서 발생,
저널은 09:26 대조로 사후 기록됐다 — 그 유형을 매일 잡는다.

출력: reports/audit/path_<YYYY-MM-DD>.json · 이상만 stdout(없으면 조용히 종료 0).
종료코드: 0 정상 / 2 이상(issues>0).
"""
import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.request

REPO = "/home/jhshi/analyist_dd"
TA = "/mnt/c/Users/jhshi/analyist_dd/trader-agent"
OUT_DIR = os.path.join(REPO, "reports", "audit")
CURL = "/mnt/c/Windows/System32/curl.exe"
EXIT_WINDOWS = {"close": ("09:00", "09:10"), "swing": (None, None), "daytrading": ("15:10", "15:25")}


def sh(cmd, timeout=25):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "") + (r.stderr or "")
    except Exception as e:  # noqa: BLE001
        return f"__ERR__ {type(e).__name__}: {e}"


def bridge(path):
    out = sh([CURL, "-s", "--noproxy", "*", "-m", "15", "http://127.0.0.1:8100" + path])
    try:
        return json.loads(out)
    except Exception:  # noqa: BLE001
        return {"__raw__": out.strip()[:200]}


def feed_health():
    try:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        return json.loads(op.open("http://127.0.0.1:8090/health", timeout=8).read().decode())
    except Exception as e:  # noqa: BLE001
        return {"error": repr(e)[:120]}


def loop_starts(days=3):
    """runner.log 의 '=== log opened ... run --loop' 기동 시각(최근 days일)."""
    p = os.path.join(TA, "runner.log")
    if not os.path.exists(p):
        return []
    pat = re.compile(r"\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\.\d+\] === log opened[^\n]*run --loop")
    cutoff = dt.date.today() - dt.timedelta(days=days)
    starts = []
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            m = pat.search(line)
            if not m:
                continue
            try:
                ts = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            if ts.date() >= cutoff:
                starts.append(ts.isoformat(timespec="seconds"))
    return starts


def main():
    now = dt.datetime.now()
    os.makedirs(OUT_DIR, exist_ok=True)
    rec = {"ts": now.isoformat(timespec="seconds"), "mode": "path", "issues": [], "info": {}}

    # ---- 수집 ----
    try:
        st = json.load(open(os.path.join(TA, "loop_state.json"), encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        st = {}
        rec["issues"].append({"check": "loop_state", "detail": f"읽기 실패: {e}"})
    hp, bal, pos, orders = bridge("/health"), bridge("/balance"), bridge("/positions"), bridge("/orders")
    fh = feed_health()
    starts = loop_starts()

    rec["info"].update({
        "loop_process_state": {k: st.get("loop", {}).get(k) for k in ("halt", "halt_reason")},
        "daily": {k: st.get("daily", {}).get(k) for k in ("date", "entries", "closed")},
        "last_cycle": {k: st.get("last_cycle", {}).get(k)
                       for k in ("ts", "phase", "plans", "acted", "exits", "reasons", "blocked_screeners")},
        "bridge_health": hp, "positions": pos, "orders_n": len(orders.get("orders", []))
        if isinstance(orders, dict) else None,
        "feed": fh, "loop_starts_3d": starts,
    })

    # ---- 검사 ----
    npos = len(pos.get("positions", [])) if isinstance(pos, dict) else None
    if isinstance(bal, dict) and bal.get("ok"):
        npos = bal.get("balance", {}).get("positions_count", npos)
    if isinstance(hp, dict) and hp.get("ok") is False:
        rec["issues"].append({"check": "bridge_down", "detail": f"/health={hp}"})
    elif isinstance(bal, dict) and bal.get("ok") is False and isinstance(hp, dict) and hp.get("ok"):
        rec["issues"].append({"check": "bridge_half_dead", "detail": "/health ok 인데 /balance 실패(반쪽 세션)"})

    # 미청산 + 청산창 경과 (close=익일 09:00–09:10 / daytrading=15:10–15:25)
    if npos:
        for name, (s, e) in EXIT_WINDOWS.items():
            if s and now.strftime("%H:%M") > e:
                rec["issues"].append({
                    "check": "exit_window_passed_with_positions",
                    "detail": f"보유 {npos}종목 · {name} 청산창 {s}-{e} 경과(현재 {now.strftime('%H:%M')}) "
                              f"— 루프 기동 이력: {starts[-2:] or '없음'}",
                })

    # 오늘 루프가 청산창 이후에야 떴는가
    today = now.date().isoformat()
    todays = [s for s in starts if s.startswith(today)]
    if todays and todays[0][11:16] > "09:10":
        rec["issues"].append({
            "check": "loop_started_after_open_exit_window",
            "severity": "fail" if npos else "warn",
            "detail": f"오늘 첫 루프 기동 {todays[0][11:16]} (>09:10) — 익일 시가 청산 창을 놓칠 수 있었다"
                      + ("" if npos else " (보유 0종목 → 경고)"),
        })

    # last_cycle 정지 (장중 15분 초과)
    lc = st.get("last_cycle", {}).get("ts")
    if lc:
        try:
            age = (now - dt.datetime.fromisoformat(lc.replace("+09:00", ""))).total_seconds()
            market = now.weekday() < 5 and (dt.time(9, 0) <= now.time() <= dt.time(15, 30))
            if market and age > 900:
                rec["issues"].append({"check": "last_cycle_stale",
                                      "detail": f"{int(age)}초 정지 (장중 임계 900초) · ts={lc}"})
        except Exception:  # noqa: BLE001
            pass

    # halt
    if st.get("loop", {}).get("halt"):
        rec["issues"].append({"check": "halt_true",
                              "detail": f"halt_reason={st.get('loop', {}).get('halt_reason')!r}"})

    # 피드 신선도
    age = fh.get("age_seconds") if isinstance(fh, dict) else None
    if not isinstance(age, (int, float)):
        rec["issues"].append({"check": "feed_unreachable", "detail": str(fh)[:150]})
    elif float(age) > 12 * 3600:
        rec["issues"].append({"check": "feed_stale",
                              "detail": f"age {float(age)/3600:.1f}h · items={fh.get('items')}"})

    fails = [i for i in rec["issues"] if i.get("severity", "fail") != "warn"]
    rec["status"] = "fail" if fails else ("warn" if rec["issues"] else "ok")
    path = os.path.join(OUT_DIR, f"path_{now.date().isoformat()}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    if rec["issues"]:
        print(f"[audit-path] {rec['status']} · {len(fails)}실패/{len(rec['issues'])-len(fails)}경고 · {path}")
        for i in rec["issues"]:
            mark = "!" if i.get("severity", "fail") != "warn" else "~"
            print(f"  {mark} {i['check']}: {i['detail']}")
    return 2 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

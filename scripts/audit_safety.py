#!/usr/bin/env python3
"""실주문 안전 감사 — 창(개장 전·청산창 전) 전에 돌아 '사람 단계 요청'을 올린다 (읽기 전용).

역할: quant-safety (docs/QUANT_ROLE_PLAN_V2.md §5.4).
경로 감사와 분리한 이유: 창 **전에** 돌아야 의미가 있다(9/30 사고는 창 후 보고였다).

출력: reports/safety/<YYYY-MM-DD>.json · 경보만 stdout.
종료코드: 0 정상 / 2 경보 / 3 사람 단계 필요(사람이 해야 풀리는 항목).
"""
import datetime as dt
import json
import os
import re
import subprocess
import sys

REPO = "/home/jhshi/analyist_dd"
TA = "/mnt/c/Users/jhshi/analyist_dd/trader-agent"
OUT_DIR = os.path.join(REPO, "reports", "safety")
CURL = "/mnt/c/Windows/System32/curl.exe"
WPY = "/mnt/c/Users/jhshi/Python312-64/python.exe"
LIMITS = {"max_daily_trades": 3, "per_stock_pct": 0.10, "max_open_positions": 3, "total_exposure_pct": 0.30}


def sh(cmd, timeout=30):
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


def config_limits():
    """trader_core/config.py + 런처 오버라이드에서 한도 실측."""
    found = {}
    for pat, key in ((r"max_daily_trades\s*[:=]\s*(\d+)", "max_daily_trades"),
                     (r"max_open_positions\s*[:=]\s*(\d+)", "max_open_positions"),
                     (r"per_stock_pct\s*[:=]\s*([0-9.]+)", "per_stock_pct"),
                     (r"total_exposure_pct\s*[:=]\s*([0-9.]+)", "total_exposure_pct"),
                     (r"kelly_fraction\s*[:=]\s*([0-9.]+)", "kelly_fraction")):
        for f in ("trader_core/config.py", "runner/config.py"):
            p = os.path.join(TA, f)
            if not os.path.exists(p):
                continue
            m = re.search(pat, open(p, encoding="utf-8", errors="replace").read())
            if m:
                found[key] = float(m.group(1)) if "." in m.group(1) else int(m.group(1))
                break
    return found


def journal_stats():
    """저널: 총건수·미청산·fees 합 (Windows 파이썬으로 읽는다 — drvfs sqlite I/O 회피).
    주의: Windows 파이썬은 /mnt/c 경로를 열 수 없다 → C:\\ 경로로 넘긴다."""
    win_journal = r"C:\Users\jhshi\analyist_dd\trader-agent\journal\trade_journal.sqlite3"
    code = (
        "import sqlite3,json;c=sqlite3.connect(r'" + win_journal + "');"
        "print(json.dumps(list(c.execute('select count(*),sum(case when exit_ts is null then 1 else 0 end),"
        "sum(coalesce(fees,0)),sum(coalesce(pnl,0)) from trades'))[0]))"
    )
    out = sh([WPY, "-c", code])
    try:
        n, opn, fees, pnl = json.loads(out.strip().splitlines()[-1])
        return {"n": n, "open": opn, "fees_sum": fees, "pnl_sum": pnl}
    except Exception:  # noqa: BLE001
        return {"error": out.strip()[:120]}


def processes():
    out = sh(["/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe", "-NoProfile", "-Command",
              "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | ForEach-Object "
              "{ $_.CommandLine }"], timeout=40)
    return {"bridge": out.count("run_bridge.py"), "loop": out.count("run_market_loop.py")}


def main():
    now = dt.datetime.now()
    os.makedirs(OUT_DIR, exist_ok=True)
    rec = {"ts": now.isoformat(timespec="seconds"), "mode": "safety", "alerts": [], "human_steps": [], "info": {}}

    hp, bal, pos, orders = bridge("/health"), bridge("/balance"), bridge("/positions"), bridge("/orders")
    npos = len(pos.get("positions", [])) if isinstance(pos, dict) else None
    if isinstance(bal, dict) and bal.get("ok"):
        npos = bal.get("balance", {}).get("positions_count", npos)
    norders = len(orders.get("orders", [])) if isinstance(orders, dict) else None
    proc = processes()
    cfg = config_limits()
    js = journal_stats()
    kill = os.path.exists(os.path.join(TA, "kill_switch.txt"))

    rec["info"] = {"bridge_health": hp, "balance_ok": bool(isinstance(bal, dict) and bal.get("ok")),
                   "positions": npos, "orders": norders, "processes": proc, "limits": cfg,
                   "kill_switch_file": kill, "journal": js}

    # 1) 킬스위치
    if kill:
        rec["alerts"].append({"check": "kill_switch_present",
                              "detail": "trader-agent/kill_switch.txt 존재 — 루프는 신규 진입을 멈춘다"})
    # 2) 한도 일치
    for k, v in LIMITS.items():
        if k in cfg and abs(float(cfg[k]) - float(v)) > 1e-9:
            rec["alerts"].append({"check": "limit_mismatch",
                                  "detail": f"{k}: 설정 {cfg[k]} vs 기대 {v}"})
    # 3) 세션 생존 (창 전 사람 단계)
    connected = bool(isinstance(hp, dict) and hp.get("connected"))
    if not connected:
        rec["human_steps"].append("Creon PLUS 로그인 후 브리지·루프 기동 (감독기가 1~2분 내 자동 기동)")
    elif not (isinstance(bal, dict) and bal.get("ok")):
        rec["human_steps"].append("브리지 /health ok 인데 /balance 실패(반쪽 세션) — 재로그인 필요할 수 있음")
    # 4) 프로세스 2개
    if proc.get("loop", 0) == 0:
        rec["alerts"].append({"check": "loop_process_missing",
                              "detail": "run_market_loop.py 프로세스 0개 (브리지만 살아있는 상태)"})
    if proc.get("bridge", 0) == 0:
        rec["alerts"].append({"check": "bridge_process_missing", "detail": "run_bridge.py 프로세스 0개"})
    # 5) 청산창 전 경보 (보유 있는데 창이 임박/도래)
    if npos:
        hm = now.strftime("%H:%M")
        if "09:00" <= hm <= "09:05":
            rec["alerts"].append({"check": "exit_window_imminent",
                                  "detail": f"보유 {npos}종목 · 익일 시가 청산창 09:00-09:10 진행 중 — 세션 생존 필수"})
        elif hm > "09:10" and "15:00" > hm:
            rec["alerts"].append({"check": "exit_window_missed",
                                  "detail": f"보유 {npos}종목 · 시가 청산창(09:00-09:10) 경과 — 루프 기동 이력 확인"})
    # 6) 회계 (fees 미반영)
    if isinstance(js, dict) and js.get("n") and not js.get("fees_sum"):
        rec["alerts"].append({"check": "fees_unbooked",
                              "detail": f"청산 {js.get('n')}건인데 fees 합=0 — 수수료 백필(tools/backfill_fees.py) 미실행"})

    rec["status"] = "human_step" if rec["human_steps"] else ("alerts" if rec["alerts"] else "ok")
    path = os.path.join(OUT_DIR, f"safety_{now.date().isoformat()}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    if rec["alerts"] or rec["human_steps"]:
        print(f"[audit-safety] {rec['status']} · {path}")
        for a in rec["alerts"]:
            print(f"  ! {a['check']}: {a['detail']}")
        for h in rec["human_steps"]:
            print(f"  ▶ 사람 단계: {h}")
    if rec["human_steps"]:
        return 3
    return 2 if rec["alerts"] else 0


if __name__ == "__main__":
    sys.exit(main())

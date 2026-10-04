#!/usr/bin/env python3
"""실주문 안전 감사 — 창(개장 전·청산창 전) 전에 돌아 '사람 단계 요청'을 올린다 (읽기 전용).

역할: quant-safety (docs/QUANT_ROLE_PLAN_V2.md §5.4).
경로 감사와 분리한 이유: 창 **전에** 돌아야 의미가 있다(9/30 사고는 창 후 보고였다).

출력: reports/safety/<YYYY-MM-DD>.json · 경보만 stdout.
종료코드: 0 정상 / 2 경보 / 3 사람 단계 필요(사람이 해야 풀리는 항목).

2026-10-04 통합: audit_exit_window(09:15 틱)의 최근 창 밖 청산을 preopen 경보로 싣는다.
WHY(실측): objective_state top_lever = exit_window_miss −1,814원(evidence '중간')인데
08:25 preopen 감사에는 이 항목이 없어 장 시작 전에 위험을 알 수 없었다. audit_exit_window 가
남긴 JSON 실측: n_sells=3 · n_out_of_window=3 · out_of_window_krw=−1,814.0 (ts 2026-10-02T22:13:38).
(조회: python3 -c "import json;print(json.load(open('data/state/objective_state.json'))['top_lever'])"
      cat data/reports/audit/exit_window.json)
통합 방식(3분리·멱등): exit_window_receive(소스 수신) → exit_window_parse(파서 생성) →
rec['alerts'/'info'] 저장(실제 저장) + dq_claim.record_claim 자기신고. safety_<date>.json 은
덮어쓰기이고 경보는 매 실행 새로 구성된다 → 재실행 안전(중복 적재 없음).
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
EXIT_WINDOW_JSON = os.path.join(REPO, "data", "reports", "audit", "exit_window.json")
# 이보다 묵은 exit_window.json 은 '최근'이라 할 수 없어 경보 근거로 쓰지 않는다(틱 정지 시 오경보 방지).
# audit_exit_window 자신의 조회 범위(--days 10)와 같다.
EXIT_WINDOW_MAX_AGE_DAYS = 10


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


def exit_window_receive(path=None):
    """소스 수신(1/3) — audit_exit_window 가 남긴 JSON(data/reports/audit/exit_window.json)을 읽는다.

    없거나 깨졌으면 (None, 사유) — preopen 감사가 이것 때문에 죽으면 안 된다(감사가 감사에
    깨지는 것). 판정은 exit_window_parse 가 한다.
    """
    p = path or EXIT_WINDOW_JSON
    if not os.path.exists(p):
        return None, f"no file: {p}"
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f), None
    except (OSError, ValueError) as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


def exit_window_parse(rec, remaining_positions=None):
    """파서 생성(2/3) — 수신 JSON → (info 요약, 경보 또는 None).

    판정 규칙(추측 금지):
      · 창 밖 = ``in_window`` 가 명시적으로 False 인 행만. 키가 없으면(옛 형식) 창 밖으로 세지 않는다.
      · 원화는 행별 vs_open_krw 합(측정 불가 행은 0 기여) — 파서가 다시 센다(파일의 합계를 그대로 안 믿는다).
      · JSON 이 EXIT_WINDOW_MAX_AGE_DAYS 일보다 묵었으면(stale) 경보 없이 info 만 준다 —
        틱이 죽은 지 오래면 '최근'이라 할 수 없고, 그 사실은 info['stale'] 로 남는다.
    remaining_positions: 저널 기준 미청산 건수(journal_stats()['open']) — 브리지가 죽은
    preopen 에도 조회 가능한 잔존 포지션 수. 경보 문구와 info 에 실린다.
    """
    if not isinstance(rec, dict):
        return None, None
    try:
        ts = dt.datetime.fromisoformat(str(rec.get("ts") or ""))
    except (TypeError, ValueError):
        return None, None
    age_days = (dt.datetime.now() - ts).total_seconds() / 86400.0
    sells = [r for r in (rec.get("sells") or []) if isinstance(r, dict)]
    out_rows = sorted((r for r in sells if r.get("in_window") is False),
                      key=lambda r: str(r.get("ts") or ""), reverse=True)
    krw = round(sum(r.get("vs_open_krw") for r in out_rows if r.get("vs_open_krw") is not None), 1)
    info = {"source_ts": rec.get("ts"), "age_days": round(age_days, 2),
            "stale": age_days > EXIT_WINDOW_MAX_AGE_DAYS,
            "window": rec.get("window"), "n_sells": len(sells),
            "n_out_of_window": len(out_rows), "out_of_window_krw": krw,
            "remaining_positions": remaining_positions,
            "out_rows": [{"date": r.get("date"), "hm": r.get("hm"), "code": r.get("code"),
                          "price": r.get("price"), "open_price": r.get("open_price"),
                          "vs_open_krw": r.get("vs_open_krw")} for r in out_rows[:3]]}
    if info["stale"] or not out_rows:
        return info, None
    latest = out_rows[0]
    rem = (f" · 잔존 포지션 {remaining_positions}건 — 오늘 창(09:00-09:10)도 놓치면 같은 손실 재발"
           if remaining_positions else "")
    alert = {"check": "exit_out_of_window_recent",
             "detail": (f"최근 창 밖 청산 {len(out_rows)}건/{len(sells)}건 · 시가 대비 {krw:+,.0f}원 "
                        f"— 최근 {latest.get('date')} {latest.get('hm')} {latest.get('code')} "
                        f"@{latest.get('price')}(시가 {latest.get('open_price')}) "
                        f"[감사 시각 {rec.get('ts')}]{rem}")}
    return info, alert


def _exit_window_claim(source_rows, claimed, persisted, note):
    """자기신고(3/3-보조) — 소스 수신/파서 생성/실제 저장 3값을 dq_runner_claim 에 남긴다.

    scripts/dq_claim.py 의 record_claim 재사용(러너 자기신고 규약). preopen 감사의 본업은
    경보이므로 자기신고 실패는 사유 문자열로 info 에만 남기고 감사를 깨지 않는다
    (dq_claim.py 설계 원칙: 자기신고 실패가 수집을 깨면 안 된다).
    """
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from dq_claim import _open_conn, record_claim  # noqa: PLC0415 — psycopg2 지연 import
        conn = _open_conn()
        try:
            record_claim(conn, runner="audit_safety", table_name="exit_window",
                         claimed_rows=int(claimed), persisted_rows=int(persisted),
                         source_rows=int(source_rows), note=str(note)[:400])
        finally:
            conn.close()
        return "ok"
    except Exception as e:  # noqa: BLE001
        return f"{type(e).__name__}: {e}"


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

    # 5b) audit_exit_window(09:15 틱) 통합 — 소스 수신 → 파서 생성 → 저장 3분리.
    #     WHY 는 파일 상단 docstring. 잔존 포지션은 브리지가 죽어도 읽히는 저널 미청산 수.
    ewr, ewr_err = exit_window_receive()
    ew_info, ew_alert = (exit_window_parse(ewr, remaining_positions=(js.get("open") if isinstance(js, dict) else None))
                         if isinstance(ewr, dict) else (None, None))
    rec["info"]["exit_window"] = ew_info or {"error": ewr_err or "unparseable",
                                             "source": EXIT_WINDOW_JSON}

    # 1) 킬스위치
    if kill:
        rec["alerts"].append({"check": "kill_switch_present",
                              "detail": "trader-agent/kill_switch.txt 존재 — 루프는 신규 진입을 멈춘다"})
    # 2) 한도 일치
    for k, v in LIMITS.items():
        if k in cfg and abs(float(cfg[k]) - float(v)) > 1e-9:
            rec["alerts"].append({"check": "limit_mismatch",
                                  "detail": f"{k}: 설정 {cfg[k]} vs 기대 {v}"})
    # 3) 세션 생존 (창 전 사람 단계) — 청산창 마감 시각과 **실측 비용**을 함께 알린다.
    #    WHY(2026-10-02 실측): close 프로파일의 청산 창은 익일 09:00-09:10 뿐이고, 09-30 에는
    #    루프가 09:19 에 기동해 3건이 창 밖(09:26)에서 청산됐다 → 시가 대비 −1,814원 = 그날 실현손실 전액.
    #    '로그인 필요'만 알리면 데드라인이 안 보인다 → 몇 시까지인지·놓치면 얼마인지 같이 쓴다.
    cost_note = ("놓치면 청산이 창 밖으로 밀려 시가보다 낮게 팔린다 — 실측 2026-09-30: 3건 합계 "
                 "−1,814원(포지션당 약 −0.5%)")
    deadline = "09:10(청산창 09:00-09:10 마감)"
    connected = bool(isinstance(hp, dict) and hp.get("connected"))
    if not connected:
        rec["human_steps"].append(
            f"Creon PLUS 로그인 후 브리지·루프 기동 — **{deadline}까지**"
            f"(감독기가 1~2분 내 자동 기동). {cost_note}")
    elif not (isinstance(bal, dict) and bal.get("ok")):
        rec["human_steps"].append("브리지 /health ok 인데 /balance 실패(반쪽 세션) — 재로그인 필요할 수 있음")
    # 3b) 보유가 있는데 루프 프로세스가 없으면 청산창을 놓칠 수 있다 — 사람이 개입할 창을 명시
    if proc.get("loop", 0) == 0 and npos:
        rec["human_steps"].append(
            f"보유 {npos}종목인데 run_market_loop.py 없음 — {deadline}까지 기동 필요. {cost_note}")
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
    # 5c) audit_exit_window 통합 경보 — 창 밖 청산이 최근에 있었다면 위험 항목으로 저장
    if ew_alert:
        rec["alerts"].append(ew_alert)
    rec["info"]["exit_window"]["claim"] = _exit_window_claim(
        source_rows=(ew_info or {}).get("n_sells", 0),
        claimed=(ew_info or {}).get("n_out_of_window", 0),
        persisted=1 if ew_alert else 0,
        note=("%s krw=%s" % (ew_info.get("source_ts"), ew_info.get("out_of_window_krw"))
              if ew_info else (ewr_err or "no data")))
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

#!/usr/bin/env python3
"""장중 스택 점검: 피드 → 게이트 → 진입 → 헬스(브리지·뉴스게이트·피드서버) + 데이터 신선도.

후보가 있는데 진입이 0건이면 차단 사유를 Top3 로 남긴다.
산출: reports/trading_health_latest.json, reports/trading_health.log
"""
from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
import urllib.error
import urllib.request

REPO = "/home/jhshi/analyist_dd"
FEED = os.path.join(REPO, "data/feed/screener_latest.json")
TRADER = "/mnt/c/Users/jhshi/analyist_dd/trader-agent"
LOOP = os.path.join(TRADER, "loop_state.json")
RUNNER = os.path.join(TRADER, "runner.log")
OUT_JSON = os.path.join(REPO, "reports/trading_health_latest.json")
OUT_LOG = os.path.join(REPO, "reports/trading_health.log")

GATES = (
    ("bridge", "http://127.0.0.1:8100/health"),
    ("newsgate", "http://127.0.0.1:8082/newsgate?codes=005930"),
    ("feedserver", "http://127.0.0.1:8090/screener_latest.json"),
)


def kst() -> str:
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat(timespec="seconds")


def http_ok(url: str, timeout: float = 6.0) -> dict:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as res:
            return {"ok": True, "status": res.status}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": exc.code, "error": str(exc)[:120]}
    except Exception as exc:
        return {"ok": False, "status": None, "error": str(exc)[:120]}


def feed_state() -> dict:
    try:
        data = json.load(open(FEED, encoding="utf-8"))
    except Exception as exc:
        return {"error": str(exc)[:120]}
    cand = data.get("candidates") or {}
    counts = {k: len((v or {}).get("items") or []) for k, v in cand.items()}
    return {
        "generated_at": data.get("generated_at"),
        "counts": counts,
        "total": sum(counts.values()),
        "regime": (data.get("regime") or {}).get("state"),
        "daytrading_reason": (cand.get("daytrading") or {}).get("reason"),
    }


def loop_state() -> dict:
    try:
        data = json.load(open(LOOP, encoding="utf-8"))
    except Exception as exc:
        return {"error": str(exc)[:120]}
    daily = data.get("daily") or {}
    guard = (data.get("check") or {}).get("bear_guard") or {}
    return {
        "date": daily.get("date"),
        "start_equity": daily.get("start_equity"),
        "entries": daily.get("entries"),
        "size_mult": guard.get("size_mult"),
        "edge_bonus": guard.get("edge_bonus"),
        "block_new": guard.get("block_new"),
        "regime": guard.get("state"),
    }


def last_cycle() -> dict:
    try:
        text = open(RUNNER, encoding="utf-8", errors="replace").read()
    except Exception as exc:
        return {"error": str(exc)[:120]}
    cycles = text.split("cycle done")
    block = cycles[-2] if len(cycles) > 1 else cycles[-1]
    lines = [ln.strip(" -") for ln in block.splitlines() if ln.strip().startswith(("PRE filter", "G skip", "r5:", "STALE skip"))]
    reasons: dict[str, int] = {}
    for ln in lines:
        for item in re.findall(r"([a-z_]+) \(", ln) or re.findall(r"(?:^|; )([a-z_0-9]+) -:", ln):
            reasons[item] = reasons.get(item, 0) + 1
    return {"lines": lines[-8:], "reason_counts": sorted(reasons.items(), key=lambda kv: -kv[1])[:6]}


def last_trading_day(today: datetime.date | None = None) -> str:
    day = today or datetime.date.today()
    holidays = set()
    path = os.path.join(REPO, "data/krx_holidays.json")
    if os.path.exists(path):
        try:
            holidays = set(json.load(open(path, encoding="utf-8")))
        except Exception:
            holidays = set()
    while day.weekday() >= 5 or day.isoformat() in holidays:
        day -= datetime.timedelta(days=1)
    return day.isoformat()


def expected_data_day(now: datetime.datetime | None = None) -> str:
    now = now or datetime.datetime.now()
    today = now.date()
    if today.weekday() < 5 and now.hour < 19:
        return last_trading_day(today - datetime.timedelta(days=1))
    return last_trading_day(today)


def supervisor_state() -> dict:
    path = os.path.join(REPO, "reports/supervisor_latest.json")
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:100]}
    age_min = None
    try:
        at = datetime.datetime.fromisoformat(data.get("at"))
        age_min = round((datetime.datetime.now(at.tzinfo) - at).total_seconds() / 60, 1)
    except Exception as exc:
        data["_age_error"] = str(exc)[:80]
        age_min = None
    return {"ok": data.get("verdict") == "OK", "verdict": data.get("verdict"),
            "age_min": age_min, "critical": data.get("critical")}


def db_freshness() -> dict:
    try:
        env = {}
        for line in open(os.path.join(REPO, ".env"), encoding="utf-8", errors="replace"):
            if "=" in line and not line.strip().startswith("#"):
                key, val = line.rstrip("\n").split("=", 1)
                env[key.strip()] = val.strip()
        sql = (
            "select 'stock_prices', max(trade_date)::text from stock_prices "
            "union all select 'minute_close_bars', max(trade_date)::text from minute_close_bars "
            "union all select 'event_features', max(trade_date)::text from event_features "
            "union all select 'disclosures', max(rcept_dt)::text from disclosures;"
        )
        cmd = ["docker", "exec", "-e", "PGPASSWORD=" + env.get("POSTGRES_PASSWORD", ""), "stock_postgres",
               "psql", "-U", env.get("POSTGRES_USER", "stock_user"), "-d", env.get("POSTGRES_DB", "stock_trading"),
               "-t", "-A", "-F", "|", "-c", sql]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout
        rows = [ln.split("|") for ln in out.strip().splitlines() if "|" in ln]
        return {name: value for name, value in rows}
    except Exception as exc:
        return {"error": str(exc)[:120]}


def main() -> int:
    report = {
        "generated_at": kst(),
        "feed": feed_state(),
        "loop": loop_state(),
        "last_cycle": last_cycle(),
        "health": {name: http_ok(url) for name, url in GATES},
        "data": db_freshness(),
        "supervisor": supervisor_state(),
    }
    expect = expected_data_day()
    stale = {name: value for name, value in report["data"].items()
             if isinstance(value, str) and value < expect}
    report["data_expected"] = expect
    report["data_stale"] = stale
    entries = report["loop"].get("entries")
    total = report["feed"].get("total") or 0
    blocked = bool(total) and entries == 0
    report["verdict"] = "NO_ENTRY_BUT_CANDIDATES" if blocked else ("OK" if entries else "NO_CANDIDATES")
    bad = [name for name, res in report["health"].items() if not res.get("ok")]
    if bad:
        report["verdict"] += "|HEALTH_FAIL:" + ",".join(bad)
    if report["data_stale"]:
        report["verdict"] += "|DATA_STALE:" + ",".join(sorted(report["data_stale"]))
    if not report["supervisor"].get("ok"):
        report["verdict"] += "|SUPERVISOR_FAIL"
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    json.dump(report, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    summary = (
        "[{0}] verdict={1} | feed={2} total={3} dt_reason={4} | entries={5} size×{6} edge+{7} "
        "| health={8} | data={9}".format(
            report["generated_at"], report["verdict"], report["feed"].get("counts"), total,
            report["feed"].get("daytrading_reason"), entries, report["loop"].get("size_mult"),
            report["loop"].get("edge_bonus"),
            {k: ("ok" if v.get("ok") else "FAIL") for k, v in report["health"].items()},
            report["data"],
        )
    )
    with open(OUT_LOG, "a", encoding="utf-8") as fh:
        fh.write(summary + "\n")
        for reason, count in report["last_cycle"].get("reason_counts") or []:
            fh.write("    사유 {0} ×{1}\n".format(reason, count))
    print(summary)
    for reason, count in report["last_cycle"].get("reason_counts") or []:
        print("  사유", reason, "×", count)
    return 0 if not bad else 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""시장·수집 경로 watchdog — 조용할 때는 침묵, 이상할 때만 한 줄씩.

감시 항목 (KST 기준, 해당 시간대에만 검사)
  1) 뉴스 수집 정지  : news_analysis 최신 적재 나이. 07~22시 2h↑ 경보, 2.5h↑ 이면 컨테이너를 1회 재시작(3h 쿨다운)
  2) 컨테이너        : 기대 17개 중 내려간 것
  3) 브리지(Creon)   : 평일 08:00~16:00 에 /health connected=false 가 2회 연속이면 경보
  4) 루프            : 평일 08:35~15:35 에 loop_state last_cycle 이 6분 이상 낡으면 경보
  5) 피드            : 평일 08:35~10:00 에 data/feed/screener_latest.json 의 generated_at 이 오늘 것이 아니면 경보

사용법: market_watchdog.py [--verbose] [--once-state FILE]
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import urllib.request

STATE_PATH = os.path.expanduser("~/.hermes/state/market_watchdog.json")
PROJ = os.environ.get("PROJ_DIR") or "/home/jhshi/analyist_dd"
FEED = os.path.join(PROJ, "data", "feed", "screener_latest.json")
LOOP_STATE = "/mnt/c/Users/jhshi/analyist_dd/trader-agent/loop_state.json"
BRIDGE = "http://127.0.0.1:8100/health"
EXPECTED_CONTAINERS = 17
NEWS_RESTART_COOLDOWN_S = 3 * 3600
ALERT_REPEAT_S = 3600  # 같은 항목 60분에 1회만


def now() -> datetime.datetime:
    return datetime.datetime.now()


def load_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return {}


def save_state(path: str, state: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except Exception:
        pass


def psql(sql: str) -> list[list[str]]:
    try:
        out = subprocess.run(
            ["docker", "exec", "stock_postgres", "psql", "-U", "stock_user",
             "-d", "stock_trading", "-t", "-A", "-F", "|", "-c", sql],
            capture_output=True, text=True, timeout=60)
    except Exception:
        return []
    rows = []
    for line in out.stdout.strip().splitlines():
        line = line.strip()
        if line:
            rows.append(line.split("|"))
    return rows


def news_age_hours() -> float | None:
    rows = psql("select extract(epoch from (now() - max(analyzed_at)))/3600 from news_analysis;")
    try:
        return float(rows[0][0])
    except Exception:
        return None


def containers_down() -> int:
    try:
        out = subprocess.run(["docker", "ps", "-q"], capture_output=True, text=True, timeout=60)
        return max(0, EXPECTED_CONTAINERS - len([x for x in out.stdout.split() if x]))
    except Exception:
        return -1


def bridge_connected() -> bool | None:
    """브리지는 Windows 프로세스라 WSL 127.0.0.1 로는 닿지 않는다 → Windows curl 로 확인."""
    candidates = [
        ["/mnt/c/Windows/System32/curl.exe", "-s", "--noproxy", "*", "-m", "12", BRIDGE],
        ["curl", "-s", "--noproxy", "*", "-m", "12", BRIDGE],
    ]
    for command in candidates:
        try:
            out = subprocess.run(command, capture_output=True, text=True, timeout=25)
        except Exception:
            continue
        text = (out.stdout or "").strip()
        if text.startswith("{"):
            try:
                return bool(json.loads(text).get("connected"))
            except Exception:
                continue
    return None


def loop_age_minutes() -> float | None:
    try:
        with open(LOOP_STATE, encoding="utf-8") as handle:
            data = json.load(handle)
        ts = (data.get("last_cycle") or {}).get("ts")
        if not ts:
            return None
        stamp = datetime.datetime.fromisoformat(ts)
        reference = now()
        if stamp.tzinfo is not None:
            reference = datetime.datetime.now(stamp.tzinfo)
        return (reference - stamp).total_seconds() / 60.0
    except Exception:
        return None


def feed_generated() -> str:
    try:
        with open(FEED, encoding="utf-8") as handle:
            return str(json.load(handle).get("generated_at") or "")
    except Exception:
        return ""


def restart_news() -> bool:
    try:
        return subprocess.run(["docker", "restart", "stock_news_analyzer"],
                              capture_output=True, timeout=180).returncode == 0
    except Exception:
        return False


def should_alert(state: dict, key: str) -> bool:
    last = float(state.get(key) or 0)
    import time
    if time.time() - last < ALERT_REPEAT_S:
        return False
    state[key] = time.time()
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--state", default=STATE_PATH)
    args = parser.parse_args()

    stamp = now()
    weekday = stamp.weekday() < 5
    clock = stamp.hour * 60 + stamp.minute
    state = load_state(args.state)
    alerts: list[str] = []

    # 1) 뉴스 수집 정지
    age = news_age_hours()
    limit = 2.0 if 7 * 60 <= clock <= 22 * 60 else 5.0
    if args.verbose:
        print(f"news_age={age if age is None else round(age, 2)}h limit={limit}h")
    if age is not None and age > limit:
        restarted = False
        import time
        cooldown_ok = time.time() - float(state.get("news_restart_at") or 0) > NEWS_RESTART_COOLDOWN_S
        if age > limit + 0.5 and cooldown_ok:
            restarted = restart_news()
            if restarted:
                state["news_restart_at"] = time.time()
        if should_alert(state, "news_alert_at"):
            tail = " → 컨테이너 재시작함(수집 재개 확인 필요)" if restarted else ""
            if not restarted and cooldown_ok is False:
                tail = " → 재시작 쿨다운 중(3h)"
            elif not restarted:
                tail = " → 재시작 실패"
            alerts.append(f"뉴스 수집 정지 의심: 최신 적재 {age:.1f}h 전 (기준 {limit:.0f}h){tail}")

    # 2) 컨테이너
    down = containers_down()
    if args.verbose:
        print(f"containers_down={down}")
    if down > 0 and should_alert(state, "container_alert_at"):
        alerts.append(f"컨테이너 {down}개 내려감 (기대 {EXPECTED_CONTAINERS}개) — docker ps 확인")

    # 3) 브리지 (평일 장중)
    if weekday and 8 * 60 <= clock <= 16 * 60:
        connected = bridge_connected()
        if args.verbose:
            print(f"bridge_connected={connected}")
        fails = int(state.get("bridge_fails") or 0)
        fails = 0 if connected else fails + 1
        state["bridge_fails"] = fails
        if fails >= 2 and should_alert(state, "bridge_alert_at"):
            alerts.append(f"브리지 미연결 {fails}회 연속 (connected={connected}) — Creon 세션/브리지 확인")
    else:
        state["bridge_fails"] = 0

    # 4) 루프 (평일 개장 후)
    if weekday and 8 * 60 + 35 <= clock <= 15 * 60 + 35:
        lag = loop_age_minutes()
        if args.verbose:
            print(f"loop_lag={None if lag is None else round(lag, 1)}m")
        if lag is not None and lag > 6 and should_alert(state, "loop_alert_at"):
            alerts.append(f"트레이더 루프 사이클 정지 의심: 마지막 {lag:.0f}분 전")

    # 5) 피드 (평일 개장 직후)
    if weekday and 8 * 60 + 35 <= clock <= 10 * 60:
        generated = feed_generated()
        today = stamp.strftime("%Y-%m-%d")
        if args.verbose:
            print(f"feed_generated={generated}")
        if today not in generated and should_alert(state, "feed_alert_at"):
            alerts.append(f"피드가 오늘 것이 아님: generated_at={generated or '(없음)'}")

    save_state(args.state, state)
    for line in alerts:
        print(line)
    return 3 if alerts else 0


if __name__ == "__main__":
    raise SystemExit(main())

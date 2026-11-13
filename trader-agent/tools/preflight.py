#!/usr/bin/env python3
"""설치·개장 전 자기진단 (preflight) — 처음 클론한 사람이 한 번에 확인하는 스크립트.

확인 항목
  1. 인터프리터 비트(64bit 코어) + 32bit 브리지 인터프리터와 pywin32
  2. Creon 호스트 프로세스(로그인 세션) + 업데이터 진행 여부
  3. 브리지 /health · /balance · /positions (루프백, 프록시 우회)
  4. 피드 신선도(또는 rank 모드)
  5. 뉴스 게이트 도달성(설정된 경우 — fail-closed 면 모든 진입이 막힌다)
  6. 저널/킬스위치/경로 쓰기 가능
  7. 유효 위험 한도(프리셋·파일·env 가 실제로 먹혔는지) + 위험 경고

사용
  C:\\Tools\\Python312-64\\python.exe tools\\preflight.py
  C:\\Tools\\Python312-64\\python.exe tools\\preflight.py --preset paper_safe
  ... --json          (기계 판독용)
  ... --feed URL      (피드 주소 일회성 지정)

종료코드: 0 정상 / 2 경고 / 3 실패 — 크론·래퍼가 그대로 쓸 수 있다.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

KST = timezone(timedelta(hours=9))
CREON_HOST = ("comain", "cpstart", "dibserver", "costarter")
CREON_UPDATER = ("spdn", "costarter", "mainupdate", "ncstarter")
PY32_CANDIDATES = (
    os.environ.get("TRADER_PY32", ""),
    os.path.join(os.environ.get("USERPROFILE", os.path.expanduser("~")), "Python311-32", "python.exe"),
    r"C:\Python311-32\python.exe",
    r"C:\Python312-32\python.exe",
)

results = []  # [(status, name, detail)]


def add(status: str, name: str, detail: str) -> None:
    results.append((status, name, detail))


def http_json(url: str, timeout: float = 8.0):
    """GET JSON with the environment proxy bypassed.

    Local services are 127.0.0.1; an inherited ``http_proxy`` turns them into
    502/000 and reads like a dead service (measured repeatedly on this stack).
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def http_status(url: str, timeout: float = 5.0) -> int:
    """Probe any HTTP status — a 4xx still proves the service is alive."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as response:
            return response.status
    except urllib.error.HTTPError as exc:  # service answered, request was wrong
        return exc.code


def is_wsl() -> bool:
    try:
        with open("/proc/sys/kernel/osrelease", "r", encoding="utf-8") as handle:
            return "microsoft" in handle.read().lower()
    except OSError:
        return False


def win_path(path: str) -> str:
    """Translate ``C:\\...`` to ``/mnt/c/...`` when running inside WSL."""
    if os.name == "nt" or not path:
        return path
    if len(path) > 2 and path[1] == ":" and path[2] in "\\/":
        return "/mnt/" + path[0].lower() + path[2:].replace("\\", "/")
    return path


def tasklist_path() -> str:
    return (r"C:\Windows\System32\tasklist.exe" if os.name == "nt"
            else "/mnt/c/Windows/System32/tasklist.exe")


def check_interpreter() -> None:
    bits = 64 if sys.maxsize > 2 ** 32 else 32
    version = ".".join(str(x) for x in sys.version_info[:3])
    if bits == 64:
        add("pass", "코어 인터프리터", "python {0} / {1}bit".format(version, bits))
    else:
        add("fail", "코어 인터프리터",
            "python {0} / {1}bit — 트레이더 코어·루프는 64bit 로 돌려야 한다".format(version, bits))


def check_py32() -> None:
    found = None
    for candidate in PY32_CANDIDATES:
        if candidate and os.path.isfile(win_path(candidate)):
            found = candidate
            break
    if not found:
        patterns = [os.path.join(os.environ.get("USERPROFILE", os.path.expanduser("~")),
                                 "Python3*-32", "python.exe")]
        if os.name != "nt":  # WSL: user profiles live under /mnt/c/Users
            patterns.append("/mnt/c/Users/*/Python3*-32/python.exe")
        matches = []
        for pattern in patterns:
            matches.extend(glob.glob(pattern))
        found = sorted(matches)[0] if matches else None
    if not found:
        add("fail", "브리지 인터프리터(32bit)",
            "32bit python 을 찾지 못했다 — Creon COM 은 32bit 전용. 설치 후 TRADER_PY32 로 지정")
        return
    probe = ("import struct,sys;print(struct.calcsize('P')*8);"
             "import win32com.client;print('pywin32-ok')")
    try:
        out = subprocess.run([win_path(found), "-c", probe], capture_output=True, text=True, timeout=60)
    except Exception as exc:  # noqa: BLE001 - report, never crash the preflight
        add("fail", "브리지 인터프리터(32bit)", "{0} 실행 실패: {1}".format(found, exc))
        return
    text = (out.stdout or "").strip().splitlines()
    bits = text[0] if text else "?"
    if bits != "32":
        add("fail", "브리지 인터프리터(32bit)", "{0} 의 비트가 {1} — 32bit 여야 한다".format(found, bits))
    elif "pywin32-ok" not in (out.stdout or ""):
        add("fail", "브리지 인터프리터(32bit)", "{0} 에 pywin32 없음 (pip install pywin32)".format(found))
    else:
        add("pass", "브리지 인터프리터(32bit)", "{0} / 32bit / pywin32 ok".format(found))


def tasklist_names() -> set:
    try:
        out = subprocess.run([tasklist_path(), "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=60, errors="replace")
    except Exception:
        return set()
    names = set()
    for line in (out.stdout or "").splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if parts and parts[0]:
            names.add(parts[0].strip('"').lower().removesuffix(".exe"))
    return names


def check_creon(names: set) -> None:
    host = sorted(set(names) & set(CREON_HOST))
    updater = sorted(set(names) & set(CREON_UPDATER))
    if updater:
        add("warn", "Creon 세션",
            "업데이터 진행 중({0}) — 패치가 끝나야 로그인 가능. 브리지는 의도적으로 내려간다".format(
                ",".join(updater)))
    elif host:
        add("pass", "Creon 세션", "호스트 프로세스 실행 중: {0}".format(",".join(host)))
    else:
        add("fail", "Creon 세션", "Creon 호스트 프로세스가 없다 — Creon PLUS 실행+로그인 필요")


def check_bridge(base: str) -> None:
    health = balance = positions = None
    try:
        health = http_json(base.rstrip("/") + "/health")
    except Exception as exc:  # noqa: BLE001
        if is_wsl():
            add("warn", "브리지 /health",
                "{0} 도달 불가({1}) — WSL에서는 Windows 루프백 브리지에 직접 닿지 않는다. "
                "Windows 파이썬으로 다시 점검하라".format(base, exc))
        else:
            add("fail", "브리지 /health", "{0} 도달 불가: {1}".format(base, exc))
        return
    if not health.get("connected"):
        add("fail", "브리지 /health", "connected=false — Creon 로그인/브리지 재시작 필요")
        return
    add("pass", "브리지 /health", "connected=true")
    try:
        balance = http_json(base.rstrip("/") + "/balance").get("balance", {})
        add("pass", "브리지 /balance",
            "equity={0:,} cash={1:,} positions={2}".format(
                int(balance.get("equity") or 0), int(balance.get("cash") or 0),
                balance.get("positions_count")))
    except Exception as exc:  # noqa: BLE001
        add("fail", "브리지 /balance", "조회 실패: {0} (반쪽 세션 의심 — 재로그인)".format(exc))
    try:
        positions = http_json(base.rstrip("/") + "/positions").get("positions", [])
        add("pass", "브리지 /positions", "{0}건".format(len(positions)))
    except Exception as exc:  # noqa: BLE001
        add("warn", "브리지 /positions", "조회 실패: {0}".format(exc))


def check_feed(cfg) -> None:
    source = str(getattr(cfg, "feed_source", "url")).lower()
    if source == "rank":
        add("pass", "피드", "rank 모드 — Creon 랭킹으로 후보 생성(외부 피드 불필요)")
        return
    url = getattr(cfg, "feed_url", "")
    try:
        doc = http_json(url)
    except Exception as exc:  # noqa: BLE001
        add("fail", "피드", "{0} 도달 불가: {1}".format(url, exc))
        return
    generated = str(doc.get("generated_at") or "")
    age_h = None
    try:
        stamp = datetime.fromisoformat(generated.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=KST)
        age_h = (datetime.now(KST) - stamp).total_seconds() / 3600.0
    except ValueError:
        pass
    counts = {k: len((v.get("items") if isinstance(v, dict) else v) or [])
              for k, v in (doc.get("candidates") or {}).items()}
    total = sum(counts.values())
    max_age_h = float(getattr(cfg, "feed_max_age", 86400)) / 3600.0
    detail = "generated_at={0} (age {1}) 후보 {2}".format(
        generated or "?", ("{0:.1f}h".format(age_h) if age_h is not None else "?"), counts)
    if total == 0:
        add("warn", "피드", "후보 0건 — " + detail)
    elif age_h is not None and age_h > max_age_h:
        add("warn", "피드", "오래됨(상한 {0:.1f}h) — {1}".format(max_age_h, detail))
    else:
        add("pass", "피드", detail)


def check_news_gate(cfg) -> None:
    url = (getattr(cfg, "news_gate_url", "") or "").strip()
    if not url:
        add("pass", "뉴스 게이트", "미설정(비활성) — 진입 게이트 없음")
        return
    try:
        code = http_status(url)
        add("pass", "뉴스 게이트", "{0} 응답(HTTP {1}) — 살아있음".format(url, code))
        return
    except Exception as exc:  # noqa: BLE001
        closed = bool(getattr(cfg, "news_gate_fail_closed", True))
        add("fail" if closed else "warn", "뉴스 게이트",
            "{0} 도달 불가: {1}{2}".format(url, exc,
                                          " — fail-closed 라 모든 진입이 막힌다" if closed else ""))


def check_paths(cfg) -> None:
    journal = str(getattr(cfg, "journal_path", "journal/trade_journal.sqlite3"))
    parent = os.path.dirname(os.path.abspath(journal)) or "."
    os.makedirs(parent, exist_ok=True)
    try:
        probe = os.path.join(parent, ".preflight_write_test")
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("ok")
        os.unlink(probe)
        add("pass", "저널 경로", "{0} (쓰기 가능)".format(journal))
    except OSError as exc:
        add("fail", "저널 경로", "{0} 쓰기 불가: {1}".format(parent, exc))
    kill = str(getattr(cfg, "kill_switch_path", "kill_switch.txt"))
    if os.path.exists(kill):
        add("warn", "킬스위치", "{0} 존재 — 루프는 주문하지 않는다(정지 상태)".format(kill))
    else:
        add("pass", "킬스위치", "없음(정상)")


def check_risk(cfg) -> None:
    invested = float(getattr(cfg, "max_invested_pct", 0) or 0)
    per_stock = float(getattr(cfg, "max_position_pct", 0) or 0)
    dry = bool(getattr(cfg, "dry_run", True))
    daily = getattr(cfg, "daily_max_trades", "?")
    detail = "총노출 {0:.0%} / 종목당 {1:.0%} / 일일 {2}건 / dry_run={3}".format(
        invested, per_stock, daily, dry)
    if invested > 0.5 or per_stock > 0.2:
        add("warn", "위험 한도", detail + " — 신규 운영자에겐 높다(권장: 30% / 10%, --preset paper_safe)")
    else:
        add("pass", "위험 한도", detail)
    if not dry:
        add("warn", "실주문 모드", "dry_run=false — 주문이 실제로 나간다(모의계좌인지 확인)")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="preflight.py", description=__doc__)
    parser.add_argument("--preset", default=None, help="config/preset 이름")
    parser.add_argument("--config", default=None, help="JSON 설정 파일")
    parser.add_argument("--feed", default=None, help="피드 URL 일회성 지정")
    parser.add_argument("--bridge", default=None, help="브리지 URL 일회성 지정")
    parser.add_argument("--json", action="store_true", help="JSON 출력")
    args = parser.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    from runner.config import RunnerConfig
    from trader_core.presets import load_preset

    merged = dict(RunnerConfig().as_dict())
    if args.preset:
        try:
            merged.update(load_preset(args.preset))
        except (OSError, ValueError) as exc:
            print("config error: {0}".format(exc), file=sys.stderr)
            return 3
    if args.config:
        merged.update(RunnerConfig.from_file(args.config).as_dict())
    merged.update(RunnerConfig.env_overrides())
    if args.bridge:
        merged["bridge_base_url"] = args.bridge
    if args.feed:
        if str(args.feed).strip().lower() == "rank":
            merged["feed_source"] = "rank"
        else:
            merged["feed_url"] = args.feed
    cfg = RunnerConfig.from_mapping(merged)
    engine = cfg.engine_config()

    names = tasklist_names()
    check_interpreter()
    check_py32()
    check_creon(names)
    check_bridge(str(engine.bridge_base_url))
    check_feed(engine)
    check_news_gate(cfg)
    check_paths(engine)
    check_risk(engine)

    fails = [r for r in results if r[0] == "fail"]
    warns = [r for r in results if r[0] == "warn"]
    if args.json:
        print(json.dumps({"results": [{"status": s, "check": n, "detail": d} for s, n, d in results],
                          "fails": len(fails), "warns": len(warns)}, ensure_ascii=False, indent=1))
    else:
        marks = {"pass": "OK  ", "warn": "WARN", "fail": "FAIL"}
        for status, name, detail in results:
            print("[{0}] {1}: {2}".format(marks[status], name, detail))
        print("")
        print("요약: 실패 {0} · 경고 {1}".format(len(fails), len(warns)))
        if fails:
            print("→ 실패 항목을 해결하기 전에는 --live 로 띄우지 마라.")
    return 3 if fails else (2 if warns else 0)


if __name__ == "__main__":
    sys.exit(main())

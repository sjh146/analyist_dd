#!/usr/bin/env python3
"""고객 PC: 일일 텔레메트리를 서버로 보낸다(로그 요약·상태·체결 수치).

개인정보·계좌번호는 보내지 않는다 — 계좌번호/주문번호는 마스킹하고 집계 수치만 보낸다.
사용: python3 telemetry.py --config config.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

ACCOUNT_RE = re.compile(r"\b\d{8,}-\d{2,}\b")
ACCOUNT_PLAIN_RE = re.compile(r"\b\d{10,14}\b")
ACCOUNT_CTX_RE = re.compile(r"(계좌\s*번호?|acct(?:ount)?\s*(?:no|num)?)[^\d]{0,8}(\d{6,14})", re.I)


def now_kst() -> str:
    return datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="seconds")


def mask(text: str) -> str:
    out = ACCOUNT_RE.sub("***-****", text)
    out = ACCOUNT_CTX_RE.sub(lambda m: m.group(1) + " ***-****", out)
    return ACCOUNT_PLAIN_RE.sub("***-****", out)


def tail(path: str, lines: int = 200) -> list[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return [mask(x.rstrip("\n")) for x in fh.readlines()[-lines:]]
    except OSError:
        return []


def load_json(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def collect(cfg: dict) -> dict:
    app_dir = cfg["app_dir"]
    logs = cfg.get("log_dir") or app_dir
    state = load_json(os.path.join(logs, "loop_state.json")) or {}
    daily = state.get("daily") or {}
    checks = state.get("check") or {}
    guard = checks.get("bear_guard") or {}
    return {
        "customer_id": cfg["customer_id"],
        "version": str(cfg.get("version") or ""),
        "collected_at": now_kst(),
        "payload": {
            "daily": {"date": daily.get("date"), "entries": daily.get("entries"),
                      "start_equity": daily.get("start_equity"),
                      "closed": daily.get("closed")},
            "guard": {"active": guard.get("active"), "level": guard.get("level"),
                      "reason": guard.get("reason")},
            "check": {"consecutive_failures": checks.get("consecutive_failures"),
                      "last_error": mask(str(checks.get("last_error") or ""))[:300]},
            "runner_log_tail": tail(os.path.join(logs, "runner.log")),
        },
    }


def send(cfg: dict, body: dict, timeout: float = 20.0) -> dict:
    url = cfg["base_url"].rstrip("/") + "/v1/telemetry"
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Authorization": "Bearer " + cfg["token"], "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="텔레메트리 전송")
    ap.add_argument("--config", default="config.json")
    args = ap.parse_args(argv)
    with open(args.config, encoding="utf-8") as fh:
        cfg = json.load(fh)
    try:
        out = send(cfg, collect(cfg))
        print(json.dumps(out, ensure_ascii=False))
        return 0
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print("telemetry 실패: {0}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

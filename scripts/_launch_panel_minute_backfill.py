#!/usr/bin/env python3
"""미수집 패널 분봉 백필을 세션과 무관하게 띄운다(start_new_session).

WHY: Hermes 세션 정리(SIGKILL)가 셸 자식까지 죽인다. 구동기 start_background() 와 같은 방식으로
떼어 놓아야 틱이 끝나도 백필이 계속 돈다. 로그는 파일로만 남긴다(비블로킹).
"""
from __future__ import annotations

import os
import subprocess
import sys

PROJ = "/home/jhshi/analyist_dd"
LOG = os.path.join(PROJ, "reports", "panel_minute_backfill_20261123b.log")
CODES = os.path.join(PROJ, "reports", "minute_codes_panel_missing_20261123.txt")


def env_from_dotenv(path: str) -> dict:
    env = dict(os.environ)
    try:
        fh = open(path, encoding="utf-8")
    except OSError as exc:
        print(f"WARN: .env 읽기 실패({exc}) — 기본 env 로 진행")
        env["POSTGRES_HOST"] = "127.0.0.1"
        env["POSTGRES_PORT"] = "5434"
        return env
    with fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    env["POSTGRES_HOST"] = "127.0.0.1"
    env["POSTGRES_PORT"] = "5434"
    return env


def main() -> int:
    if not os.path.exists(CODES):
        print("STOP: codes file 없음:", CODES)
        return 2
    with open(CODES, encoding="utf-8") as f:
        n = sum(1 for ln in f if ln.strip())
    log = open(LOG, "a", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-u", os.path.join(PROJ, "scripts", "naver_minute_backfill.py"),
         "--from", "2026-11-10", "--to", "2026-11-20",
         "--codes-file", CODES, "--sleep", "0.4"],
        cwd=PROJ, env=env_from_dotenv(os.path.join(PROJ, ".env")),
        stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    print(f"started pid={proc.pid} codes={n} log={LOG}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

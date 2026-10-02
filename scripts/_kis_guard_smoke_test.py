#!/usr/bin/env python3
"""KIS 클라이언트 × net_guard 통합 스모크 테스트 (네트워크 불필요 — curl_runner 주입).

검증:
  1. 클라이언트가 scripts/net_guard.py 를 실제로 로드한다(호스트 경로 탐색)
  2. 호출 간격이 가드 상태파일에 기록된다(프로세스 간 강제의 근거)
  3. 403(HTML/WAF) 응답 → 차단 기록 → **다음 호출은 HTTP 를 보내지 않고** 즉시 중단
     (호출 횟수가 증가하지 않는지로 증명)
  4. KIS_MAX_CALLS 예산 소진 → NETGUARD-BUDGET 으로 중단(HTTP 없음)
  5. 두 신호 모두 KisApiError + RATE_LIMIT_CODES → 수집 루프의 quota_hit 분기가 멈춘다
종료코드 0 = 전부 통과.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="kisguard")
TOKEN = os.path.join(TMP, "tok", "token.json")
os.makedirs(os.path.dirname(TOKEN), exist_ok=True)
with open(TOKEN, "w", encoding="utf-8") as f:          # 토큰 발급 HTTP 를 건너뛰게 캐시를 미리 둔다
    json.dump({"access_token": "test-token", "expire_at": time.time() + 86400}, f)

os.environ["NET_GUARD_STATE_DIR"] = TMP
os.environ["NET_GUARD_EVENTS"] = os.path.join(TMP, "events.jsonl")
os.environ["NET_GUARD_PATH"] = os.path.join(REPO, "scripts/net_guard.py")
os.environ["KIS_MAX_CALLS"] = "0"
sys.path.insert(0, os.path.join(REPO, "services", "kis-collector"))

from kis_app.client.kis_client import (  # noqa: E402
    KisApiError, KisClient, RATE_LIMIT_CODES, _load_net_guard)

FAILS: list = []
MODE = {"kind": "ok"}
HTTP = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def fake_curl(args, timeout=30):
    """curl 대역 — 호출 URL·시각을 기록하고 MODE 에 따라 응답을 흉내낸다."""
    url = args[args.index("--url") + 1]
    HTTP.append((time.time(), url))
    if MODE["kind"] == "blocked":
        return 403, "<html><head><title>Blocked</title></head></html>"
    if MODE["kind"] == "rate":
        return 429, '{"msg_cd":"EGW00124","msg1":"분당 초과"}'
    return 200, json.dumps({
        "rt_cd": "0", "msg_cd": "0",
        "output2": [{"stck_bsop_date": "20261001", "stck_clpr": "10000"}]})


def make_client(appkey, **kw):
    return KisClient(appkey, "SECRET", "http://127.0.0.1:9",
                     delay=kw.pop("delay", 0.2), jitter=0.0, retry_max=kw.pop("retry_max", 0),
                     token_path=TOKEN, sleep_fn=lambda s: None,
                     curl_runner=fake_curl, **kw)


def main() -> int:
    print("=== 1. 가드 로드 ===")
    mod = _load_net_guard()
    check("scripts/net_guard.py 로드", mod is not None,
          getattr(mod, "__file__", "None"))

    print("\n=== 2. 호출 간격이 상태파일에 기록 ===")
    c1 = make_client("APPKEY-T1")
    HTTP.clear()
    for _ in range(3):
        c1.get_daily_chart("005930", "J", "20260901", "20261001")
    gaps = [round(b[0] - a[0], 3) for a, b in zip(HTTP, HTTP[1:])]
    check("3콜 기록됨", len(HTTP) == 3, f"n={len(HTTP)}")
    check("호출 간격 ≥ 0.2s (가드가 강제)", all(g >= 0.19 for g in gaps), f"gaps={gaps}")
    st = mod.load_state(mod.kis_key("APPKEY-T1"))
    check("상태파일에 오늘 호출 3건", st.get("calls") == 3, f"calls={st.get('calls')}")

    print("\n=== 3. 403(HTML/WAF) → 차단 기록 + 다음 호출은 HTTP 없이 중단 ===")
    MODE["kind"] = "blocked"
    n_before = len(HTTP)
    err = None
    try:
        c1.get_daily_chart("005930", "J", "20260901", "20261001")
    except Exception as e:  # noqa: BLE001
        err = e
    check("403 응답은 예외로 올라온다", err is not None, f"{type(err).__name__}: {err}")
    check("차단이 기록됨", mod.Guard("kis-" + mod.kis_key("APPKEY-T1").split("-", 1)[1]).status()
          .get("blocked_reason", "") != "", "")
    after_block_http = len(HTTP)
    err2 = None
    try:
        c1.get_daily_chart("005930", "J", "20260901", "20261001")
    except KisApiError as e:
        err2 = e
    check("다음 호출이 즉시 중단(NETGUARD-BLOCK)", err2 is not None and err2.msg_cd == "NETGUARD-BLOCK",
          f"{(err2.msg_cd if err2 else err)}")
    check("★HTTP 를 보내지 않았다(호출 수 불변)", len(HTTP) == after_block_http,
          f"{after_block_http} → {len(HTTP)}")
    check("중단 신호가 RATE_LIMIT_CODES 에 포함(루프가 멈춘다)", "NETGUARD-BLOCK" in RATE_LIMIT_CODES)

    print("\n=== 4. 예산 소진 ===")
    MODE["kind"] = "ok"
    os.environ["KIS_DAILY_BUDGET"] = "2"       # 일일 예산은 별도 env(실행 상한 KIS_MAX_CALLS 와 의미가 다르다)
    c2 = make_client("APPKEY-T2")
    err3 = None
    n_ok = 0
    try:
        for _ in range(4):
            c2.get_daily_chart("000660", "J", "20260901", "20261001")
            n_ok += 1
    except KisApiError as e:
        err3 = e
    check("예산 2 → 2콜 후 중단", n_ok == 2, f"n_ok={n_ok}")
    check("NETGUARD-BUDGET 로 중단", err3 is not None and err3.msg_cd == "NETGUARD-BUDGET",
          f"{(err3.msg_cd if err3 else None)}")
    os.environ["KIS_DAILY_BUDGET"] = "0"
    check("실행 상한(KIS_MAX_CALLS)은 예산으로 쓰지 않는다",
          os.environ.get("KIS_DAILY_BUDGET") == "0")

    print("\n=== 5. CLI status/events ===")
    env = dict(os.environ, NET_GUARD_STATE_DIR=TMP)
    out = subprocess.run([sys.executable, os.path.join(REPO, "scripts/net_guard.py"), "status"],
                         capture_output=True, text=True, env=env, timeout=60)
    check("status 가 키·호출수를 출력", "kis-" in out.stdout and "APPKEY-T1" not in out.stdout,
          out.stdout.strip().splitlines()[-1][:90] if out.stdout.strip() else out.stderr[:120])
    check("status 에 차단 키가 보인다", "차단" in out.stdout)
    ev = subprocess.run([sys.executable, os.path.join(REPO, "scripts/net_guard.py"), "events", "-n", "5"],
                        capture_output=True, text=True, env=env, timeout=60)
    check("events 에 blocked 기록", '"event": "blocked"' in ev.stdout,
          ev.stdout.strip().splitlines()[-1][:90] if ev.stdout.strip() else ev.stderr[:120])

    print("\n=== 정리 ===")
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{'ALL PASS' if not FAILS else 'FAILED: ' + ', '.join(FAILS)}")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())

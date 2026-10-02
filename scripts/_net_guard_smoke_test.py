#!/usr/bin/env python3
"""net_guard 스모크 테스트 (네트워크 불필요).

검증하는 것 — 이 모듈이 주장하는 '프로세스 경계를 넘는' 보호가 실제로 성립하는가:
  1. classify: 403/HTML=blocked, 429/5xx=transient, 200/404=ok
  2. 단일 프로세스 간격: delay=0.25 로 4회 → 간격 ≥ 0.25
  3. **프로세스 간 간격**: 3개 프로세스 × 4회(같은 키) → 합친 타임스탬프의 최소 간격 ≥ 0.25
     (가드 없이는 3배로 몰린다 → 대조군으로 DISABLE 실행도 함께 측정해 테스트가 비어있지 않음을 증명)
  4. 차단 쿨다운: 한 프로세스가 403 을 기록하면 **다른 프로세스**의 acquire 가 Blocked 로 즉시 중단
  5. 예산: budget=3 → 4번째 acquire 가 BudgetExhausted
  6. fail-open: 상태 디렉터리를 못 쓰면 예외 없이 진행(수집을 막지 않는다)
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

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import net_guard  # noqa: E402

WORKER = r"""
import os, sys, time, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) if False else %(here)r)
import net_guard as ng
key, n, delay, out = sys.argv[1], int(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
g = ng.Guard(key, delay=delay, jitter=0.0, enabled=False if os.environ.get('NG_OFF') else None)
ts = []
for _ in range(n):
    g.acquire()
    ts.append(time.time())
open(out, 'w').write(json.dumps(ts))
"""

FAILS = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def use_state(path):
    """상태·이벤트 경로를 임시 디렉터리로 돌린다.

    주의: 모듈 전역 `STATE_DIR` 은 **임포트 시점**에 env 를 읽는다. 테스트가 중간에
    os.environ 을 바꿔도 전역은 그대로라, 전역까지 함께 바꿔야 자식 프로세스와 같은
    디렉터리를 본다(이걸 빠뜨리면 부모는 실제 상태 디렉터리에 쓴다 — 실측).
    """
    os.makedirs(path, exist_ok=True)
    os.environ["NET_GUARD_STATE_DIR"] = path
    net_guard.STATE_DIR = path
    net_guard.EVENTS = os.path.join(path, "events.jsonl")


def run_workers(state_dir, key, n, delay, procs=3, off=False):
    env = dict(os.environ, NET_GUARD_STATE_DIR=state_dir, NET_GUARD_FAIL_OPEN="1")
    if off:
        env["NG_OFF"] = "1"
    else:
        env.pop("NG_OFF", None)
    tmp = tempfile.mkdtemp(prefix="ngw")
    script = WORKER % {"here": HERE}
    procs_ = [subprocess.Popen([sys.executable, "-c", script, key, str(n), str(delay),
                                os.path.join(tmp, f"{i}.json")], env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
              for i in range(procs)]
    ts = []
    for p in procs_:
        _, err = p.communicate(timeout=120)
        if p.returncode != 0:
            raise RuntimeError(f"worker 실패: {err.decode()[:300]}")
    for i in range(procs):
        ts += json.load(open(os.path.join(tmp, f"{i}.json")))
    shutil.rmtree(tmp, ignore_errors=True)
    return sorted(ts)


def main() -> int:
    print("=== 1. classify ===")
    cases = [
        ("403 html", 403, "<html>blocked</html>", "blocked"),
        ("403 json", 403, '{"msg":"forbidden"}', "blocked"),
        ("401", 401, "{}", "blocked"),
        ("429", 429, '{"msg":"too many"}', "transient"),
        ("503", 503, "", "transient"),
        ("200", 200, "{}", "ok"),
        ("404", 404, '{"respMsg":"no such path"}', "ok"),
        ("html 200", 200, "<!DOCTYPE html>", "blocked"),
    ]
    for name, st, body, want in cases:
        got = net_guard.classify(st, body)
        check(f"classify {name} → {want}", got == want, f"got={got}")

    print("\n=== 2. 단일 프로세스 간격 (delay=0.25) ===")
    d1 = tempfile.mkdtemp(prefix="ng1")
    use_state(d1)
    g = net_guard.Guard("t-single", delay=0.25, jitter=0.0)
    ts = []
    for _ in range(4):
        g.acquire()
        ts.append(time.time())
    gaps = [round(b - a, 3) for a, b in zip(ts, ts[1:])]
    check("4회 간격 ≥ 0.25", all(x >= 0.24 for x in gaps), f"gaps={gaps}")

    print("\n=== 3. 프로세스 간 간격 (3 프로세스 × 4회) ===")
    d3 = tempfile.mkdtemp(prefix="ng3")
    use_state(d3)
    env = dict(os.environ, NET_GUARD_STATE_DIR=d3, NET_GUARD_FAIL_OPEN="1")
    merged = run_workers(d3, "t-multi", 4, 0.25, procs=3)
    gaps_on = [round(b - a, 3) for a, b in zip(merged, merged[1:])]
    first, last = merged[0], merged[-1]
    span_on = last - first
    check("12콜 최소 간격 ≥ 0.24 (가드 ON)", all(x >= 0.24 for x in gaps_on),
          f"min={min(gaps_on) if gaps_on else None} n={len(merged)}")
    check("총 소요 ≥ 11×0.25 (직렬화됨)", span_on >= 11 * 0.25 * 0.9,
          f"span={span_on:.2f}s")
    merged_off = run_workers(d3 + "-off", "t-multi", 4, 0.25, procs=3, off=True)
    gaps_off = [round(b - a, 3) for a, b in zip(merged_off, merged_off[1:])]
    check("대조군(가드 OFF)은 간격이 무너진다", min(gaps_off) < 0.15,
          f"min off={min(gaps_off):.3f} (테스트가 비어있지 않음 증명)")

    print("\n=== 4. 차단 쿨다운이 다른 프로세스로 전파 ===")
    d4 = tempfile.mkdtemp(prefix="ng4")
    use_state(d4)
    gb = net_guard.Guard("t-block", delay=0.0, jitter=0.0)
    gb.acquire()
    gb.note(403, "<html>WAF</html>")
    st = gb.status()
    check("차단 상태 기록됨", st.get("blocked_now") is True, f"reason={st.get('blocked_reason')}")
    code = (
        "import os,sys,json;sys.path.insert(0,%r);import net_guard as ng;"
        "g=ng.Guard('t-block',delay=0.0);"
        "import contextlib\n"
        "try:\n    g.acquire();print('ACQUIRED')\n"
        "except ng.Blocked as e:\n    print('BLOCKED')\n" % HERE
    )
    out = subprocess.run([sys.executable, "-c", code], env=dict(env, NET_GUARD_STATE_DIR=d4),
                         capture_output=True, text=True, timeout=60)
    check("다른 프로세스가 즉시 중단", "BLOCKED" in out.stdout, f"stdout={out.stdout.strip()[:60]} {out.stderr[:120]}")
    gb.clear("테스트")
    check("해제 후 다시 통과", net_guard.Guard("t-block").status().get("blocked_now") is False)

    print("\n=== 5. 예산 ===")
    d5 = tempfile.mkdtemp(prefix="ng5")
    use_state(d5)
    g5 = net_guard.Guard("t-budget", delay=0.0, budget=3)
    n_ok = 0
    try:
        for _ in range(5):
            g5.acquire()
            n_ok += 1
    except net_guard.BudgetExhausted as e:
        print(f"      BudgetExhausted: {e}")
    check("예산 3 → 정확히 3콜 후 중단", n_ok == 3, f"n_ok={n_ok}")

    print("\n=== 6. fail-open (상태 디렉터리 쓰기 불가) ===")
    bad = "/proc/definitely/not/writable"
    os.environ["NET_GUARD_STATE_DIR"] = bad
    net_guard.STATE_DIR = bad
    net_guard.EVENTS = bad + "/events.jsonl"
    g6 = net_guard.Guard("t-failopen", delay=0.1)
    try:
        g6.acquire()
        g6.note(200, "{}")
        check("쓰기 불가여도 예외 없이 진행", True)
    except Exception as e:  # noqa: BLE001
        check("쓰기 불가여도 예외 없이 진행", False, repr(e)[:120])

    print("\n=== 정리 ===")
    for d in (d1, d3, d3 + "-off", d4, d5):
        shutil.rmtree(d, ignore_errors=True)
    os.environ.pop("NET_GUARD_STATE_DIR", None)

    print(f"\n{'ALL PASS' if not FAILS else 'FAILED: ' + ', '.join(FAILS)}")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())

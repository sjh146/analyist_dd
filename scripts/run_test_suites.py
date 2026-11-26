#!/usr/bin/env python3
"""테스트 스위트 자동 실행·기록 (2026-11-23).

목적: 3,000개 가까운 테스트가 저장소에 있는데 상시 실행 경로가 없었다.
      스위트별로 돌려 통과/실패/소요를 이력으로 남기고, 회귀(이전 통과 → 지금 실패)를 표시한다.

설계:
  · 스위트 = pytest 를 돌릴 수 있는 디렉터리(discover). py 파일이 없으면 건너뛴다.
  · 각 스위트는 상한 시간(timeout) 안에서만 실행하고, 실패해도 다음 스위트를 계속한다.
  · 결과는 data/reports/test_runs/latest.json + history.jsonl 에 남긴다.
  · 정상(전부 통과 + 회귀 없음)이면 한 줄, 문제가 있으면 실패 스위트와 원인을 출력한다.

사용:
  python scripts/run_test_suites.py                # 전체
  python scripts/run_test_suites.py --only tests   # 이름 필터
  python scripts/run_test_suites.py --quick        # 회귀 판정만(요약 출력 최소)
종료코드: 0 정상 · 1 실패 스위트 있음 · 2 회귀 발견
"""
from __future__ import annotations
import argparse, json, os, re, subprocess, sys, time
from datetime import datetime

PROJ = os.environ.get("PROJ_DIR", "/home/jhshi/analyist_dd")
OUT_DIR = os.path.join(PROJ, "data/reports/test_runs")
LATEST = os.path.join(OUT_DIR, "latest.json")
HISTORY = os.path.join(OUT_DIR, "history.jsonl")
HOST_PY = "/usr/bin/python3"
WIN_PY = "/mnt/c/Users/jhshi/Python312-64/python.exe"
WIN_TA = "C:/Users/jhshi/trader-agent"

def load_env() -> None:
    """저장소 .env 를 스스로 읽는다 (2026-11-23).

    WHY: 이 러너를 수동 실행하면 POSTGRES_PASSWORD 가 없어 DB 의존 테스트가 실패하고,
    같은 코드가 4건/5건으로 다르게 보고된다(실측). 크론 래퍼만 .env 를 읽어서
    "야간은 4건, 수동은 5건"이라는 혼동이 생겼다. 러너가 직접 읽고 호스트 매핑
    포트(127.0.0.1:5434)를 강제해 판정을 한 가지로 만든다.
    """
    path = os.path.join(PROJ, ".env")
    try:
        fh = open(path, encoding="utf-8")
    except OSError:
        fh = None
    if fh is not None:
        with fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
    os.environ["POSTGRES_HOST"] = "127.0.0.1"
    os.environ["POSTGRES_PORT"] = "5434"


SUITES = [
    {"name": "root-tests", "dir": "tests", "python": HOST_PY, "timeout": 900},
    {"name": "api-gateway", "dir": "services/api-gateway/tests", "python": HOST_PY, "timeout": 600},
    {"name": "trader-core", "dir": "/mnt/c/Users/jhshi/trader-agent/trader_core/tests", "python": WIN_PY, "timeout": 900},
    {"name": "trader-runner", "dir": "/mnt/c/Users/jhshi/trader-agent/runner/tests", "python": WIN_PY, "timeout": 600},
    {"name": "trader-bridge", "dir": "/mnt/c/Users/jhshi/trader-agent/bridge/tests", "python": WIN_PY, "timeout": 600},
]


def parse_summary(stdout: str) -> dict:
    res = {"passed": None, "failed": None, "errors": None, "skipped": None}
    tail = "\n".join(stdout.strip().splitlines()[-12:])
    for key, pat in (("passed", r"(\d+)\s+passed"), ("failed", r"(\d+)\s+failed"),
                     ("errors", r"(\d+)\s+errors?"), ("skipped", r"(\d+)\s+skipped")):
        m = re.search(pat, tail)
        if m:
            res[key] = int(m.group(1))
    for key, pat in (("failed", r"(\d+)\s+failed"), ("passed", r"(\d+)\s+passed")):
        m = re.search(pat, tail)
        if m and res.get(key) is None:
            res[key] = int(m.group(1))
    return res


def run_suite(suite: dict) -> dict:
    d = suite["dir"] if os.path.isabs(suite["dir"]) else os.path.join(PROJ, suite["dir"])
    if not os.path.isdir(d):
        return {"name": suite["name"], "suite_skipped": "디렉터리 없음"}
    py_files = [f for f in os.listdir(d) if f.endswith(".py")]
    if not py_files:
        return {"name": suite["name"], "suite_skipped": "테스트 파일 없음"}
    test_arg = d.replace("/mnt/c/", "C:/") if suite["python"].startswith("/mnt/c/") else d
    cmd = [suite["python"], "-m", "pytest", "-q", "-p", "no:cacheprovider", test_arg]
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=suite["timeout"], cwd=PROJ)
        out = (r.stdout or "") + (r.stderr or "")
        summary = parse_summary(out)
        return {
            "name": suite["name"], "rc": r.returncode, "seconds": round(time.time() - t0, 1),
            **summary, "tail": out.strip().splitlines()[-3:] if r.returncode else [],
        }
    except subprocess.TimeoutExpired:
        return {"name": suite["name"], "rc": -1, "seconds": suite["timeout"], "timeout": True,
                "tail": [f"timeout {suite['timeout']}s"]}
    except OSError as e:
        return {"name": suite["name"], "rc": -2, "error": str(e)}


def run_with_retries(suite: dict, retries: int) -> dict:
    """실패한 스위트만 *retries* 회 재실행 — 한 번이라도 통과하면 flaky 로 표시.

    2026-11-23 사용자 지시(야간 3회 반복). 전 스위트를 3회 돌리면 시간이 3배가 되므로
    실패한 것만 재시도한다(정상 야간은 131s 그대로).
    """
    result = run_suite(suite)
    bad = result.get("rc", 0) != 0 or (result.get("failed") or 0) > 0
    if not bad or retries <= 0 or result.get("suite_skipped"):
        result["attempts"] = 1
        return result
    attempts = 1
    for _ in range(int(retries)):
        attempts += 1
        retry = run_suite(suite)
        retry_bad = retry.get("rc", 0) != 0 or (retry.get("failed") or 0) > 0
        if not retry_bad:
            retry["attempts"] = attempts
            retry["flaky"] = True
            return retry
        result = retry
    result["attempts"] = attempts
    result["flaky"] = False
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--retries", type=int, default=2,
                    help="실패 스위트 재실행 횟수(기본 2 → 최대 3회 시도, 플래키 감지)")
    args = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)
    load_env()

    suites = [s for s in SUITES if not args.only or any(o in s["name"] for o in args.only)]
    results = [run_with_retries(s, args.retries) for s in suites]

    prev = {}
    if os.path.exists(LATEST):
        try:
            prev = {r["name"]: r for r in json.load(open(LATEST, encoding="utf-8")).get("results", [])}
        except (OSError, json.JSONDecodeError):
            prev = {}

    regressions = []
    for r in results:
        if r.get("suite_skipped"):
            continue
        before = prev.get(r["name"], {})
        now_bad = r.get("rc", 0) != 0 or (r.get("failed") or 0) > 0 or (r.get("errors") or 0) > 0
        was_good = (before.get("rc", 1) == 0) and not (before.get("failed") or 0)
        if now_bad and was_good:
            regressions.append(r["name"])

    payload = {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "results": results,
        "regressions": regressions,
        "totals": {
            "passed": sum(r.get("passed") or 0 for r in results),
            "failed": sum(r.get("failed") or 0 for r in results),
            "errors": sum(r.get("errors") or 0 for r in results),
            "suites_ok": sum(1 for r in results if not r.get("suite_skipped") and r.get("rc") == 0),
            "suites_bad": sum(1 for r in results if not r.get("suite_skipped") and r.get("rc") != 0),
            "seconds": round(sum(r.get("seconds") or 0 for r in results), 1),
        },
    }
    json.dump(payload, open(LATEST, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    with open(HISTORY, "a", encoding="utf-8") as f:
        f.write(json.dumps({k: payload[k] for k in ("checked_at", "totals", "regressions")},
                           ensure_ascii=False) + "\n")

    t = payload["totals"]
    bad = [r for r in results if not r.get("suite_skipped") and r.get("rc") != 0]
    if not bad and not regressions:
        print(f"[테스트] {t['suites_ok']}스위트 통과 · {t['passed']:,}개 통과 · {t['seconds']}s")
        sys.exit(0)

    print(f"[테스트] 통과 {t['suites_ok']} / 실패 {t['suites_bad']} · {t['passed']:,} passed "
          f"{t['failed']} failed {t['errors']} errors · {t['seconds']}s")
    flaky = [r["name"] for r in results if r.get("flaky")]
    if flaky:
        print(f"  ⚠ 플래키(재시도에서 통과): {', '.join(flaky)}")
    if regressions:
        print(f"  ⚠ 회귀(이전 통과 → 지금 실패): {', '.join(regressions)}")
    for r in bad[:6]:
        print(f"  ✗ {r['name']} rc={r.get('rc')} ({r.get('seconds')}s)")
        for line in (r.get("tail") or [])[:2]:
            print(f"     {line[:120]}")
    sys.exit(2 if regressions else 1)


if __name__ == "__main__":
    main()

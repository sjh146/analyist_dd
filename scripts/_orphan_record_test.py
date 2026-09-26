#!/usr/bin/env python3
"""기록 없이 죽은 사이클이 **원장·백로그에 남는지** 검증한다(2026-09-26 U3 사고 재현 테스트).

배경: 사용자가 기차 탑승 전 컴퓨터를 끄면서 종료 준비 스크립트가 우리 사이클(pid 14876)과
wf_label_sweep 자식들을 의도적으로 정지시켰는데, 구동기 프로세스가 함께 SIGKILL 되어 원장에
아무 기록이 없었다. 틱은 한 줄만 출력하고 끝났다 → 그 실행의 정체·진척이 증거로 남지 않았고
scoreboard 의 무효 카운터에도 안 잡혔다.

검증: ME_PROJ 로 임시 프로젝트를 만들어 실제 tick() 을 돌린다(운영 원장을 건드리지 않는다).
  1) 죽은 pid + state.json + 진행률 로그 → tick() 이 원장에 rc=137 '실행실패' 기록을 남긴다
  2) 그 기록에 진행률(detail)과 로그 경로가 들어간다(무엇을·얼마나 했는지 증거)
  3) 백로그 상태가 pending 으로 돌아가고 attempts 에 시도가 남는다(재개 가능)
  4) 같은 죽음에 대해 두 번 기록하지 않는다(멱등 — 이후 틱이 중복 기록하면 원장이 오염된다)
  5) 성능 판정(verdict=노이즈/신호있음)이 붙지 않는다(rc!=0 규칙)

실행: python3 scripts/_orphan_record_test.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _dead_pid():
    """이미 종료된 프로세스의 pid(존재하지 않는 pid 를 안전하게 얻는다)."""
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="me_orphan_test_")
    os.makedirs(os.path.join(tmp, "docs"))
    os.makedirs(os.path.join(tmp, "data/reports/me_cycle/logs"))
    backlog = {"updated_at": "x", "items": [
        {"id": "TST", "title": "테스트 항목", "status": "pending", "priority": 1,
         "command": "true", "est_minutes": 700}]}
    with open(os.path.join(tmp, "docs/QUANT_MODEL_BACKLOG.json"), "w", encoding="utf-8") as f:
        json.dump(backlog, f, ensure_ascii=False)

    os.environ["ME_PROJ"] = tmp
    sys.path.insert(0, os.path.join(PROJ, "scripts"))
    import model_engineer_cycle as m            # noqa: E402 — ME_PROJ 지정 후 import 해야 한다

    m.north_star = lambda role: ""              # 스코어보드 호출은 이 테스트의 대상이 아니다

    piddir = os.path.join(tmp, "data/reports/me_cycle")
    pid = _dead_pid()
    with open(os.path.join(piddir, "running.pid"), "w") as f:
        f.write(str(pid))
    started = m.now_kst().isoformat(timespec="seconds")
    with open(os.path.join(piddir, "state.json"), "w", encoding="utf-8") as f:
        json.dump({"id": "TST", "pid": pid, "started": started}, f)
    with open(os.path.join(piddir, "bg_TST.log"), "w", encoding="utf-8") as f:
        f.write("[x] 실행: TST\n")
    logf = os.path.join(piddir, "logs/me_cycle_TST_20260926-185733.log")
    with open(logf, "w", encoding="utf-8") as f:
        f.write("2026-09-26 10:53:45,158 INFO Build progress: 1400/32626 stock-date pairs (4.3%) "
                "0.42 pair/s ETA 1251min\n")

    fails = []

    def check(name, cond, extra=""):
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}{'' if cond else ' — ' + str(extra)}")
        if not cond:
            fails.append(name)

    print("=== [1] 기록 없이 죽은 사이클 → tick() 이 원장에 남기는가 ===")
    rc = m.tick()
    check("tick rc=0", rc == 0, rc)
    rows = [json.loads(l) for l in open(m.LEDGER, encoding="utf-8") if l.strip()] \
        if os.path.exists(m.LEDGER) else []
    check("원장 기록 1건 생성", len(rows) == 1, rows)
    if rows:
        r = rows[0]
        check("id=TST", r.get("id") == "TST", r.get("id"))
        check("rc=137", r.get("rc") == 137, r.get("rc"))
        check("verdict=실행실패", r.get("verdict") == "실행실패", r.get("verdict"))
        check("성능 판정 미부착", r.get("verdict") not in ("노이즈", "신호있음"), r.get("verdict"))
        check("진행률이 증거로 들어감", "1,400/32,626" in r.get("detail", ""), r.get("detail"))
        check("실행 로그 경로 기록", r.get("log", "").endswith(".log"), r.get("log"))
        check("parsed.rc=137", (r.get("parsed") or {}).get("rc") == 137, r.get("parsed"))
    b = json.load(open(os.path.join(tmp, "docs/QUANT_MODEL_BACKLOG.json"), encoding="utf-8"))
    it = b["items"][0]
    check("백로그 pending 복귀(재개 대상)", it["status"] == "pending", it["status"])
    check("attempts 1건 기록", len(it.get("attempts", [])) == 1, it.get("attempts"))
    check("retry_note 존재", bool(it.get("retry_note")), it.get("retry_note"))

    print("=== [2] 멱등성: 같은 죽음을 두 번 기록하지 않는가 ===")
    # pidfile 은 [1] 에서 이미 지워졌다 → 다시 만들고 같은 id 로 틱을 돌린다.
    with open(os.path.join(piddir, "running.pid"), "w") as f:
        f.write(str(pid))
    with open(os.path.join(piddir, "state.json"), "w", encoding="utf-8") as f:
        json.dump({"id": "TST", "pid": pid, "started": started}, f)
    m.tick()
    rows2 = [json.loads(l) for l in open(m.LEDGER, encoding="utf-8") if l.strip()]
    # [1] 의 기록이 시작 시각 이후로 존재하므로 중복 기록하면 안 된다.
    check("원장 여전히 1건", len(rows2) == 1, len(rows2))

    print("=== [3] 원장 기록이 다른(정상) 실행을 막지 않는가 ===")
    check("PIDFILE 제거됨", not os.path.exists(os.path.join(piddir, "running.pid")))

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n결과: {'전부 PASS' if not fails else 'FAIL ' + str(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

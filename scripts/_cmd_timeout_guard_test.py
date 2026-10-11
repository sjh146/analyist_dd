#!/usr/bin/env python3
"""_cmd_timeout_guard_test — 컨테이너 `timeout` ↔ `est_minutes` 자기모순 가드 검증.

실측 배경(2026-10-11 CG173): 백로그 항목이 `est_minutes: 45`(2700s) 인데 명령에는
`timeout 1800`(30분)이 박혀 있었다 — 스킬 교훈 "est_minutes 만 고치면 반쪽"이 **등록 단계에서**
재발해 30분짜리 10폴드 감사 런이 [c] 후보 패널 50/100일 지점에서 SIGKILL(rc=124)로 소실됐다.
가드는 두 겹이다: ① 틱/현황 경고(등록 실수 조기 발견) ② execute() 가 필요 벽시계로 timeout 을
**올려서** 실행(내리는 방향 금지).

이 테스트가 확인하는 것: 가드가 장식이 아니라 **실제 subprocess 로 넘어가는 명령**을 바꾸는가.

실행: python3 scripts/_cmd_timeout_guard_test.py
"""
import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import model_engineer_cycle as m  # noqa: E402

FAIL = []


def check(name, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        FAIL.append(name)


# CG173 실명령(중첩 셸 quoting 그대로) — 파서가 실제 등록값을 읽는지 확인하는 기준.
AUDIT_CMD = (
    "docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 1800 python -u "
    "scripts/model_metric_protocol_audit.py --models \"[[\\\"cg166\\\",\\\"app/models/champion_cand_cg166\\\"]]\" "
    "--topk 3 --folds 10 --dates-per-fold 10 --out /app/app/reports/cg173_audit_pair_10f.json'"
)
TWO_TIMEOUTS = (AUDIT_CMD.replace("timeout 1800", "timeout 1800")
                + " && OMP_NUM_THREADS=2 timeout 900 python -u -m app.training.champion_promote --dry-run")


def test_parse():
    print("── 파싱 ──")
    check("중첩 셸 안의 timeout 1800 을 읽는다", m.command_timeout_seconds(AUDIT_CMD) == 1800)
    check("timeout 이 여러 개면 최대값(1800)", m.command_timeout_seconds(TWO_TIMEOUTS) == 1800)
    check("timeout 없으면 None", m.command_timeout_seconds("docker exec x python -u foo.py") is None)
    check("빈/None 명령 → None",
          m.command_timeout_seconds("") is None and m.command_timeout_seconds(None) is None)
    # \b 가드: 'runtime 5' 같은 단어 안의 'timeout' 을 상한으로 오독하지 않는다.
    check("'runtime 5' 같은 오탐 없음", m.command_timeout_seconds("echo runtime 5") is None)
    check("숫자 아닌 timeout(x)은 무시", m.command_timeout_seconds("timeout x python a.py") is None)


def test_required():
    print("── 필요 벽시계(est×60×1.5+300) ──")
    check("est 45분 → 4350s", m.required_timeout_seconds(45) == 4350)
    check("est 25분 → 2550s", m.required_timeout_seconds(25) == 2550)
    check("문자열 est 도 처리", m.required_timeout_seconds("10") == 1200)
    check("est 없음/0/음수 → None",
          m.required_timeout_seconds(None) is None and m.required_timeout_seconds(0) is None
          and m.required_timeout_seconds(-3) is None)


def test_widen():
    print("── timeout 상향 ──")
    new, chg = m.widen_command_timeouts(AUDIT_CMD, 4350)
    check("1800 → 4350 로 올라간다", "timeout 4350" in new and "timeout 1800" not in new)
    check("나머지 문자열은 바이트 동일",
          new == AUDIT_CMD.replace("timeout 1800", "timeout 4350"))
    check("변경 내역을 보고한다", chg == [(1800, 4350)])
    # 내리는 방향 금지 — 이미 충분히 긴 상한은 그대로 둔다(운영자가 명시한 상한 존중).
    keep, chg2 = m.widen_command_timeouts("timeout 99999 python a.py", 4350)
    check("긴 timeout 은 내리지 않는다", keep == "timeout 99999 python a.py" and chg2 == [])
    both, chg3 = m.widen_command_timeouts(TWO_TIMEOUTS, 4350)
    check("짧은 것만 각각 올린다(1800·900 → 4350, 2건)",
          both.count("timeout 4350") == 2 and len(chg3) == 2)


def test_effective():
    print("── effective_command_for ──")
    cmd, chg, t, need = m.effective_command_for({"command": AUDIT_CMD, "est_minutes": 45})
    check("자기모순 항목은 상향된 명령을 돌려준다", "timeout 4350" in cmd and t == 1800 and need == 4350)
    cmd2, chg2, t2, need2 = m.effective_command_for({"command": AUDIT_CMD, "est_minutes": 5})
    check("est 5분(필요 750s)엔 1800s 로 충분 → 원본 유지", cmd2 == AUDIT_CMD and chg2 == [])
    cmd3, _, _, _ = m.effective_command_for({"command": "docker exec x python a.py", "est_minutes": 45})
    check("timeout 이 아예 없으면 손대지 않는다(경고 담당)", cmd3 == "docker exec x python a.py")


def test_warnings():
    print("── 등록 자기모순 경고 ──")
    w = m.timeout_selfcheck_warnings([{"id": "A", "status": "pending", "est_minutes": 45,
                                      "command": AUDIT_CMD}])
    check("est 45 · timeout 1800 → 경고 1건", len(w) == 1 and "4350" in w[0])
    ok = m.timeout_selfcheck_warnings([{"id": "B", "status": "pending", "est_minutes": 25,
                                       "command": AUDIT_CMD.replace("timeout 1800", "timeout 4500")}])
    check("est 25 · timeout 4500 → 경고 없음", ok == [])
    n = m.timeout_selfcheck_warnings([{"id": "C", "status": "pending", "est_minutes": 30,
                                      "command": "docker exec x python a.py"}])
    check("est 있는데 timeout 없음 → 경고", len(n) == 1 and "timeout" in n[0])
    sk = m.timeout_selfcheck_warnings([{"id": "D", "status": "done", "est_minutes": 45,
                                       "command": AUDIT_CMD}])
    check("done 항목은 경고 대상 아님", sk == [])
    ne = m.timeout_selfcheck_warnings([{"id": "E", "status": "pending", "command": AUDIT_CMD}])
    check("est_minutes 없으면 판단하지 않음", ne == [])


def test_execute_integration():
    """가드가 실제 subprocess 로 넘어가는 명령을 바꾸는가(장식 금지)."""
    print("── execute() 통합(부작용 격리) ──")
    captured = {}

    class _R:                      # rc=124 = 컨테이너 timeout
        returncode = 124

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _R()

    tmp = tempfile.mkdtemp(prefix="cmdguard_")
    saved = {k: getattr(m, k) for k in ("subprocess", "LOGDIR", "RUNTIME", "PIDFILE", "STATE",
                                        "guards", "log", "append_ledger", "load_backlog",
                                        "save_backlog")}
    try:
        m.subprocess = types.SimpleNamespace(run=fake_run, STDOUT=-2, PIPE=-1, DEVNULL=-3)
        m.LOGDIR = tmp
        m.RUNTIME = tmp
        m.PIDFILE = os.path.join(tmp, "p.pid")
        m.STATE = os.path.join(tmp, "state.json")
        m.guards = lambda force, item: (True, "ok")
        m.log = lambda *a, **k: None
        m.append_ledger = lambda rec: None
        m.load_backlog = lambda: {"items": []}
        m.save_backlog = lambda b: None
        item = {"id": "CGX", "title": "timeout 가드 통합", "command": AUDIT_CMD,
                "est_minutes": 45, "metric": None}
        rc = m.execute(item)
        check("execute() 가 124 를 실패로 처리(측정값 없음)", rc == 0)
        check("subprocess 로 간 명령이 상향본이다(PANEL 빌드 소실 방지)",
              "timeout 4350" in captured.get("cmd", ""))
        logs = [f for f in os.listdir(tmp) if f.endswith(".log")]
        head = open(os.path.join(tmp, logs[0]), encoding="utf-8").read() if logs else ""
        check("실행 로그에 원본·효과 명령을 함께 남긴다(재현성)",
              "# command:" in head and "# effective_command:" in head
              and "timeout 4350" in head)
    finally:
        for k, v in saved.items():
            setattr(m, k, v)


def main():
    print("=== 컨테이너 timeout ↔ est_minutes 자기모순 가드 회귀 테스트 ===")
    test_parse()
    test_required()
    test_widen()
    test_effective()
    test_warnings()
    test_execute_integration()
    print(f"\n결과: {'전부 PASS' if not FAIL else 'FAIL ' + str(len(FAIL)) + '건: ' + str(FAIL)}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""status_snapshot.py — analyist_dd 상태를 **한 번에** 모아 출력한다(반복 조회 제거).

WHY(2026-10-04, 사용자 "작업 과정이 원활·효율적으로" 요청): 상태 보고마다 크론 실행표·목표 상태·
실험 registry·CEO 기록·컨테이너 잡·git 로그를 각각 조회하느라 도구 호출 4~6회가 들었다.
같은 것을 매번 다시 조립하는 비용을 없애려고 **한 명령으로 고정**한다.

설계 원칙(실패에 강할 것): 모든 조회는 개별 try — 하나가 없거나 깨져도 나머지는 출력한다.
값을 지어내지 않는다: 못 읽으면 '없음/실패'라고 쓴다(조용한 0 금지).
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import sqlite3
import subprocess
import sys
from _paths import proj, proj_dir

REPO = proj_dir()
CRON_DB = "/home/jhshi/.hermes/cron/executions.db"

KEY_JOBS = [
    ("c092a24cee20", "되돌림 22:30"), ("1da9e9522c0a", "돈상태 23:30"),
    ("31b7a5a8efa8", "CEO 23:45"), ("4fe9ce1604aa", "오케스트레이터 03:30"),
    ("39844789fbdc", "T1탐색 03:45"), ("39b8f887b196", "거래경로 08:50"),
    ("9c89953d7082", "수수료 16:05"), ("dc981c84398d", "청산창 09:15"),
    ("e6efdeed7947", "안전 개장전 08:25"),
]


def _json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _cron():
    out = []
    try:
        c = sqlite3.connect(CRON_DB)
        for jid, name in KEY_JOBS:
            r = list(c.execute("select started_at,status from executions where job_id=? "
                               "order by rowid desc limit 1", (jid,)))
            out.append((name, r[0][1] if r else "실행 전", (r[0][0][:16] if r else "")))
        day = dt.datetime.now().strftime("%Y-%m-%d")
        fails = list(c.execute("select job_id, substr(started_at,12,5), coalesce(error,'') "
                               "from executions where started_at like ? and status='failed'", (day + "%",)))
        return out, fails
    except Exception as exc:                                  # noqa: BLE001
        return out, [("(크론 조회 실패)", str(exc)[:40], "")]


def _git():
    try:
        log = subprocess.run(["git", "log", "--oneline", "-4"], cwd=REPO, capture_output=True,
                             text=True, timeout=20).stdout.strip().splitlines()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=REPO, capture_output=True,
                               text=True, timeout=20).stdout.strip().splitlines()
        return log, len(dirty)
    except Exception:
        return [], -1


def main() -> int:
    now = dt.datetime.now()
    print("=== analyist_dd 상태 스냅샷 · {0} ({1}) ===".format(
        now.strftime("%F %H:%M"), "월화수목금토일"[now.weekday()]))

    rows, fails = _cron()
    print("\n[자율 틱]")
    for name, status, when in rows:
        print("  {0:<16} {1:<10} {2}".format(name, status, when))
    print("  오늘 실패: {0}".format("없음" if not fails else
                                    " · ".join("{0} {1} {2}".format(*f) for f in fails[:4])))

    obj = _json(os.path.join(REPO, "data", "state", "objective_state.json"), {}) or {}
    val = obj.get("value") or {}
    money = obj.get("money") or {}
    print("\n[목표·돈]")
    print("  지표: {0}".format(json.dumps(val, ensure_ascii=False)[:150] or "없음"))
    print("  돈: 실현 {0}원 · 청산 {1}건 · 자본 {2}원 · 보유 {3}".format(
        money.get("realized_pnl_krw"), money.get("closed_trades"),
        money.get("equity_krw"), money.get("open_positions")))
    print("  봉투 위반: {0}".format(((obj.get("envelope") or {}).get("breaches") or ["없음"])))
    print("  1순위 레버: {0}".format(json.dumps(obj.get("top_lever"), ensure_ascii=False)[:110] or "없음"))

    reg = _json(os.path.join(REPO, "data", "state", "experiments.json"), {}) or {}
    tested = reg.get("tested") or {}
    passing = [k for k, v in tested.items() if v.get("pass")]
    print("\n[탐색] 조합 {0} · 통과 {1}{2}".format(
        len(tested), len(passing), (" " + ", ".join(passing[:3])) if passing else ""))

    ceo = os.path.join(REPO, "data", "state", "ceo_decisions.jsonl")
    if os.path.exists(ceo):
        lines = [l for l in open(ceo, encoding="utf-8") if l.strip()]
        last = json.loads(lines[-1]) if lines else {}
        print("\n[CEO] 결정 {0}건 · 최근 레버: {1} · 쿼터 {2} · 에스컬 {3}건".format(
            len(lines), str(last.get("lever"))[:60], last.get("experiment_quota"),
            len(last.get("escalations") or [])))
    else:
        print("\n[CEO] 결정 기록 없음(첫 판단 대기)")

    print("\n[야간 컨테이너 잡]")
    for name, path in (("CG100", os.path.join(REPO, "scripts", "_cg100_run.log")),):
        try:
            tail = subprocess.run(["tail", "-1", path], capture_output=True, text=True,
                                  timeout=10).stdout.strip()
            print("  {0}: {1}".format(name, tail[-110:] or "로그 비어 있음"))
        except Exception:
            print("  {0}: 로그 없음".format(name))

    log, dirty = _git()
    print("\n[리포] 커밋 {0}개 · 작업트리 변경 {1}건".format(len(log), dirty if dirty >= 0 else "?"))
    for line in log:
        print("  " + line)
    print("\n[성장 지표 4개] ① 챔피언 풀 대비 초과 (위 지표) ② 거래 발생일 "
          "③ 수수료 계상(16:05) ④ 실현손익(위 돈)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

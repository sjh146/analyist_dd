#!/usr/bin/env python3
"""improve_dispatch.py — 야간 개선 오케스트레이터: 큐 → worktree 위임 → 기계 검증 → (조건부) 병합.

WHY (2026-10-04): 자율 개선 루프의 마지막 조각. 2026-10-02/03 밤에 손으로 한 흐름
(측정 → worktree 에서 위임 → 검증 → 병합)을 매일 밤 자동으로 돌린다.

경계(설계 docs/ORCHESTRATOR_PLAN.md):
  · 자동 병합 금지 경로(PROTECTED)를 건드린 변경은 **절대 병합하지 않는다** → needs_human
  · `gates.orchestrator_auto_merge` 가 꺼져 있으면 **검증까지만** 하고 결과를 보고한다(권고안 dry-run 3일)
  · `data/state/improve_pause` 파일이 있으면 즉시 중단(사람의 한 줄 킬스위치)
  · 야간 최대 --max-tasks(기본 3)건, 동시 1건, 과제당 timeout(기본 2700초)

사용:
  python3 scripts/improve_dispatch.py --dry-run          # 검증·보고만(기본)
  python3 scripts/improve_dispatch.py --auto-merge       # T0 자동 병합(게이트도 켜져 있어야)
  python3 scripts/improve_dispatch.py --status           # 큐 상태만
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WT_ROOT = "/home/jhshi/wt"
PAUSE = os.path.join(REPO, "data", "state", "improve_pause")
RUNS = os.path.join(REPO, "data", "reports", "improve_runs.jsonl")
sys.path.insert(0, os.path.join(REPO, "scripts"))
import improve_queue as iq  # noqa: E402
from objective import load as load_objective  # noqa: E402

# 자동 병합 금지 경로 — 1줄이라도 건드리면 needs_human (설계 §0)
PROTECTED = (
    "trader-agent/", "scripts/full_pipeline_dd.sh",
    "services/xgboost-ml/app/training/champion_promote.py",
    "config/objective.json", ".env", "kill_switch.txt",
    "scripts/gate_promote_live_score.py", "scripts/objective.py",
)


def sh(cmd, cwd=None, timeout=None, env=None):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       timeout=timeout, env=env, shell=isinstance(cmd, str))
    return p.returncode, (p.stdout or ""), (p.stderr or "")


def touches_protected(paths: list[str]) -> list[str]:
    """변경 경로 중 금지 경로에 걸리는 것(빈 리스트면 안전)."""
    hit = []
    for p in paths:
        q = p.strip()
        if not q:
            continue
        for pat in PROTECTED:
            if q == pat or q.startswith(pat) or pat in q:
                hit.append(q)
                break
    return hit


def changed_paths(worktree: str) -> list[str]:
    rc, out, _ = sh(["git", "status", "--porcelain"], cwd=worktree)
    return [l[3:].strip() for l in out.splitlines() if l.strip()] if rc == 0 else []


def build_prompt(task: dict) -> str:
    acc = "\n".join(f"  - {a}" for a in task.get("acceptance") or ["변경 경로 테스트 통과"])
    return (
        f"작업: {task['title']}\n\n"
        f"왜(근거): {task.get('why') or '(없음)'}\n\n"
        f"수용 기준(이 기준을 만족해야 병합된다):\n{acc}\n\n"
        "규칙(위반 시 작업이 폐기된다):\n"
        "  · 아래 경로는 **절대 수정하지 말라**(실주문 경로·정책 값): trader-agent/, "
        "scripts/full_pipeline_dd.sh, services/xgboost-ml/app/training/champion_promote.py, "
        "config/objective.json, .env, kill_switch.txt, scripts/objective.py\n"
        "  · git 명령 금지(커밋은 오케스트레이터가 한다). 리포 밖 파일 생성 금지.\n"
        "  · 추측 금지: 수치는 실제 실행 결과만. 못 구하면 '측정 불가'로 남겨라.\n"
        "  · 파일 상단 docstring 에 WHY 와 근거(명령·파일)를 남겨라.\n"
        "  · 검증 명령을 실제로 실행하고 출력을 보고서에 붙여라.\n"
    )


def run_one(task: dict, *, auto_merge: bool, timeout: int, log=print) -> dict:
    qid = task["id"]
    wt = os.path.join(WT_ROOT, f"q_{qid}")
    branch = f"auto/{qid}"
    rec = {"id": qid, "title": task["title"], "started": dt.datetime.now().isoformat(timespec="seconds"),
           "worktree": wt, "branch": branch}
    # 1) 기존 worktree 정리 후 생성
    if os.path.isdir(wt):
        sh(["git", "worktree", "remove", "--force", wt], cwd=REPO)
    rc, out, err = sh(["git", "worktree", "add", "-b", branch, wt, "master"], cwd=REPO)
    if rc != 0:
        rec.update({"result": "rejected", "note": f"worktree 생성 실패: {err[:200]}"})
        return rec
    # 2) 위임(Claude Code 1차 → 실패 시 opencode 폴백은 ask_claude.sh 가 처리)
    prompt = build_prompt(task)
    env = dict(os.environ, ASK_CLAUDE_TIMEOUT=str(timeout), ASK_CLAUDE_PROJ=wt,
               ASK_CLAUDE_STALL_S="600", ASK_CLAUDE_EXTRA_DIR="/mnt/c/Users/jhshi/analyist_dd/trader-agent")
    report = os.path.join(wt, f"reports/agent_{qid}.md")
    os.makedirs(os.path.join(wt, "reports"), exist_ok=True)
    log(f"[{qid}] 위임 시작(worktree {wt})")
    rc, out, err = sh(["bash", os.path.join(REPO, "scripts", "ask_claude.sh"), "build", report, prompt],
                      cwd=wt, timeout=timeout + 120, env=env)
    rec["agent_rc"] = rc
    paths = changed_paths(wt)
    rec["changed"] = paths[:40]
    if not paths:
        rec.update({"result": "rejected", "note": "변경 없음(에이전트가 아무 것도 하지 않음)"})
        return rec
    hit = touches_protected(paths)
    if hit:
        rec.update({"result": "needs_human", "note": f"금지 경로 변경: {hit[:5]}"})
        return rec
    # 3) 기계 검증: 구문·테스트·루트 pytest
    rc_v, out_v, _ = sh(["bash", os.path.join(REPO, "scripts", "verify_agent_worktree.sh"), wt],
                        cwd=REPO, timeout=900)
    rec["verify_rc"] = rc_v
    rc_p, out_p, _ = sh(["/usr/bin/python3", "-m", "pytest", "-q", "tests/"], cwd=wt, timeout=900)
    rec["pytest_rc"] = rc_p
    rec["pytest_tail"] = (out_p or "")[-200:]
    if rc_v != 0 or rc_p != 0:
        rec.update({"result": "rejected",
                    "note": f"검증 실패(verify rc={rc_v}, pytest rc={rc_p}) — worktree 보존: {wt}"})
        return rec
    # 4) 커밋(worktree 안) — 과제당 1커밋
    sh(["git", "add", "-A"], cwd=wt)
    rc_c, _, err_c = sh(["git", "-c", "user.name=hermes-auto", "-c", "user.email=auto@local",
                         "commit", "-m", f"auto({qid}): {task['title'][:60]}"], cwd=wt)
    if rc_c != 0:
        rec.update({"result": "rejected", "note": f"커밋 실패: {err_c[:150]}"})
        return rec
    _, sha, _ = sh(["git", "rev-parse", "HEAD"], cwd=wt)
    sha = sha.strip()
    rec["commit"] = sha
    rec["revert"] = f"git revert {sha}"
    if not auto_merge:
        rec.update({"result": "verified_dry_run",
                    "note": f"검증 통과, 병합 보류(dry-run). 브랜치 {branch} 보존 — 검토 후 병합 가능"})
        return rec
    # 5) 병합: 단일 커밋 체리픽 → push
    rc_m, _, err_m = sh(["git", "cherry-pick", sha], cwd=REPO)
    if rc_m != 0:
        sh(["git", "cherry-pick", "--abort"], cwd=REPO)
        rec.update({"result": "rejected", "note": f"체리픽 실패: {err_m[:150]}"})
        return rec
    rc_push, _, err_push = sh(["git", "push", "origin", "master"], cwd=REPO)
    rec["push_rc"] = rc_push
    rec.update({"result": "merged",
                "note": f"병합·푸시 완료(push rc={rc_push})" + ("" if rc_push == 0 else f" {err_push[:120]}")})
    return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="야간 개선 오케스트레이터")
    ap.add_argument("--dry-run", action="store_true", help="검증만(기본; 게이트가 꺼져 있으면 동일)")
    ap.add_argument("--auto-merge", action="store_true", help="T0 자동 병합(게이트가 켜져 있어야)")
    ap.add_argument("--max-tasks", type=int, default=3)
    ap.add_argument("--timeout", type=int, default=2700)
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args(argv)

    d = iq._load()
    if a.status:
        print(json.dumps({"counts": {s: sum(1 for t in d["tasks"] if t["state"] == s)
                                     for s in iq.STATES},
                          "next": (iq.next_task(d) or {}).get("id")}, ensure_ascii=False, indent=2))
        return 0
    if os.path.exists(PAUSE):
        print(f"[improve] 일시중단 파일 존재({PAUSE}) — 아무 것도 하지 않는다")
        return 0

    obj = load_objective()
    gate = bool(obj.get("gates", {}).get("orchestrator_auto_merge", False))
    auto = bool(a.auto_merge and gate and not a.dry_run)
    added = iq.seed(d)
    iq._save(d)

    results = []
    for _ in range(max(0, a.max_tasks)):
        t = iq.next_task(d)
        if not t:
            break
        iq.main(["claim", t["id"]])              # inflight + attempts+1
        rec = run_one(t, auto_merge=auto, timeout=a.timeout)
        state = {"merged": "merged", "verified_dry_run": "todo", "rejected": "rejected",
                 "needs_human": "needs_human"}.get(rec["result"], "rejected")
        note = rec.get("note")
        if rec["result"] == "verified_dry_run":
            # dry-run 은 과제를 'todo' 로 되돌려 검토 후 병합할 수 있게 한다(시도 횟수는 소모).
            note = (note or "") + " [dry-run]"
        iq.main(["finish", t["id"], "--state", state, "--commit", rec.get("commit") or "",
                 "--note", note or ""])
        with open(RUNS, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        results.append(rec)
        d = iq._load()

    summary = {"when": dt.datetime.now().isoformat(timespec="seconds"), "auto_merge": auto,
               "seeded": added, "ran": [{"id": r["id"], "result": r["result"], "note": r.get("note"),
                                         "commit": r.get("commit"), "revert": r.get("revert")}
                                        for r in results]}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if results:
        merged = [r for r in results if r["result"] == "merged"]
        dry = [r for r in results if r["result"] == "verified_dry_run"]
        bad = [r for r in results if r["result"] in ("rejected", "needs_human")]
        print(f"\n[개선 오케스트레이터] 병합 {len(merged)} · 검증만 {len(dry)} · 기각/대기 {len(bad)}")
        for r in results:
            print(f"  - {r['id']} {r['result']}: {str(r.get('note'))[:90]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

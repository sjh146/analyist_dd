#!/usr/bin/env python3
"""improve_queue.py — 야간 자율 개선 과제 큐(우선순위·상태·중복 방지).

WHY (2026-10-04): 자율 개선의 병목은 '다음에 무엇을 고칠지'를 사람이 정해준다는 점이었다.
과제를 파일로 고정하고 상태를 기계가 관리하면, 디스패처가 매일 밤 스스로 1건을 꺼내 진행할 수 있다.
억지 과제는 만들지 않는다 — 근거(측정된 손실원·문서의 미완 항목·역할 원장의 blocked)가 있는 것만 넣는다.

사용:
  python3 scripts/improve_queue.py status                  # 요약
  python3 scripts/improve_queue.py add --title "..." --why "..." --acceptance "tests/x 통과" \
      [--priority 1] [--protected]
  python3 scripts/improve_queue.py next                    # 다음 과제 1건(JSON, 없으면 rc=1)
  python3 scripts/improve_queue.py claim QID               # inflight 로 표시
  python3 scripts/improve_queue.py finish QID --state merged|rejected|needs_human [--commit SHA] [--note ...]
  python3 scripts/improve_queue.py seed                    # objective_state·문서·원장에서 과제 생성(중복 제외)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUEUE = os.environ.get("IMPROVE_QUEUE") or os.path.join(REPO, "data", "state", "improve_queue.json")
STATES = ("todo", "inflight", "merged", "rejected", "needs_human")


def _load() -> dict:
    try:
        with open(QUEUE, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("tasks"), list):
            return d
    except (OSError, ValueError):
        pass
    return {"updated": None, "tasks": []}


def _save(d: dict) -> None:
    d["updated"] = dt.datetime.now().isoformat(timespec="seconds")
    os.makedirs(os.path.dirname(QUEUE), exist_ok=True)
    tmp = QUEUE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, QUEUE)


def _new_id(d: dict) -> str:
    day = dt.date.today().strftime("%Y%m%d")
    n = sum(1 for t in d["tasks"] if str(t.get("id", "")).startswith(f"Q{day}")) + 1
    return f"Q{day}-{n:02d}"


def add(d: dict, title: str, why: str, acceptance: list[str], priority: int = 2,
        protected: bool = False, source: str = "") -> dict:
    for t in d["tasks"]:                       # 제목 중복 방지(살아 있는 항목이 있으면 추가하지 않음)
        if t["title"].strip() == title.strip() and t["state"] in ("todo", "inflight"):
            return t
    task = {"id": _new_id(d), "title": title.strip(), "why": why.strip(),
            "acceptance": [a.strip() for a in acceptance if a.strip()],
            "protected": bool(protected), "priority": int(priority), "state": "todo",
            "attempts": 0, "max_attempts": 2, "commit": None, "note": None,
            "created": dt.datetime.now().isoformat(timespec="seconds"), "source": source}
    d["tasks"].append(task)
    return task


def next_task(d: dict) -> dict | None:
    todo = [t for t in d["tasks"]
            if t["state"] == "todo" and t["attempts"] < t.get("max_attempts", 2)]
    if not todo:
        return None
    return sorted(todo, key=lambda t: (t["priority"], t["created"]))[0]


def by_id(d: dict, qid: str) -> dict | None:
    return next((t for t in d["tasks"] if t["id"] == qid), None)


def seed(d: dict, repo: str = REPO) -> list[str]:
    """측정된 근거에서 과제를 만든다(없으면 아무 것도 만들지 않는다)."""
    added: list[str] = []
    # ① 목표 상태의 1순위 손실원
    try:
        st = json.load(open(os.path.join(repo, "data", "state", "objective_state.json"), encoding="utf-8"))
        lever = st.get("top_lever") or {}
        if lever.get("title"):
            t = add(d, f"1순위 손실원 제거: {str(lever['title'])[:60]}",
                    f"objective_state 1순위 · 금액 {lever.get('krw')}원 · 근거 {lever.get('evidence')}",
                    ["변경한 경로의 테스트 통과", "루트 pytest 통과"],
                    priority=1, source="objective_state.top_lever")
            added.append(t["id"])
    except (OSError, ValueError):
        pass
    # ② 역할 원장의 blocked/needs_setup
    for name in ("model_engineer_ledger.jsonl", "researcher_ledger.jsonl"):
        p = os.path.join(repo, "data", "reports", name)
        try:
            rows = [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]
        except (OSError, ValueError):
            continue
        for r in rows[-25:]:
            parsed = r.get("parsed") or {}
            if isinstance(parsed, dict) and parsed.get("needs_setup"):
                t = add(d, f"배선 필요: {str(r.get('title'))[:60]}",
                        f"{name} needs_setup · {str(parsed.get('needs_setup'))[:120]}",
                        ["변경한 경로의 테스트 통과"], priority=3, source=name)
                added.append(t["id"])
    return added


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="야간 개선 과제 큐")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("next")
    sub.add_parser("seed")
    a1 = sub.add_parser("add")
    a1.add_argument("--title", required=True)
    a1.add_argument("--why", default="")
    a1.add_argument("--acceptance", action="append", default=[])
    a1.add_argument("--priority", type=int, default=2)
    a1.add_argument("--protected", action="store_true")
    for name in ("claim", "finish"):
        sp = sub.add_parser(name)
        sp.add_argument("qid")
        if name == "finish":
            sp.add_argument("--state", required=True, choices=STATES)
            sp.add_argument("--commit", default=None)
            sp.add_argument("--note", default=None)

    a = ap.parse_args(argv)
    d = _load()
    if a.cmd == "status":
        from collections import Counter
        c = Counter(t["state"] for t in d["tasks"])
        print(json.dumps({"queue": QUEUE, "updated": d.get("updated"), "counts": dict(c),
                          "next": (next_task(d) or {}).get("id"),
                          "todo": [{"id": t["id"], "p": t["priority"], "title": t["title"][:60]}
                                   for t in sorted(d["tasks"], key=lambda x: (x["priority"], x["created"]))
                                   if t["state"] == "todo"]}, ensure_ascii=False, indent=2))
        return 0
    if a.cmd == "next":
        t = next_task(d)
        if not t:
            print("[queue] todo 없음", file=sys.stderr)
            return 1
        print(json.dumps(t, ensure_ascii=False, indent=2))
        return 0
    if a.cmd == "seed":
        added = seed(d)
        _save(d)
        print(json.dumps({"added": added, "queue_total": len(d["tasks"])}, ensure_ascii=False))
        return 0
    if a.cmd == "add":
        t = add(d, a.title, a.why, a.acceptance, a.priority, a.protected)
        _save(d)
        print(json.dumps(t, ensure_ascii=False, indent=2))
        return 0
    t = by_id(d, a.qid)
    if not t:
        print(f"[queue] {a.qid} 없음", file=sys.stderr)
        return 1
    if a.cmd == "claim":
        t["state"] = "inflight"
        t["attempts"] = int(t.get("attempts", 0)) + 1
        t["claimed_at"] = dt.datetime.now().isoformat(timespec="seconds")
    else:
        t["state"] = a.state
        t["commit"] = a.commit
        t["note"] = a.note
        t["finished_at"] = dt.datetime.now().isoformat(timespec="seconds")
        # 2회 실패한 과제는 사람 대기로 고정한다(무한 재시도 금지).
        if a.state == "rejected" and int(t.get("attempts", 0)) >= int(t.get("max_attempts", 2)):
            t["state"] = "needs_human"
    _save(d)
    print(json.dumps(t, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

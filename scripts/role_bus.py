#!/usr/bin/env python3
"""역할 버스 (role bus) — 역할 간 인계·선점·결정을 한 줄씩 남기는 append-only 로그.

왜 필요한가 (2026-11-16 실측):
  리서처·엔지니어·트레이더·감사가 각자 크론 틱으로 돌지만, 서로의 작업 상태를 아는 경로가
  `docs/QUANT_BOARD.md`(547KB)와 각자 원장(1.4MB/132KB/70KB)뿐이었다. 그래서
    · 같은 진단을 두 역할이 반복하고(중복),
    · 한 역할이 고친 것을 다른 역할이 모른 채 되돌리고,
    · 인계 항목이 큐에 쌓여도 누가 언제 처리할지가 사람 눈으로만 관리됐다.
  이 버스는 그 셋을 기계 판독 가능한 이벤트로 바꾼다. 파일이 곧 계약이다.

이벤트 스키마(한 줄 = JSON):
  {"ts": ISO8601, "role": "researcher|engineer|trader|auditor|ceo", "kind": "claim|release|result|handoff|decision|blocked",
   "task": "<짧은 식별자>", "ref": "<경로/이슈/실험 ID>", "note": "<한 줄>", "ttl_min": 180, "to": "<role|all>"}

사용:
  python3 scripts/role_bus.py post --role engineer --kind result --task news_backfill_fix \
      --ref scripts/news_event_backfill.py --note "source 필터·유니버스 수정, 귀속 46→62종목"
  python3 scripts/role_bus.py post --role researcher --kind handoff --task event_feature_revive --to engineer --note "..."
  python3 scripts/role_bus.py list --since 24h
  python3 scripts/role_bus.py claim --role engineer --task event_feature_revive   # 선점(다른 역할이 이미 잡았으면 exit 3)
  python3 scripts/role_bus.py release --role engineer --task event_feature_revive
  python3 scripts/role_bus.py open      # 아직 결과가 없는 claim/handoff 목록(인계 지연 감시)

종료코드: 0 정상 / 3 선점 충돌 / 2 사용 오류
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUS = os.path.join(PROJ, "data", "reports", "role_bus.jsonl")
ROLES = ("researcher", "engineer", "trader", "auditor", "ceo", "review_board")
KINDS = ("claim", "release", "result", "handoff", "decision", "blocked", "note")
DEFAULT_TTL_MIN = 180


def now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def read_events(limit: int | None = None) -> list[dict]:
    if not os.path.exists(BUS):
        return []
    out: list[dict] = []
    with open(BUS, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # 손상 줄은 건너뛴다(append-only 라 서로 덮어쓰지 않는다)
    return out[-limit:] if limit else out


def post(ev: dict) -> None:
    os.makedirs(os.path.dirname(BUS), exist_ok=True)
    with open(BUS, "a", encoding="utf-8") as f:
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")


def parse_since(s: str) -> dt.datetime:
    s = (s or "").strip().lower()
    unit, n = (s[-1:], s[:-1]) if s and s[-1] in "hmd" else ("h", s)
    try:
        n = float(n)
    except ValueError:
        n = 24.0
    return now() - dt.timedelta(**{{"h": "hours", "m": "minutes", "d": "days"}[unit]: n})


def live_claims(events: list[dict]) -> dict[str, dict]:
    """아직 release/result 가 없는 claim — task 별 마지막 claim."""
    open_by_task: dict[str, dict] = {}
    for ev in events:
        t = ev.get("task")
        if not t:
            continue
        if ev.get("kind") == "claim":
            ttl = float(ev.get("ttl_min") or DEFAULT_TTL_MIN)
            ts = dt.datetime.fromisoformat(ev["ts"])
            if now() - ts <= dt.timedelta(minutes=ttl):
                open_by_task[t] = ev
            else:
                open_by_task.pop(t, None)
        elif ev.get("kind") in ("release", "result"):
            open_by_task.pop(t, None)
    return open_by_task


def main() -> int:
    ap = argparse.ArgumentParser(description="역할 버스")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("post")
    p.add_argument("--role", required=True, choices=ROLES)
    p.add_argument("--kind", required=True, choices=KINDS)
    p.add_argument("--task", required=True)
    p.add_argument("--ref", default="")
    p.add_argument("--note", default="")
    p.add_argument("--to", default="all")
    p.add_argument("--ttl-min", type=float, default=DEFAULT_TTL_MIN)

    c = sub.add_parser("claim")
    c.add_argument("--role", required=True, choices=ROLES)
    c.add_argument("--task", required=True)
    c.add_argument("--note", default="")
    c.add_argument("--ttl-min", type=float, default=DEFAULT_TTL_MIN)

    r = sub.add_parser("release")
    r.add_argument("--role", required=True, choices=ROLES)
    r.add_argument("--task", required=True)
    r.add_argument("--note", default="")

    l = sub.add_parser("list")
    l.add_argument("--since", default="24h")
    l.add_argument("--role", default="")
    l.add_argument("--limit", type=int, default=50)

    sub.add_parser("open")

    a = ap.parse_args()
    events = read_events()

    if a.cmd == "post":
        post({"ts": now().isoformat(timespec="seconds"), "role": a.role, "kind": a.kind,
              "task": a.task, "ref": a.ref, "note": a.note, "ttl_min": a.ttl_min, "to": a.to})
        print(f"[bus] {a.kind} {a.role}:{a.task}")
        return 0

    if a.cmd == "claim":
        held = live_claims(events).get(a.task)
        if held and held.get("role") != a.role:
            print(f"[bus] 선점 충돌 — {a.task} 는 {held['role']} 가 {held['ts']} 에 잡음", file=sys.stderr)
            return 3
        post({"ts": now().isoformat(timespec="seconds"), "role": a.role, "kind": "claim",
              "task": a.task, "note": a.note, "ttl_min": a.ttl_min, "to": "all"})
        print(f"[bus] claim {a.role}:{a.task} (ttl {a.ttl_min:.0f}m)")
        return 0

    if a.cmd == "release":
        post({"ts": now().isoformat(timespec="seconds"), "role": a.role, "kind": "release",
              "task": a.task, "note": a.note, "to": "all"})
        print(f"[bus] release {a.role}:{a.task}")
        return 0

    if a.cmd == "list":
        since = parse_since(a.since)
        rows = [e for e in events
                if dt.datetime.fromisoformat(e["ts"]) >= since
                and (not a.role or e.get("role") == a.role)]
        for e in rows[-a.limit:]:
            print(f"{e['ts']} {e.get('role','?'):10s} {e.get('kind','?'):9s} {e.get('task','?'):28s} "
                  f"{e.get('ref','')[:40]:40s} {e.get('note','')[:80]}")
        print(f"({len(rows)}건)")
        return 0

    if a.cmd == "open":
        held = live_claims(events)
        if not held:
            print("[bus] 열린 claim 없음")
            return 0
        for t, e in sorted(held.items()):
            print(f"OPEN {e['role']:10s} {t:28s} since {e['ts']} ttl={float(e.get('ttl_min') or DEFAULT_TTL_MIN):.0f}m")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())

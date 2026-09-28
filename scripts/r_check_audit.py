#!/usr/bin/env python3
"""리서처 백로그 check 전수 감사 (읽기 전용 — DB SELECT 만, 수집·빌드 없음).

WHY (2026-09-29 06:2x 실측): check 3건(R10·R11·R12)이 `computed_at=(SELECT MAX(computed_at))`
= '마지막 배치' 필터를 쓰고 있었다. 9/28 06:38 거시 배치 16행이 MAX 를 차지하자 9/25 배치 소속
피처가 판정에서 빠져 R10·R12 가 **0(미달)** 로 읽혔다(데이터는 그대로 — 필터만 빼면 5). 즉
"피처가 있는데 check 가 0" 인 항목은 수집 실패가 아니라 **판정 결함**일 수 있다.

이 감사기는 각 항목의 check 를 구동기(researcher_cycle.eval_check)와 같은 방식으로 실행해
값·판정을 한 표로 보여준다 → 다음 틱의 리서처가 '미달의 원인'을 로그 추적 없이 분류할 수 있다.
같은 배치 스코프 필터가 다시 들어오면 여기서 즉시 드러난다(경고 표시).

사용:
  cd /home/jhshi/analyist_dd && POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434 \
    /usr/bin/python3 scripts/r_check_audit.py [--only R10,R12]
종료코드: 0 항상(감사 도구) / 2 배치 스코프 필터 재발.
"""
import argparse
import json
import os
import sys

PROJ = os.environ.get("PROJ_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJ, "scripts"))

import researcher_cycle as rc   # noqa: E402  (구동기와 동일한 판정 경로를 쓴다)

BACKLOG = os.path.join(PROJ, "docs", "QUANT_RESEARCH_BACKLOG.json")
SUSPECT = "computed_at=(SELECT MAX(computed_at)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None, help="쉼표 구분 ID 목록")
    args = ap.parse_args()
    only = {s.strip() for s in args.only.split(",")} if args.only else None

    b = json.load(open(BACKLOG, encoding="utf-8"))
    suspect = []
    rows = []
    for it in sorted(b["items"], key=lambda x: x.get("priority", 99)):
        if only and it["id"] not in only:
            continue
        if not it.get("check"):
            rows.append((it["id"], it.get("status"), "-", None, None, "check 미정의"))
            continue
        val, detail, passed = rc.eval_check(it)
        rows.append((it["id"], it.get("status"), f"{val:g}" if val is not None else "-",
                     passed, it.get("check_target"), detail))
        if SUSPECT in it["check"]:
            suspect.append(it["id"])

    print(f"{'ID':4s} {'status':15s} {'value':>10s} {'pass':>5s}  detail")
    for rid, st, v, ok, tgt, detail in rows:
        flag = "PASS" if ok else ("FAIL" if ok is not None else "??")
        print(f"{rid:4s} {str(st):15s} {v:>10s} {flag:>5s}  {detail}")
    if suspect:
        print(f"\n⚠ 배치 스코프 필터 재발({SUSPECT}...): {', '.join(suspect)} "
              f"— '마지막 배치'를 요구하면 다른 배치가 MAX 를 차지할 때 값이 0 으로 뒤집힌다(실측 2026-09-29 R10·R12).")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

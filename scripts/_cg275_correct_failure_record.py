#!/usr/bin/env python3
"""CG275 의 '실행실패(측정값 없음)' 기록을 정정 표시한다 — 2026-11-14 17:1x.

왜: 스윕은 rc=0 으로 완주했고 요약 JSON(cg275_insample_sweep_summary.json, mtime 17:10)이 존재한다.
죽은 곳은 **리포트 쓰기 한 곳**이다(reports/overnight 가 root 소유 → 호스트 jhshi PermissionError).
그래서 rc=1 기록의 detail "측정값 없음"은 사실이 아니다. 이력은 지우지 않고 correction 을 붙이며,
같은 측정은 ingest 로 원장에 별도 기록됐다(17:15, 판정 '혼합').
"""
import json
import os
from datetime import datetime, timedelta, timezone

LED = "/home/jhshi/analyist_dd/data/reports/model_engineer_ledger.jsonl"
KST = timezone(timedelta(hours=9))
rows = [json.loads(l) for l in open(LED, encoding="utf-8") if l.strip()]
now = datetime.now(KST).isoformat(timespec="seconds")
n = 0
for r in rows:
    if r.get("id") != "CG275" or r.get("rc") == 0:
        continue
    if r.get("correction"):
        continue
    r["correction"] = {
        "at": now,
        "note": ("측정은 존재한다 — 스윕 rc=0 완주(요약 mtime 17:10) 후 리포트 파일 쓰기만 실패"
                 "(reports/overnight 는 root 소유). rc=1 은 '측정 실패'가 아니라 '리포트 쓰기 실패'다. "
                 "같은 측정을 ingest 로 재편입(17:15, 판정 '혼합')했다."),
    }
    r["reported"] = True          # 이 실패는 이번 보고에서 함께 보고했다(중복 보고 방지)
    r["reported_at"] = now
    n += 1
tmp = LED + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    for r in rows:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
os.replace(tmp, LED)
print(f"정정 표시 {n}건")

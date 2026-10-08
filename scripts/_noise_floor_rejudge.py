#!/usr/bin/env python3
"""원장 사후 재판정 — protocol_noise_floor 기록의 헤드라인 verdict 교정 (2026-10-09 CG143).

배경: parse/judge 배선 시점 차이로 CG143 이 verdict='판정불가'·detail='' 로 기록됐다(요약 JSON 은
정상). 재실행(수 분) 대신 **요약 JSON 을 새 판정기로 다시 읽어 기록만 교정**한다.
대상은 metric=='protocol_noise_floor' 이고 verdict 가 '판정불가' 인 기록뿐이다(멱등).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_engineer_cycle as me  # noqa: E402

rows = me.load_ledger()
backlog = me.load_backlog()
fixed = []
for r in rows:
    if r.get("metric") != "protocol_noise_floor":
        continue
    if str(r.get("verdict")) != "판정불가":
        continue
    spath = me.summary_path("protocol_noise_floor", None)
    parsed = me.parse_protocol_noise_floor(spath, 0.0)
    if parsed.get("error"):
        print(f"SKIP {r.get('id')}: {parsed['error']}")
        continue
    verdict, detail, _ = me.judge_by_metric({"metric": "protocol_noise_floor", "id": r.get("id")}, parsed)
    old = r.get("verdict")
    r["parsed"] = parsed
    r["verdict"], r["detail"] = verdict, detail
    r["rejudged"] = {"ts": me.now_kst().isoformat(timespec="seconds"),
                     "note": "judge 분기 사후 배선 — 요약 JSON 재파싱으로 판정 교정(재실행 아님)"}
    fixed.append((r.get("id"), old, verdict, detail))

if not fixed:
    print("교정 대상 없음(이미 판정 기록됨)")
    sys.exit(0)

me._rewrite_ledger(rows)
for it in backlog["items"]:
    for iid, _old, v, d in fixed:
        if it.get("id") == iid and isinstance(it.get("result"), dict):
            it["result"].update({"verdict": v, "detail": d})
me.save_backlog(backlog)
for iid, old, v, d in fixed:
    print(f"교정 {iid}: {old} → {v}")
    print(f"  {d}")

"""R48 백로그 result 를 원장 최신 회차로 정정한다(낡은 rc=1 → 실제 최신 rc=0).

실측 2026-11-25: R48 은 00:47 rc=1 실패 뒤 수리돼 04:48 재실행에서 rc=0 으로 통과했고 원장에는
rc=0 회차가 남았는데, 백로그 result 만 00:47 에 멈춰 있어 틱 보고가 '실패'로 오독했다.
이 스크립트는 result 만 원장 최신 회차로 다시 맞춘다(attempts·status 는 건드리지 않는다).
"""
import json
import os

PROJ = "/home/jhshi/analyist_dd"
p = os.path.join(PROJ, "docs/QUANT_RESEARCH_BACKLOG.json")
led = [json.loads(l) for l in open(os.path.join(PROJ, "data/reports/researcher_ledger.jsonl"),
                                   encoding="utf-8") if l.strip()]
latest = {}
for r in led:
    if r.get("id"):
        latest[r["id"]] = r

d = json.load(open(p, encoding="utf-8"))
changed = []
for it in d["items"]:
    r = latest.get(it.get("id"))
    if not r:
        continue
    cur = it.get("result") if isinstance(it.get("result"), dict) else {}
    if (cur.get("rc"), cur.get("log")) != (r.get("rc"), r.get("log")):
        it["result"] = {"detail": r.get("detail"), "check_value": r.get("check_value"),
                        "rc": r.get("rc"), "log": r.get("log")}
        changed.append((it["id"], cur.get("rc"), r.get("rc")))

if changed:
    tmp = p + ".tmp"
    json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    os.replace(tmp, p)
print("corrected:", changed or "none")

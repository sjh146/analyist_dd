#!/usr/bin/env python3
"""크론 세션이 직접 보고한 실험 기록의 원장 reported 플래그를 켠다(중복 보고 방지)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
import model_engineer_cycle as m

IDS = [a for a in sys.argv[1:]]
led = m.load_ledger()
n = 0
for r in led:
    if r.get("id") in IDS and not r.get("reported"):
        r["reported"] = True
        r["reported_at"] = m.now_kst().isoformat(timespec="seconds")
        n += 1
        print("marked", r["ts"], r["id"], r.get("verdict"))
if n:
    m._rewrite_ledger(led)
print("done: marked", n)
rem = [r for r in m.load_ledger() if not r.get("reported")]
print("unreported 남음:", len(rem), [r.get("id") for r in rem])

#!/usr/bin/env python3
"""캐시 오염 가드 자체점검 — mtime 은 '방금', 내용의 생성시각은 낡은 증거를 심고
source_freshness_note() 가 캐시를 믿지 않고 다시 재는지 확인한다."""
import json
import os
import sys
import time

PROJ = "/home/jhshi/analyist_dd"
P = os.path.join(PROJ, "data/reports/source_freshness.json")
sys.path.insert(0, os.path.join(PROJ, "scripts"))

import model_engineer_cycle as m  # noqa: E402

orig = open(P, encoding="utf-8").read()
try:
    d = json.loads(orig)
    d["generated_at"] = "2026-10-08T09:00:00+00:00"
    d["reference"] = {"table": "market_data", "date": "2026-10-08",
                      "lag_trading_days": 0, "verdict": "OK"}
    for v in d["sources"].values():
        v["lag_trading_days"] = 0
        v["verdict"] = "OK"
    with open(P, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.utime(P, (time.time(), time.time()))
    print("오염 캐시 심음: generated_at=2026-10-08, mtime=now")
    note = m.source_freshness_note()
    print("NOTE:", note)
    ok = ("⛔" in note) or ("CRITICAL" in note) or ("낡음" in note)
    print("[PASS] 낡은 캐시를 믿지 않고 재측정" if ok else "[FAIL] 낡은 '정상' 캐시를 그대로 보고")
    sys.exit(0 if ok else 1)
finally:
    with open(P, "w", encoding="utf-8") as f:
        f.write(orig)

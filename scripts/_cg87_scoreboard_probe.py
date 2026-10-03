#!/usr/bin/env python3
"""스코어보드 트레일링 무개선 스트릭을 **원장 순서**로 재계산 + CG86 기여 분해."""
import json

LED = "/home/jhshi/analyist_dd/data/reports/model_engineer_ledger.jsonl"
BASE = 0.5406
recs = []
for line in open(LED, encoding="utf-8"):
    line = line.strip()
    if not line:
        continue
    try:
        recs.append(json.loads(line))
    except json.JSONDecodeError:
        continue

def best(rec):
    per = (rec.get("parsed") or {}).get("per_exp") or {}
    vals = [(float(v["mean"]), k) for k, v in per.items()
            if isinstance(v, dict) and isinstance(v.get("mean"), (int, float))]
    return max(vals) if vals else (None, None)

measured = [r for r in recs if r.get("rc") == 0 and best(r)[0] is not None
            and not (isinstance(r.get("parsed"), dict) and r["parsed"].get("error"))]
print("측정성립(원장 순서) =", len(measured), "| 마지막 6건:")
for r in measured[-6:]:
    b, n = best(r)
    print(f"  {r.get('ts')} {r.get('id'):6s} best={b:.4f} ({n}) verdict={r.get('verdict')} "
          f"improved={b - BASE >= 0.02}")

tail = 0
for r in reversed(measured):
    if best(r)[0] - BASE >= 0.02:
        break
    tail += 1
print("\n현 스코어보드 기준(per_exp 최고-arm) 트레일링 =", tail)

# CG86 을 제외하면?
tail2 = 0
for r in reversed([x for x in measured if x.get("id") != "CG86"]):
    if best(r)[0] - BASE >= 0.02:
        break
    tail2 += 1
print("CG86(판정=노이즈, 최고 arm 은 30종목 구간판 0.5703) 제외 시 트레일링 =", tail2)

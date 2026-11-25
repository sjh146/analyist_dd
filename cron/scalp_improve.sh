#!/bin/bash
set -uo pipefail
REPO=/home/jhshi/analyist_dd
cd "$REPO" || exit 1
DAY=$(date +%Y%m%d)
QUEUE="$REPO/reports/scalp_improve_queue_${DAY}.md"
AUDIT="$REPO/reports/scalp_audit_latest.json"

{
  echo "# 단타 개선 큐 (${DAY})"
  echo
  echo "생성: $(date '+%F %T')"
  echo
  if [ -f "$AUDIT" ]; then
    /usr/bin/python3 - "$AUDIT" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding='utf-8'))
print("현재 판정: **%s**" % d.get("verdict"))
print()
for f in d.get("findings") or []:
    print("- `%s` (%s): %s" % (f.get("kind"), f.get("severity"), f.get("detail")))
if not d.get("findings"):
    print("- 위반 없음")
PY
  else
    echo "- 감사 산출물 없음"
  fi
  echo
  echo "## 오늘 누적 발견(중복 제거)"
  echo
  if [ -f "$REPO/reports/scalp_findings.jsonl" ]; then
    /usr/bin/python3 - "$REPO/reports/scalp_findings.jsonl" "$DAY" <<'PY'
import json, sys
from collections import Counter
path, day = sys.argv[1], sys.argv[2]
rows = []
for line in open(path, encoding='utf-8'):
    line = line.strip()
    if not line:
        continue
    try:
        r = json.loads(line)
    except ValueError:
        continue
    if str(r.get("at", ""))[:10].replace("-", "") == day:
        rows.append(r)
if not rows:
    print("- 없음")
else:
    c = Counter(r["kind"] for r in rows)
    for kind, n in c.most_common():
        sample = next(r["detail"] for r in rows if r["kind"] == kind)
        print("- `%s` × %d — 예: %s" % (kind, n, sample[:120]))
PY
  else
    echo "- 기록 없음"
  fi
  echo
  echo "## 처리 규칙"
  echo
  echo "1. 위반은 재현 스크립트/수치와 함께 고친다(추측 금지)."
  echo "2. 실주문 경로 변경은 테스트 → 장중 실측 검증까지 통과해야 종료."
  echo "3. 커밋 1건당 수정 1개, 되돌리기 sha 기록."
} > "$QUEUE"

echo "{\"at\": \"$(date -Iseconds)\", \"queue\": \"$QUEUE\"}" >> "$REPO/reports/scalp_improve_history.jsonl"
echo "queue written: $QUEUE"

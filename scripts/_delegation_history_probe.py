import json
import glob

rows = []
for line in open("data/reports/researcher_ledger.jsonl", encoding="utf-8"):
    line = line.strip()
    if not line:
        continue
    try:
        rows.append(json.loads(line))
    except ValueError:
        rows.append({"_raw": line})

blob_all = json.dumps(rows, ensure_ascii=False)
for key in ("저작위임", "저작 위임", "저작완료", "저작실패", "저작생략", "delegate_rc", "ask_claude"):
    print("{0:<12} 등장 {1}회".format(key, blob_all.count(key)))

au = [r for r in rows if isinstance(r, dict) and (
    "저작" in json.dumps(r, ensure_ascii=False) or "delegate_rc" in json.dumps(r, ensure_ascii=False))]
print("\n저작 관련 원장 항목:", len(au))
for r in au[-8:]:
    d = r.get("detail")
    tag = ""
    if isinstance(d, dict):
        tag = "rc={0} exists={1} timeout={2}".format(d.get("delegate_rc"), d.get("exists"), d.get("timeout"))
    elif isinstance(d, str):
        tag = d[:80]
    print("  -", r.get("id") or r.get("item") or "?", "|", str(r.get("msg") or r.get("result") or "")[:70], "|", tag)

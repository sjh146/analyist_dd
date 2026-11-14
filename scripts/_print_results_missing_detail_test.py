#!/usr/bin/env python3
"""원장 기록에 `detail` 이 없어도 틱 표시가 죽지 않는지 검증 (2026-11-14 16:05 실사고 대응).

문제: 수동 실행 결과를 원장에 편입한 CG273 은 판정 근거를 `note` 로만 남겼다(detail 키 없음).
      `_print_results` 가 r['detail'] 을 직접 읽어 KeyError: 'detail' → 틱 전체 rc=1 →
      **그 결과가 사용자에게 전달되지 않았다**(다음 틱이 미전달을 되돌려 재보고해야 했다).

검증: ① detail 없는 기록에 note 만 있어도 _print_results 가 출력하고 죽지 않는다
      ② detail·note 둘 다 없어도 죽지 않는다('(근거 미기록)')
      ③ load_ledger 가 읽기 측에서 detail 을 note 로 채운다(다른 소비자도 안전)
"""
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout

sys.path.insert(0, "/home/jhshi/analyist_dd/scripts")
import model_engineer_cycle as m

fails = []


def check(name, cond, extra=""):
    print(f"{'PASS' if cond else 'FAIL'} {name}{(' — ' + extra) if extra and not cond else ''}")
    if not cond:
        fails.append(name)


def run(rows):
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            m._print_results(rows)
        return True, buf.getvalue()
    except Exception as e:                      # noqa: BLE001 — 여기서 죽으면 그게 실패다
        return False, f"{type(e).__name__}: {e}"


# ① note 만 있는 기록(CG273 실측 형태)
ok, out = run([{"id": "CG273", "title": "t", "rc": 0, "elapsed_min": 1.5,
                "verdict": "후보 없음", "note": "수동 실행 — 살아있는 최고 |Δ|=0.0113"}])
check("① detail 없이 note 만 있어도 출력", ok and "0.0113" in out, out)
check("① 판정 문구 유지", ok and "후보 없음" in out, out)

# ② detail·note 둘 다 없음
ok2, out2 = run([{"id": "CGX", "title": "t", "verdict": "미상"}])
check("② 근거가 아예 없어도 죽지 않음", ok2 and "근거 미기록" in out2, out2)

# ③ load_ledger 읽기 정규화
d = tempfile.mkdtemp(prefix="ledgernorm")
p = os.path.join(d, "ledger.jsonl")
with open(p, "w", encoding="utf-8") as f:
    f.write(json.dumps({"id": "A", "note": "n-only"}, ensure_ascii=False) + "\n")
    f.write(json.dumps({"id": "B", "detail": "d"}, ensure_ascii=False) + "\n")
    f.write(json.dumps({"id": "C"}, ensure_ascii=False) + "\n")
    f.write("{broken json\n")
saved = m.LEDGER
try:
    m.LEDGER = p
    rows = m.load_ledger()
finally:
    m.LEDGER = saved
byd = {r["id"]: r for r in rows}
check("③ note → detail 채움", byd.get("A", {}).get("detail") == "n-only", str(byd.get("A")))
check("③ 기존 detail 보존", byd.get("B", {}).get("detail") == "d", str(byd.get("B")))
check("③ 둘 다 없으면 자리표시", byd.get("C", {}).get("detail") == "(근거 미기록)", str(byd.get("C")))
check("③ 깨진 줄은 건너뛴다", len(rows) == 3, str(len(rows)))

print("\n" + ("전부 PASS" if not fails else f"{len(fails)}건 FAIL: {fails}"))
sys.exit(1 if fails else 0)

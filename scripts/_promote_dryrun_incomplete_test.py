#!/usr/bin/env python3
"""[테스트] 승격 판정 레코드의 '근거 미비' 표시 — champion_scorecard.last_promote_dryrun.

왜(실측 2026-11-26 CG507): 일일 판정 배치가 남긴 CG_DAILY_1125 는 parsed 에
status·candidate_auc 만 있고 reason·champion_baseline 이 없는데, 성적표가 그 기록을
최신 승격 판정으로 실어 reason=null 을 내보냈다. 트레이더 계약2 검증은 필드 존재만 보므로
미비가 '형식 충족'으로 통과했다. 수리 후 이 함수는 값을 만들어 채우지 않고
complete=False + missing 목록을 남긴다(소비자가 계약 위반으로 계상할 수 있게).

검사:
 1) 최신 기록이 미비 → complete=False · missing=['reason','champion_baseline'] · record_id 보존
 2) 최신 기록이 완전 → complete=True · 'missing' 키 없음
 3) champion_baseline 은 없어도 baseline_source 가 있으면 근거 있음으로 본다
 4) 원장이 비면 None(회귀)
 5) 미비 기록이 최신일 때 **옛 완전 기록으로 조용히 대체하지 않는다**(최신 판정을 정직하게 표시)

실행: python3 scripts/_promote_dryrun_incomplete_test.py
"""
import importlib.util
import json
import os
import tempfile

SPEC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "champion_scorecard.py")
spec = importlib.util.spec_from_file_location("csc", SPEC)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

PASS = FAIL = 0


def check(name, got, want):
    global PASS, FAIL
    ok = got == want
    PASS, FAIL = PASS + ok, FAIL + (not ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}\n      got ={got}\n      want={want}")


def row(id_, ts, parsed):
    return {"id": id_, "ts": ts, "metric": parsed.get("metric"), "parsed": parsed}


daily = row("CG_DAILY_1125", "2026-11-25T22:30:57+09:00",
            {"metric": "engineer_manual", "candidate_auc": 0.5199, "status": "kept_incumbent"})
full = row("CG453", "2026-11-23T22:28:53+09:00",
           {"metric": "engineer_manual", "status": "kept_incumbent", "candidate_auc": 0.5791,
            "reason": "돈 기준 게이트 거부: 순기대 -0.153%p", "champion_baseline": 0.5513,
            "champion_baseline_source": "robust_auc.json"})
r = m.last_promote_dryrun([full, daily])
check("최신 미비 → complete", r.get("complete"), False)
check("최신 미비 → missing", r.get("missing"), ["reason", "champion_baseline"])
check("최신 미비 → record_id", r.get("record_id"), "CG_DAILY_1125")
check("최신 미비 → 옛 기록으로 대체하지 않음", (r.get("reason"), r.get("champion_baseline")), (None, None))

r2 = m.last_promote_dryrun([daily, full])
check("완전 최신 → complete", r2.get("complete"), True)
check("완전 최신 → missing 키 없음", "missing" in r2, False)
check("완전 최신 → reason 보존", r2.get("reason"), "돈 기준 게이트 거부: 순기대 -0.153%p")

src = row("CG_X", "2026-11-24T00:00:00+09:00",
          {"metric": "champion_promote_dryrun", "status": "kept_incumbent", "candidate_auc": 0.5,
           "reason": "floor", "champion_baseline_source": "robust_auc.json"})
check("baseline_source 만 → complete", m.last_promote_dryrun([src]).get("complete"), True)

check("빈 원장 → None", m.last_promote_dryrun([]), None)
check("parsed 없는 기록 → None", m.last_promote_dryrun([{"id": "X", "ts": "t"}]), None)

tmpd = tempfile.mkdtemp(prefix="pd_incomplete_")
lp = os.path.join(tmpd, "ledger.jsonl")
with open(lp, "w", encoding="utf-8") as f:
    for r_ in (full, daily):
        f.write(json.dumps(r_, ensure_ascii=False) + "\n")
check("원장 파일 로드 경유", m.last_promote_dryrun(m._read_ledger(lp)).get("missing"),
      ["reason", "champion_baseline"])

print(f"\n{PASS}/{PASS + FAIL} PASS")
raise SystemExit(0 if FAIL == 0 else 1)

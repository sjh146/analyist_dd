#!/usr/bin/env python3
"""[테스트] 승격 dry-run 레코드 근거 완비 metric(`promote_dryrun_reason_present`) 배선 검증.

왜(실측 2026-11-26 CG507): 일일 판정 크론이 trader_ledger 에 남긴 validation.promote_dryrun 이
`status=kept_incumbent · candidate_auc=0.5199` 만 남기고 reason·champion_baseline 을 비웠는데,
트레이더 계약2 검증은 원장 문자열에 'dry-run' 이 있는지만 봐서 통과했다. 구동기에 이 metric 의
파서가 없어 실행은 rc=0 인데 원장에는 '판정불가(parser 없음)' 로만 남았다 →
**근거 없는 판정이 정상으로 보이는 경로**가 그대로 유지된다.

검사:
 1) 요약 경로 라우팅(성적표 JSON)
 2) 미비 레코드 → complete=false · missing=[reason, champion_baseline, baseline_source]
 3) 판정 → '계약 미비' (성능 판정 아님)
 4) 완비 레코드 → '계약 충족'
 5) promoted 는 기준선 없이도 통과(비교 대상이 없는 판정)
 6) 파일 없음/status 없음 → 판정불가
 7) per_exp 를 만들지 않는다(scoreboard 오염 방지)

실행: python3 scripts/_promote_dryrun_reason_test.py
"""
import importlib.util
import json
import os
import sys
import tempfile

sys.path.insert(0, "/home/jhshi/analyist_dd/scripts")
MEC = "/home/jhshi/analyist_dd/scripts/model_engineer_cycle.py"
spec = importlib.util.spec_from_file_location("mec", MEC)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

PASS = FAIL = 0


def check(name, got, want):
    global PASS, FAIL
    ok = got == want
    PASS, FAIL = PASS + ok, FAIL + (not ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}\n      got ={got}\n      want={want}")


tmp = tempfile.mkdtemp(prefix="pdreason_")


def write(name, obj):
    p = os.path.join(tmp, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    return p


check("라우팅: 성적표 JSON", m.summary_path("promote_dryrun_reason_present"), m.SCORECARD)

bare = write("bare.json", {"promote_dryrun": {
    "status": "kept_incumbent", "candidate_auc": 0.5199,
    "reason": None, "champion_baseline": None, "baseline_source": None,
    "record_id": "CG_DAILY_1125", "record_ts": "2026-11-25T22:30:57+09:00"}})
p = m.parse_promote_dryrun_reason(bare, 0)
check("미비 레코드: complete", p.get("complete"), False)
check("미비 레코드: missing", p.get("missing"), ["reason", "champion_baseline", "baseline_source"])
check("미비 레코드: reason_present", p.get("reason_present"), False)
v, d, delta = m.judge_promote_dryrun_reason({"id": "CG507"}, p)
check("미비 레코드 판정", v, "계약 미비")
check("미비 레코드 detail 에 기록 id·미비 필드", all(s in d for s in ("CG_DAILY_1125", "미비 필드")), True)
check("per_exp 미생성", "per_exp" in p, False)
check("델타 없음(성능 판정 아님)", delta, None)

full = write("full.json", {"promote_dryrun": {
    "status": "kept_incumbent", "candidate_auc": 0.5199,
    "reason": "candidate auc 0.5199 < floor 0.53",
    "champion_baseline": 0.5513, "baseline_source": "robust_auc.json",
    "record_id": "X", "record_ts": "2026-11-26T22:30:00+09:00"}})
p = m.parse_promote_dryrun_reason(full, 0)
check("완비 레코드: complete", p.get("complete"), True)
check("완비 레코드 판정", m.judge_promote_dryrun_reason({}, p)[0], "계약 충족")

promo = write("promo.json", {"promote_dryrun": {
    "status": "promoted", "reason": "beat baseline", "candidate_auc": 0.56}})
check("promoted 는 기준선 없이 통과",
      m.judge_promote_dryrun_reason({}, m.parse_promote_dryrun_reason(promo, 0))[0], "계약 충족")

check("파일 없음 → 판정불가",
      m.judge_promote_dryrun_reason({}, m.parse_promote_dryrun_reason(full + ".nope", 0))[0], "판정불가")
nostatus = write("nostatus.json", {"promote_dryrun": {"reason": "x"}})
check("status 없음 → 판정불가",
      m.judge_promote_dryrun_reason({}, m.parse_promote_dryrun_reason(nostatus, 0))[0], "판정불가")

src = open(MEC, encoding="utf-8").read()
check("디스패치 배선(parser)", "kind == \"promote_dryrun_reason_present\":" in src, True)
check("디스패치 배선(judge)", "return judge_promote_dryrun_reason(item, parsed)" in src, True)

print(f"\n{PASS}/{PASS + FAIL} PASS")
raise SystemExit(1 if FAIL else 0)

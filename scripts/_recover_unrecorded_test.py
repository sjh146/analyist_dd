#!/usr/bin/env python3
"""회귀 테스트 — model_engineer_cycle.recover_unrecorded (2026-11-11 CG196 실사고).

증상(실측 2026-11-11 21:35): wf_label_sweep 이 완주해 요약 JSON 을 썼는데 구동기 프로세스가
append_ledger 전에 사라졌고 pidfile·state 도 이미 정리돼 있어 orphan 검사가 못 잡았다 →
그날 유일한 실험 결과가 원장·스코어보드에서 통째로 빠졌다.

이 테스트는 '요약이 항목 created_at 이후 갱신됐는데 원장 기록이 없으면 복구한다'를
**쓰기 없이(dry_run)** 확인한다.
"""
import os, sys, json, datetime, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_engineer_cycle as M

KST = datetime.timezone(datetime.timedelta(hours=9))
HOST_SUM = os.path.join(M.PROJ, "services/xgboost-ml/reports/overnight/cg196_const_summary.json")

fails = []
def check(name, cond, extra=""):
    print(f"{'PASS' if cond else 'FAIL'} {name}{(' — ' + extra) if extra and not cond else ''}")
    if not cond:
        fails.append(name)

check("요약 파일 존재(전제)", os.path.exists(HOST_SUM), HOST_SUM)

def item(iid, created, cmd=None):
    return {"id": iid, "status": "pending", "metric": "wf_sweep_summary",
            "command": cmd or ("docker exec x sh -c 'python scripts/wf_label_sweep.py --only CG196_ctl_h5,"
                               "CG196_noconst_h5 --summary-out /app/reports/overnight/cg196_const_summary.json'"),
            "created_at": created, "counterfactual": "CG196_ctl_h5 (같은 런)"}

# ① created_at 이 요약 mtime 이전 + 원장 기록 없음 → 복구 대상으로 잡힌다
bk = {"items": [item("TEST_REC_1", "2026-11-11T20:00:00+09:00")]}
got = M.recover_unrecorded(bk, dry_run=True)
check("① 미기록 완주 감지", len(got) == 1 and got[0][0] == "TEST_REC_1", str(got))

# ② created_at 이 요약 mtime 이후(낡은 요약 재사용) → 잡지 않는다
bk2 = {"items": [item("TEST_REC_2", "2030-01-01T00:00:00+09:00")]}
check("② 낡은 요약 오복구 차단", M.recover_unrecorded(bk2, dry_run=True) == [])

# ③ 원장에 이미 기록이 있으면 잡지 않는다(CG196 은 오늘 기록됨)
bk3 = {"items": [item("CG196", "2026-11-11T20:00:00+09:00")]}
check("③ 기존 기록 있는 항목 제외", M.recover_unrecorded(bk3, dry_run=True) == [])

# ④ max_age_h 밖(과거) → 잡지 않는다
check("④ 오래된 요약 제외", M.recover_unrecorded(
    {"items": [item("TEST_REC_4", "2026-11-11T20:00:00+09:00")]}, max_age_h=0, dry_run=True) == [])

# ⑤ 실제 백로그에서 오탐 0 (지금 시점: pending CG198 은 요약이 아직 없다)
real = M.recover_unrecorded(M.load_backlog(), dry_run=True)
check("⑤ 실 백로그 오탐 0", real == [], str(real))

print(f"\n{'ALL PASS' if not fails else 'FAILED: ' + ','.join(fails)} ({5 - len(fails)}/5)")
sys.exit(0 if not fails else 1)

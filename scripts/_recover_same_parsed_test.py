#!/usr/bin/env python3
"""회귀 테스트 — recover_unrecorded 의 '내용 동일' 중복 복구 차단.

왜(실측 2026-11-26 19:07 tick): CG507 요약 JSON 은 `refresh_scorecard_if_stale()` 가 매 틱
재생성해 mtime 만 앞으로 간다. `_same_summary`(mtime 비교)만으로는 못 막아서 16:07 결과가
19:07 에 **두 번째 원장 기록**으로 복구됐고, 그 경로가 return 0 으로 끝나 pending 6건이
그 틱에 실행되지 못했다. 이 테스트는 ①내용 동일 → 스킵 ②내용 다름 → 복구 허용 을 고정한다.

실행: python3 scripts/_recover_same_parsed_test.py
"""
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("mec", os.path.join(HERE, "model_engineer_cycle.py"))
mec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mec)

FAILS = []


def check(name, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'} {name}: got={got!r} want={want!r}")
    if not ok:
        FAILS.append(name)


BASE = {"metric_name": "promote_dryrun_reason_present", "record_id": "CG_DAILY_1125",
        "record_ts": "2026-11-25T22:30:57+09:00", "status": "kept_incumbent",
        "candidate_auc": 0.5199, "complete": False,
        "missing": ["reason", "champion_baseline", "baseline_source"]}

check("같은 내용·다른 mtime → 스킵",
      mec._same_parsed({"parsed": dict(BASE, summary_mtime=1795676998.07)},
                       dict(BASE, summary_mtime=1795687626.44)), True)
check("record_ts 가 다르면 새 결과 → 복구 허용",
      mec._same_parsed({"parsed": dict(BASE, summary_mtime=1.0)},
                       dict(BASE, record_ts="2026-11-26T22:30:00+09:00", summary_mtime=2.0)), False)
check("키 집합이 다르면 복구 허용",
      mec._same_parsed({"parsed": dict(BASE, summary_mtime=1.0)},
                       dict(BASE, new_field=1, summary_mtime=2.0)), False)
check("빈 parsed 는 비교 불가 → 복구 허용(오스킵 금지)",
      mec._same_parsed({"parsed": {}}, dict(BASE)), False)
check("원장 레코드에 parsed 자체가 없으면 복구 허용",
      mec._same_parsed({}, dict(BASE)), False)
check("_same_summary 는 mtime 다르면 False(기존 동작 보존)",
      mec._same_summary({"parsed": dict(BASE, summary_mtime=1795676998.07)}, 1795687626.44), False)

print(f"\n{'ALL PASS' if not FAILS else 'FAILED: ' + ', '.join(FAILS)}")
raise SystemExit(1 if FAILS else 0)

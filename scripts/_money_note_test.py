#!/usr/bin/env python3
"""자체점검 — money_baseline_note(): 틱이 돈 기준(배포 챔피언) 한 줄을 항상 찍는가.

배경(2026-10-09 04:0x, 엔지니어 자율): 틱 헤드라인(스코어보드 best_robust)과 실제 배포
챔피언의 돈 기준 성적은 다른 자로 잰 값이다(헤드라인 arm = 배포 불가 rank 변환 + 누수 패널).
표시 전용 줄이므로 **어떤 입력에서도 예외로 틱을 죽이면 안 된다** — 그 계약을 검사한다.

실행: python3 scripts/_money_note_test.py
통과 기준: 정상 파일 파싱·결측/손상/부분키에서도 예외 없이 문자열 반환.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location(
    "me_cycle", str(REPO / "scripts" / "model_engineer_cycle.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

GOOD = {
    "robust_auc": 0.4908, "expectancy_pct": -0.182, "expectancy_t": -0.81,
    "n_sessions": 87, "n_trades": 1340, "created_at": "2026-10-05T12:37:23",
    "halves": {"front_pct": -0.1066, "back_pct": -0.2557, "stable": "neither"},
}
checks = []


def ok(name, cond, detail=""):
    checks.append((name, bool(cond), detail))


def run(content):
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "robust_oos.json"
        if content is not None:
            p.write_text(content)
        return mod.money_baseline_note(str(p))


s = run(json.dumps(GOOD))
ok("정상 파일 → 값 포함", "0.4908" in s and "-0.182" in s and "87세션" in s, s)
ok("정상 파일 → age 표기", "age" in s, s)
ok("정상 파일 → 판정 아님 명시", "판정 아님" in s, s)
s = run(None)
ok("파일 없음 → 예외 없이 안내", "없음" in s, s)
s = run("{ not json")
ok("손상 JSON → 예외 없이 안내", "없음" in s, s)
s = run(json.dumps({"robust_auc": 0.5}))
ok("부분 키(halves·created_at 없음) → 예외 없이 문자열", isinstance(s, str) and "0.5" in s, s)
s = run(json.dumps({"created_at": "none"}))
ok("created_at 파싱 불가 → age 생략", isinstance(s, str) and "age" not in s, s)

n_fail = 0
for name, c, detail in checks:
    print(f"[{'PASS' if c else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not c else ""))
    n_fail += 0 if c else 1
print(f"\n{len(checks) - n_fail}/{len(checks)} PASS")
sys.exit(1 if n_fail else 0)

#!/usr/bin/env python3
"""_u3_done_check_test.py — u3_done_check 의 자체점검(이 스택엔 pytest 가 없다).

검증 대상(교착 방지의 핵심):
  1) 원장 없음            → 0 (런처 유지)
  2) 실패만 있음(137/1)   → 0  ← 오늘 U3 의 실제 상태(패널만 완성되고 스윕 잘린 밤)
  3) rc=0 기록 존재       → 1 (런처 종료)
  4) 다른 id 의 rc=0      → 0 (id 오염 방지)
  5) 빈 줄·잘린 줄 섞임   → 0 (원장 파서가 죽지 않아야 한다)
  6) rc=0 이지만 rc="0"(문자열) → 0 (타입 불일치를 완료로 오독하지 않는다)
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from u3_done_check import u3_done        # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}: {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        FAILED.append(name)


def write(tmp, records, raw_tail=None):
    p = os.path.join(tmp, "led.jsonl")
    with open(p, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        if raw_tail is not None:
            fh.write(raw_tail)
    return p


with tempfile.TemporaryDirectory() as tmp:
    check("1 원장 없음 → 0", u3_done(os.path.join(tmp, "nope.jsonl")) is False)

    fail_only = [
        {"id": "U3", "rc": 137, "ts": "2026-09-28T02:50:46+09:00"},
        {"id": "U3", "rc": 1, "ts": "2026-09-29T02:29:42+09:00"},
    ]
    check("2 실패만(137/1) → 0", u3_done(write(tmp, fail_only)) is False)

    with_ok = fail_only + [{"id": "U3", "rc": 0, "ts": "2026-09-30T07:12:00+09:00"}]
    check("3 rc=0 존재 → 1", u3_done(write(tmp, with_ok)) is True)

    other_ok = [{"id": "CG38", "rc": 0}, {"id": "L3", "rc": 0}]
    check("4 다른 id 만 rc=0 → 0", u3_done(write(tmp, other_ok)) is False)

    raw = [{"id": "U3", "rc": 137}]
    p = write(tmp, raw, raw_tail='\n\n{"id": "U3", "rc": 0, "broken"')   # 잘린 줄 + 빈 줄
    check("5 빈 줄·잘린 줄 섞임 → 0(파서 생존)", u3_done(p) is False)

    check("6 rc='0'(문자열) → 0", u3_done(write(tmp, [{"id": "U3", "rc": "0"}])) is False)

print(f"\n{'ALL PASS' if not FAILED else str(len(FAILED)) + ' FAIL'}")
sys.exit(1 if FAILED else 0)

#!/usr/bin/env python3
"""failure_cause() 오귀속 정정 검증 (2026-09-27 TR3).

수정 전: 137 이면 StartedAt 을 실행 시작과 비교하지 않고 무조건 "컨테이너 재생성"으로 기록
        → 호스트 부팅 직후 실행(14:54)에도 "평일 20:00 evening_pipeline 탓" 오보.
수정 후: StartedAt > 실행 시작 일 때만 재생성으로 단정.
"""
import sys
from datetime import datetime
sys.path.insert(0, "/home/jhshi/analyist_dd/scripts")
import model_engineer_cycle as m

ok = True

# 케이스 A: 컨테이너 StartedAt(2026-09-27T05:43:17Z = 14:43 KST) < 실행 시작(14:54 KST)
started = datetime(2026, 9, 27, 14, 54, tzinfo=m.KST)
c = m.failure_cause(137, started)
a_ok = ("재생성 아님" in c) and ("Evening" not in c) and ("evening_pipeline 이 원인" not in c)
print(f"[A] StartedAt < 시작 → 재생성 아님 판정: {'PASS' if a_ok else 'FAIL'}")
print("    " + c[:150])
ok &= a_ok

# 케이스 B: 시작 시각 미지정이면 단정하지 않는다
c2 = m.failure_cause(137)
b_ok = "미확정" in c2 or "재생성 아님" in c2
print(f"[B] started 미지정 → 원인 단정 금지: {'PASS' if b_ok else 'FAIL'}")
print("    " + c2[:150])
ok &= b_ok

# 케이스 C: 124(타임아웃)는 그대로
c3 = m.failure_cause(124, started)
c_ok = "timeout(124)" in c3
print(f"[C] rc=124 문구 유지: {'PASS' if c_ok else 'FAIL'}")
ok &= c_ok

print("\n전체:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)

#!/usr/bin/env python3
"""조기 중단 가드 검증 (2026-09-29 U3 실측: 빌드가 100%(32,576/32,576)를 채운 뒤
'빌드 중 피처 코드 변경'으로 저장을 거부당해 밤 5.9시간이 통째로 소실됐다).

검사 대상:
  A) 서명이 도중에 바뀌면 _assert_code_unchanged 가 RuntimeError 를 던진다(빌드 계속 금지)
  B) 그때 체크포인트(.rows.pkl/.meta.json)도 함께 삭제된다(무효 체크포인트 재개 오해 방지)
  C) 서명이 같으면 아무 일도 없다(정상 빌드 회귀 없음)
  D) 서명을 못 읽으면(None) 막지 않는다 — 기존 동작 유지
  E) failure_cause(rc=1, 로그) 가 로그 꼬리에서 실제 예외 줄을 원인으로 적는다
     (종전엔 "종료코드 1" 만 남아 원인 진단에 로그를 다시 열어야 했다)

호스트/컨테이너 어디서든 실행 가능: docker exec stock_xgboost_ml python /app/scripts/_code_churn_abort_test.py
"""
import os
import sys
import tempfile

for p in ("/app", "/home/jhshi/analyist_dd",
          "/home/jhshi/analyist_dd/services/xgboost-ml"):
    if os.path.isdir(p):
        sys.path.insert(0, p)

from app.feature_engine.feature_pipeline import FeaturePipeline          # noqa: E402

sys.path.insert(0, "/home/jhshi/analyist_dd/scripts")
sys.path.insert(0, "/app/scripts")
import model_engineer_cycle as m                                          # noqa: E402

ok = True
fp = FeaturePipeline.__new__(FeaturePipeline)      # __init__ 의존(DB 등) 없이 검사 대상만 쓴다

d = tempfile.mkdtemp(prefix="churn_")
rows = os.path.join(d, "ck.rows.pkl")
meta = os.path.join(d, "ck.meta.json")

sig = FeaturePipeline._feature_code_sig()
print(f"[0] 현재 code_sig = {sig} (None 이면 테스트 무효)")
ok &= sig is not None


def _mk():
    open(rows, "w").write("x")
    open(meta, "w").write("{}")


# A + B: 서명 불일치 → RuntimeError + 체크포인트 삭제
_mk()
try:
    fp._assert_code_unchanged(float(sig) - 1.0, 12345, 32576, rows, meta)
    a_ok, err = False, "(예외가 나지 않음 — 조기 중단 실패)"
except RuntimeError as e:
    err = str(e)
    a_ok = "피처 코드 변경" in err and "12345/32576" in err
print(f"[A] 서명 변경 → RuntimeError: {'PASS' if a_ok else 'FAIL'}")
print("    " + err[:160])
ok &= a_ok
b_ok = (not os.path.exists(rows)) and (not os.path.exists(meta))
print(f"[B] 체크포인트 동반 삭제: {'PASS' if b_ok else 'FAIL'}")
ok &= b_ok

# C: 서명 동일 → 무예외, 체크포인트 보존
_mk()
try:
    fp._assert_code_unchanged(sig, 10, 100, rows, meta)
    c_ok = os.path.exists(rows) and os.path.exists(meta)
    cmsg = "예외 없음"
except RuntimeError as e:
    c_ok, cmsg = False, f"오탐 RuntimeError: {e}"
print(f"[C] 서명 동일 → 통과·체크포인트 보존: {'PASS' if c_ok else 'FAIL'} ({cmsg})")
ok &= c_ok

# D: 서명 None → 막지 않는다
try:
    fp._assert_code_unchanged(None, 10, 100, rows, meta)
    d_ok = True
except RuntimeError as e:
    d_ok = False
print(f"[D] 서명 None → 막지 않음: {'PASS' if d_ok else 'FAIL'}")
ok &= d_ok

# E: failure_cause(rc=1) 이 로그 꼬리에서 예외 줄을 뽑는다
log = os.path.join(d, "run.log")
with open(log, "w", encoding="utf-8") as f:
    f.write("\n".join(f"pad line {i}" for i in range(80)) + "\n")
    f.write("2026-09-28T17:29:40+00:00 panel: (32576, 202) features=210\n")
    f.write("RuntimeError: 빌드 중 피처 코드 변경 감지(code_sig 1790334858.601 → 1790614188.127) "
            "— 혼합 패널 방지를 위해 저장하지 않음.\n")
c1 = m.failure_cause(1, None, log)
e_ok = ("피처 코드 변경" in c1) and ("1790334858.601" in c1)
print(f"[E] rc=1 원인에 실제 예외 줄: {'PASS' if e_ok else 'FAIL'}")
print("    " + c1[:170])
ok &= e_ok
# E2: 로그에 예외가 없으면 단정하지 않는다
log2 = os.path.join(d, "run2.log")
open(log2, "w").write("all good\n")
c2 = m.failure_cause(1, None, log2)
e2_ok = "미발견" in c2
print(f"[E2] 예외 줄 없음 → 단정 금지: {'PASS' if e2_ok else 'FAIL'}")
ok &= e2_ok
# E3: 기존 137/124 문구 회귀 없음
e3_ok = "timeout(124)" in m.failure_cause(124)
print(f"[E3] rc=124 문구 회귀 없음: {'PASS' if e3_ok else 'FAIL'}")
ok &= e3_ok

print("\n전체:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)

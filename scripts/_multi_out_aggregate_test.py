#!/usr/bin/env python3
"""다중 산출(셸 루프) 실험의 판정이 '마지막 파일 하나'가 아니라 전량 집계인지 검증.

문제(2026-11-28 실측): CG537(k3/k5/k10 비용 스트레스)은 세 k 모두 사전문턱을 넘겼는데
구동기는 마지막 파일(k10)만 읽어 '노이즈'로 닫았다. CG539(같은 arm, 창을 매칭창/최근 33세션으로
분해)는 마지막 파일이 매칭창이라 '신호있음'으로 닫혔지만 실제로는 매칭창 Δ+0.307(t3.29) vs
최근 Δ−0.602(t−1.80) 로 부호가 뒤집혀 있었다.

검증(실데이터 사용 — cg539_matched_k5=통과, cg539_recent_k5=미달):
  ① 둘 다 통과 파일 → '신호있음'
  ② 통과 + 미달 → '조건엇갈림'(신호 아님)
  ③ 파일 1개(루프 아님) → None(기존 마지막-파일 경로 유지, 회귀 없음)
  ④ summary_out 명시 항목 → None(항목이 대표 산출을 지정한 기존 계약 우선)
"""
import json
import os
import shutil
import sys
import time

sys.path.insert(0, "/home/jhshi/analyist_dd/scripts")
import model_engineer_cycle as m

SRC = os.path.join(m.PROJ, "services/xgboost-ml/reports/overnight")
PASS_SRC = os.path.join(SRC, "cg539_matched_k5.json")
FAIL_SRC = os.path.join(SRC, "cg539_recent_k5.json")
TMP = "/tmp/_multi_out_agg"
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP, exist_ok=True)


def put(name, src):
    dst = os.path.join(TMP, name)
    shutil.copy2(src, dst)
    t = time.time() + 1
    os.utime(dst, (t, t))
    return dst


ITEM = {
    "id": "TEST-MULTI",
    "metric": "fillable_topk_expectancy",
    "command": ("docker exec x sh -c 'for n in a b; do PYTHONPATH=/app python -u x.py "
                f"--k 5 --json-out {TMP}/out_$n.json; done'"),
}

ok = 0
total = 0


def check(label, cond, info=""):
    global ok, total
    total += 1
    print(("PASS " if cond else "FAIL ") + label + (f" — {info}" if info else ""))
    if cond:
        ok += 1


for f in ("out_a.json", "out_b.json"):
    p = os.path.join(TMP, f)
    if os.path.exists(p):
        os.remove(p)
    put(f, PASS_SRC)
r = m._multi_out_aggregate(ITEM, 0)
check("전부 통과 → 신호있음", r is not None and r[0] == "신호있음", str(r)[:160] if r else "None")

shutil.copy2(FAIL_SRC, os.path.join(TMP, "out_b.json"))
t = time.time() + 1
os.utime(os.path.join(TMP, "out_b.json"), (t, t))
r = m._multi_out_aggregate(ITEM, 0)
check("엇갈림 → 조건엇갈림(신호 아님)",
      r is not None and r[0] == "조건엇갈림" and "신호 1/2" in r[1],
      str(r)[:220] if r else "None")

os.remove(os.path.join(TMP, "out_b.json"))
r = m._multi_out_aggregate(ITEM, 0)
check("파일 1개 → None(기존 마지막-파일 판정 유지)", r is None, str(r)[:120])

put("out_b.json", PASS_SRC)
it2 = dict(ITEM, summary_out=f"{TMP}/out_a.json")
r = m._multi_out_aggregate(it2, 0)
check("summary_out 명시 → None(항목 계약 우선)", r is None, str(r)[:120])

old = os.path.join(TMP, "out_b.json")
os.utime(old, (time.time() - 3600, time.time() - 3600))
r = m._multi_out_aggregate(ITEM, time.time() - 60)
check("mtime_floor 이전 파일 제외 → None", r is None, str(r)[:120])

print(f"\n{ok}/{total} PASS")
sys.exit(0 if ok == total else 1)

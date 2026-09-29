#!/usr/bin/env python3
"""[테스트] 구동기 summary_path 가 커맨드의 `--out` 을 존중하는지 검증.

왜(실측 2026-09-29): champion_robust_eval 경로가 **고정**돼 있어, 같은 metric 을 다른 `--out`
으로 돌리는 항목(CG36 h=1 OOS, CG35 클린 컷오프)은 구동기가 기본 경로만 보게 되고
→ '요약 미갱신 → 실행실패'로 오판하거나 기본 경로 파일(CG31 기준선 산출물)을 덮어쓴다.

실행: python3 scripts/_summary_path_test.py
"""
import importlib.util
import json
import os
import sys

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


DEFAULT = os.path.join(m.PROJ, "services/xgboost-ml/reports/champion_robust_eval.json")

# 1) 회귀: metric 만 주면 종전과 같은 기본 경로
check("기본 경로(회귀)", m.summary_path("champion_robust_eval"), DEFAULT)
check("wf_sweep 경로(회귀)", m.summary_path("wf_sweep_summary").endswith(
    "reports/overnight/wf_label_sweep_summary.json"), True)

# 2) --out 을 컨테이너 절대경로로 준 경우 → 호스트 경로로 변환
cmd = ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 2700 "
       "python scripts/champion_robust_eval.py --horizon 1 --out /app/reports/champion_oos_h1.json'")
check("--out 컨테이너 경로", m.summary_path("champion_robust_eval", cmd),
      os.path.join(m.PROJ, "services/xgboost-ml/reports/champion_oos_h1.json"))

# 3) --out= 형식 + 호스트 절대경로
check("--out= 형식", m.summary_path("champion_robust_eval", "x --out=/app/reports/a.json"),
      os.path.join(m.PROJ, "services/xgboost-ml/reports/a.json"))
check("호스트 절대경로 유지", m.summary_path("champion_robust_eval", "--out /tmp/a.json"),
      "/tmp/a.json")

# 4) 실제 백로그 항목: champion_robust_eval 을 쓰는 pending 항목의 경로가 유효한가
b = json.load(open(os.path.join(m.PROJ, "docs/QUANT_MODEL_BACKLOG.json")))
n = 0
for it in b["items"]:
    if it.get("metric") == "champion_robust_eval" and it.get("status") == "pending":
        p = m.summary_path("champion_robust_eval", it.get("command"))
        n += 1
        check(f"{it['id']} 요약 경로 부모 존재", os.path.isdir(os.path.dirname(p)), True)
        check(f"{it['id']} 경로가 기본경로와 다름(산출물 보존)", p != DEFAULT, True)
check("검사한 pending 항목 수", n >= 1, True)

# 5) 알 수 없는 metric 은 ValueError
try:
    m.summary_path("nope")
    check("알 수 없는 metric", "no-raise", "ValueError")
except ValueError:
    check("알 수 없는 metric", "ValueError", "ValueError")

print(f"\n{PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)

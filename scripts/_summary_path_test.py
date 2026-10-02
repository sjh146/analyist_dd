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

# 4) wf_sweep_summary 도 --summary-out 을 존중해야 한다 (2026-10-02 수리).
#    왜: 종전엔 기본 경로를 고정 반환했다 → --summary-out 을 준 실행(CG66/67/70 계열)은
#    기본 파일을 건드리지 않으므로 구동기가 '요약 미갱신 → 실행실패'로 오판하고,
#    반대로 기본 경로를 덮어써 다른 실험의 산출물(기록 기준선 요약)을 잃는다.
SWEEP_DEFAULT = os.path.join(m.PROJ, "services/xgboost-ml/reports/overnight/wf_label_sweep_summary.json")
SWEEP_CMD = ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 2700 "
             "python -u scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_420_asof3.npz "
             "--only A,B --summary-out /app/reports/overnight/cg70_summary.json'")
check("wf_sweep --summary-out 컨테이너 경로", m.summary_path("wf_sweep_summary", SWEEP_CMD),
      os.path.join(m.PROJ, "services/xgboost-ml/reports/overnight/cg70_summary.json"))
check("wf_sweep --summary-out 없으면 기본경로(회귀)",
      m.summary_path("wf_sweep_summary", "python scripts/wf_label_sweep.py --only A"), SWEEP_DEFAULT)
check("wf_sweep 기본경로와 --summary-out 경로가 다름(산출물 보존)",
      m.summary_path("wf_sweep_summary", SWEEP_CMD) != SWEEP_DEFAULT, True)

# 4) 실제 백로그 항목: champion_robust_eval 을 쓰는 **미완료** 항목의 경로가 유효한가
#    (2026-09-29 수리: 예전 필터는 status=="pending" 만 봤다 → 대기 항목이 하루만 없어도
#     n=0 이 되어 '검사한 항목 수' 가 FAIL 로 뜨는 시각의존 테스트였다. 코드 회귀가 아니라
#     백로그 상태 변화인데도 빨간불이 켜지므로, 미완료(pending/backlog/needs_setup) 전체를 본다.)
b = json.load(open(os.path.join(m.PROJ, "docs/QUANT_MODEL_BACKLOG.json")))
n = 0
skipped_no_cmd = []
for it in b["items"]:
    if it.get("metric") == "champion_robust_eval" and it.get("status") in (
            "pending", "backlog", "needs_setup"):
        # command 가 아직 없는 항목(미착수 설계 단계)은 '경로 미정'이지 회귀가 아니다.
        # 실측(2026-09-29 22:5x): FS1 이 needs_setup(command=None)으로 등록되면서 이 검사가
        # 빨간불을 켰다 — 검사 대상은 "실행 명령이 이미 있는데 기본 산출물을 덮는가"다.
        # (지침: command 를 채울 때 `--out /app/reports/<item>.json` 을 반드시 넣어라.)
        if not it.get("command"):
            skipped_no_cmd.append(it["id"])
            continue
        p = m.summary_path("champion_robust_eval", it.get("command"))
        n += 1
        check(f"{it['id']} 요약 경로 부모 존재", os.path.isdir(os.path.dirname(p)), True)
        check(f"{it['id']} 경로가 기본경로와 다름(산출물 보존)", p != DEFAULT, True)
if n >= 1:
    check("검사한 미완료 항목 수", n >= 1, True)
else:
    # 왜 FAIL 이 아닌가(2026-10-01 실측): 지금 미완료 항목 중 champion_robust_eval 을 쓰는 것이
    # 하나도 없으면(모두 done / 다른 metric) 검사할 대상이 없는 것뿐이다 — 코드 회귀가 아니다.
    # 백로그 상태에 의존하는 검사는 '검사 대상 없음'과 '회귀'를 구분해야 한다(2026-09-29 교훈).
    print("NOTE: 미완료 항목 중 champion_robust_eval 을 쓰는 항목이 없어 경로 검사를 건너뜀")
if skipped_no_cmd:
    print(f"NOTE: 명령 미정(command=None)으로 건너뜀 — {', '.join(skipped_no_cmd)} "
          f"(착수 시 --out 필수)")

# 5) 알 수 없는 metric(또는 metric 없음)은 **예외 없이 빈 경로** — 크래시 금지.
#    왜(2026-09-30): 백로그의 metric 없는 항목(진단·준비)을 --start 하면 종전 ValueError 로
#    원장 기록 없이 죽었다(설계원칙 4 위반). 이제는 "요약 없음 → 판정불가" 로 정직하게 끝난다.
try:
    p_unknown = m.summary_path("nope")
    check("알 수 없는 metric 은 빈 경로(크래시 금지)", p_unknown, "")
    check("metric 없음도 빈 경로", m.summary_path(""), "")
    check("빈 경로는 '요약 없음'으로 판정", os.path.exists(p_unknown), False)
except Exception as e:
    check("알 수 없는 metric 은 빈 경로(크래시 금지)", f"raised {type(e).__name__}", "")

print(f"\n{PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)

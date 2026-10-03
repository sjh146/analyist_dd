"""CG80 자체점검 — 학습 목적함수(objective) 축 배선 검증.

왜 순수 파이썬인가: 이 스택에는 pytest 가 없다(skill 실측 교훈). PASS/FAIL 만 출력한다.

검사 항목:
  [1] _rank_order_and_groups: order 정렬·cut 그룹경계 스냅·group 합 == 행 수·날짜 경계 미분할
  [2] 단일 날짜(퇴화) 입력에서도 예외 없이 유효한 그룹을 만든다
  [3] RPb_*/RPr_* config 존재 + arm↔대조가 **objective 만** 다르다(다른 필드는 전부 동일)
  [4] 랭킹 arm 은 단일 모델(ens.skip)이어야 한다는 가드가 config 에 반영돼 있다
  [5] 프로덕션 무변경: XGBoostModel.train 의 신규 인자 기본값이 전부 None

실행: docker exec -e PYTHONPATH=/app stock_xgboost_ml python /app/scripts/_rank_objective_test.py
"""
import sys
import os

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

import numpy as np

FAILS = []


def check(cond, msg):
    print(("PASS  " if cond else "FAIL  ") + msg)
    if not cond:
        FAILS.append(msg)


# ── import ────────────────────────────────────────────────────────────────
try:
    import importlib.util
    spec = importlib.util.spec_from_file_location("wfls", "/app/scripts/wf_label_sweep.py")
    wfls = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wfls)
    IMP = True
    IMPERR = ""
except Exception as e:  # pragma: no cover
    IMP = False
    IMPERR = f"{type(e).__name__}: {e}"

check(IMP, f"[0] wf_label_sweep import 성공 — {IMPERR}")

if not IMP:
    print(f"\nFAIL {len(FAILS)}건 (import 실패)")
    sys.exit(1)

# ── [1] 정렬·그룹 분할 불변식 ───────────────────────────────────────────────
dates = (["2026-01-01"] * 7 + ["2026-01-02"] * 3 + ["2026-01-03"] * 5
         + ["2026-01-04"] * 4 + ["2026-01-05"] * 6)
order, cut, gc_t, gc_v = wfls._rank_order_and_groups(dates, 0.67)
d = np.asarray(dates)[order]
check(list(d) == sorted(d), "[1] order 가 날짜 오름차순이다")
check(int(gc_t.sum()) == cut, f"[1] gc_t 합 == cut ({int(gc_t.sum())} vs {cut})")
check(int(gc_v.sum()) == len(dates) - cut,
      f"[1] gc_v 합 == val 행수 ({int(gc_v.sum())} vs {len(dates) - cut})")
check(int(gc_t.sum()) + int(gc_v.sum()) == len(dates), "[1] 두 그룹 합 == 전체 행 수")
# 경계 미분할: d[:cut] 의 마지막 날짜가 d[cut:] 에 다시 나타나면 안 된다
check(d[:cut][-1] not in set(d[cut:]), "[1] 같은 날짜가 train/val 에 걸치지 않는다")
# 각 날짜의 그룹 카운트가 실제 행 수와 일치
u, c = np.unique(d[:cut], return_counts=True)
check(list(c) == [int(x) for x in gc_t], "[1] gc_t 가 train 구간 날짜별 개수와 일치")
u, c = np.unique(d[cut:], return_counts=True)
check(list(c) == [int(x) for x in gc_v], "[1] gc_v 가 val 구간 날짜별 개수와 일치")

# ── [2] 퇴화 입력 ─────────────────────────────────────────────────────────
o2, c2, gt2, gv2 = wfls._rank_order_and_groups(["2026-01-01"] * 5, 0.67)
check(int(gt2.sum()) + int(gv2.sum()) == 5 and 0 < c2 < 5,
      f"[2] 단일 날짜 입력에서도 유효(cut={c2})")

# ── [3] config 대조 ───────────────────────────────────────────────────────
CFGS = {c["id"]: c for c in wfls.CONFIGS}
for suf in ["00_30", "30_60", "60_90", "90_120", "120_150"]:
    a = f"RPr_{suf}"
    b = f"RPb_{suf}"
    check(a in CFGS and b in CFGS, f"[3] {a}/{b} 존재")
    if a in CFGS and b in CFGS:
        A, B = CFGS[a], CFGS[b]
        check(A.get("objective") == "rank:pairwise", f"[3] {a} objective=rank:pairwise")
        check(B.get("objective") == "binary:logistic", f"[3] {b} objective=binary:logistic")
        diff = {k for k in set(A) | set(B) if A.get(k) != B.get(k)}
        check(diff == {"objective", "id", "desc"},
              f"[3] {a}↔{b} 차이가 objective/id/desc 뿐 — 실제 차이 {sorted(diff)}")
        check(A.get("ens", {}).get("skip") == ["lightgbm", "catboost"],
              f"[3] {a} ens.skip 이 lgb/cat 을 뺀다(랭킹=단일모델)")
        check(A.get("codes_slice") == B.get("codes_slice"), f"[3] {a} codes_slice 동일")
        check(A.get("select") == B.get("select") == "top30", f"[3] {a} select 동일")

# ── [4] 프로덕션 무변경 ───────────────────────────────────────────────────
try:
    from app.models.xgboost_model import XGBoostModel
    import inspect
    sig = inspect.signature(XGBoostModel.train)
    defaults = {k: v.default for k, v in sig.parameters.items()
                if v.default is not inspect.Parameter.empty}
    check(defaults.get("group", "MISSING") is None, "[4] train group 기본값 None")
    check(defaults.get("objective", "MISSING") is None, "[4] train objective 기본값 None")
    check(defaults.get("sample_weight", "MISSING") is None, "[4] train sample_weight 기본값 None")
except Exception as e:
    check(False, f"[4] XGBoostModel import/서명 확인 실패 — {type(e).__name__}: {e}")

print()
if FAILS:
    print(f"FAIL {len(FAILS)}건:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("ALL PASS")
sys.exit(0)

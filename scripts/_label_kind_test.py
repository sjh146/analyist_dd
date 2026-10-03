"""_label_kind_test.py — champion_robust_eval 의 --label-kind 축 자체점검 (pytest 없음).

왜: CG36(h=1 시장상대 중앙값) 0.4719 를 "챔피언의 자기 과제 점수"로 읽으면 오독이다.
이 스크립트의 기본 라벨은 시장상대 중앙값이고, 챔피언이 학습한 라벨은 절대 1일 방향이다
(retrain_champion._create_labels). --label-kind abs 를 신설하면서 ① 기본값(rel) 동작이
비트 동일한지 ② abs 가 r>0 기준인지 ③ 단일값 라벨 가드가 실제로 존재하는지 확인한다.

2026-10-04 CG92: kind='quantile'(+ --label-q) 계약이 추가됐다 —
  ④ quantile 은 상위 q=1/하위 q=0/가운데 None(채점 제외) ⑤ q 범위 검증.

실행: docker exec stock_xgboost_ml python /app/scripts/_label_kind_test.py
"""
import importlib.util
import statistics
import sys

FAIL = []
PASS = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(f"{name} — {detail}")


spec = importlib.util.spec_from_file_location(
    "cre", "/app/scripts/champion_robust_eval.py" if __import__("os").path.exists(
        "/app/scripts/champion_robust_eval.py") else "scripts/champion_robust_eval.py")
cre = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cre)

rets = [-0.02, -0.01, 0.0, 0.01, 0.03]

# 1) 기본값(rel)은 기존 프로토콜(중앙값 초과=1)과 동일해야 한다
med = statistics.median(rets)
legacy = [1 if r > med else 0 for r in rets]
check("기본 rel = 중앙값 초과", cre._make_labels(rets) == legacy, f"{cre._make_labels(rets)} vs {legacy}")
check("kind='rel' 명시 = 기본", cre._make_labels(rets, "rel") == legacy)

# 2) abs 는 r>0 기준 (0.0 은 양성 아님)
check("abs = r>0", cre._make_labels(rets, "abs") == [0, 0, 0, 1, 1],
      f"{cre._make_labels(rets, 'abs')}")

# 3) 두 라벨이 실제로 다른 케이스(하락일: 중앙값 −0.02 이면 r>med 가 4개, abs 는 0개)
down = [-0.05, -0.04, -0.03, -0.02]
check("하락일엔 두 라벨이 정반대", cre._make_labels(down, "rel") == [0, 0, 1, 1]
      and cre._make_labels(down, "abs") == [0, 0, 0, 0], f"rel={cre._make_labels(down, 'rel')}")

# 4) 단일값 가드: abs 라벨이 전부 0/1 이면 set 크기 1 → 채점에서 버려져야 한다
allup = [0.01, 0.02, 0.03]
check("양봉일 abs 라벨 단일값", len(set(cre._make_labels(allup, "abs"))) == 1,
      "전부 1 → AUC 정의 불가(코드가 건너뜀)")
check("양봉일 rel 라벨은 2값", len(set(cre._make_labels(allup, "rel"))) == 2)

# 4b) quantile(2026-10-04 CG92): 상위 q=1 / 하위 q=0 / 가운데 None
q_rets = [float(i) for i in range(100)]          # 0..99, q=0.05 → 상위 5 = 1, 하위 5 = 0
y_q = cre._make_labels(q_rets, "quantile", 0.05)
n_one = sum(1 for v in y_q if v == 1)
n_zero = sum(1 for v in y_q if v == 0)
n_none = sum(1 for v in y_q if v is None)
check("quantile 꼬리 분할(상위 q=1·하위 q=0·가운데 None)",
      n_one == 5 and n_zero == 5 and n_none == 90, f"1={n_one} 0={n_zero} None={n_none}")
check("quantile 은 라벨 종류가 rel/abs 와 다름(전체가 2값이 아님)",
      len(set(v for v in y_q if v is not None)) == 2 and None in y_q)
_bad = False
try:
    cre._make_labels(q_rets, "quantile", 0.9)
    _bad = True
except ValueError:
    pass
try:
    cre._make_labels(q_rets, "quantile", None)
    _bad = True
except ValueError:
    pass
check("quantile q 범위/null 검증", not _bad, "q=0.9·None 이 예외를 내지 않음")

# 5) 인자 등록 확인(CLI 계약)
import argparse  # noqa: E402
src = open(cre.__file__).read()
_Q = chr(34)                                     # 큰따옴표(이스케이프 표류 방지)
check("CLI --label-kind 등록",
      ("--label-kind" in src) and (f"choices=({_Q}rel{_Q}, {_Q}abs{_Q}, {_Q}quantile{_Q})" in src))
check("CLI --label-q 등록", "label_q" in src and "--label-q" in src)
check("기본값 rel 유지", f"default={_Q}rel{_Q}" in src)
check("payload 에 label_kind 기록", f"{_Q}label_kind{_Q}: args.label_kind" in src)
check("payload 에 label_q 기록", f"{_Q}label_q{_Q}:" in src)

print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
for p in PASS:
    print("  PASS:", p)
for f in FAIL:
    print("  FAIL:", f)
sys.exit(1 if FAIL else 0)

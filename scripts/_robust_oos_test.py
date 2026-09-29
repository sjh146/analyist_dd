#!/usr/bin/env python3
"""[테스트] champion_robust_eval 의 학습구간 오염 차단 로직 검증.

배경(2026-09-29 실측 CG34): 배포 챔피언(학습 2026-06-25~09-23)의 5개 평가창 중 2개가
학습구간과 겹쳐 창평균 0.5302 가 오염됐다(겹친 창 0.5883 vs 학습구간 밖 0.4914).
새 규칙: **창 전체가 학습구간 시작일 이전일 때만** 채점한다.

실행(컨테이너 안): docker exec stock_xgboost_ml python /app/scripts/_robust_oos_test.py
"""
import importlib.util
import json
import os
import sys
import tempfile

spec = importlib.util.spec_from_file_location("cre", "/app/scripts/champion_robust_eval.py")
cre = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cre)

PASS = FAIL = 0


def check(name, got, want):
    global PASS, FAIL
    ok = got == want
    PASS, FAIL = PASS + ok, FAIL + (not ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}: got={got} want={want}")


CG31_WINDOWS = [
    ["2025-11-28", "2025-12-10", "2026-01-20"],
    ["2026-01-28", "2026-02-15", "2026-03-23"],
    ["2026-03-31", "2026-04-20", "2026-05-20"],
    ["2026-05-29", "2026-06-30", "2026-07-20"],
    ["2026-07-28", "2026-08-20", "2026-09-15"],
]

# 1) CG31 실측 창 + 학습구간 2026-06-25~09-23 → 앞 3창만 OOS
oos, bad = cre._split_oos(CG31_WINDOWS, "2026-06-25", "2026-09-23")
check("CG31 OOS 창 수", len(oos), 3)
check("CG31 제외 창 수", len(bad), 2)
check("CG31 제외 창 (창4 시작)", bad[0][0], "2026-05-29")
check("CG31 OOS 마지막 창 끝", oos[-1][-1], "2026-05-20")

# 1b) 컷오프를 과거로 고정한 모델(2026-02-19~05-20) → 앞 창1 + **이후** 창4·5 가 OOS
oos, bad = cre._split_oos(CG31_WINDOWS, "2026-02-19", "2026-05-20")
check("컷오프 모델 OOS 창 수(앞1 + 이후2)", len(oos), 3)
check("컷오프 모델 첫 OOS 창", oos[0][0], "2025-11-28")
check("컷오프 모델 이후 OOS 창 시작", oos[1][0], "2026-05-29")
check("컷오프 모델 제외 창 수(겹침 2개)", len(bad), 2)

# 1c) train_end 를 모르면 '이후 창'은 안전하게 제외(미래 창 오판 방지)
oos, bad = cre._split_oos(CG31_WINDOWS, "2026-02-19", None)
check("train_end 미지정 → 이후 창 제외", (len(oos), len(bad)), (1, 4))

# 2) 경계: 학습시작 하루 전에 끝나면 OOS, 같은 날 끝나면 제외
oos, bad = cre._split_oos([["2026-06-01", "2026-06-24"], ["2026-06-25"]], "2026-06-25", "2026-09-23")
check("경계(하루 전) OOS", len(oos), 1)
check("경계(당일 끝) 제외", len(bad), 1)

# 2b) 경계: 학습 종료 '다음 날' 시작하면 OOS
oos, bad = cre._split_oos([["2026-05-21"], ["2026-05-20"]], "2026-02-19", "2026-05-20")
check("경계(종료 다음날) OOS", len(oos), 1)
check("경계(종료 당일) 제외", len(bad), 1)

# 3) 빈 창은 제외로 분류(런타임 크래시 방지)
oos, bad = cre._split_oos([[]], "2026-06-25", "2026-09-23")
check("빈 창 제외", (len(oos), len(bad)), (0, 1))

# 4) meta 자동 판정
with tempfile.TemporaryDirectory() as d:
    check("meta 없음 → (None,None)", cre._meta_range(d), (None, None))
    with open(os.path.join(d, "training-result-20260101-000000.json"), "w") as f:
        json.dump({"n_rows": 1}, f)
    check("meta 에 날짜 없음 → (None,None)", cre._meta_range(d), (None, None))
    with open(os.path.join(d, "training-result-20260102-000000.json"), "w") as f:
        json.dump({"n_rows": 1, "data_start": "2026-05-20", "data_end": "2026-09-23"}, f)
    check("meta 학습구간 읽기", cre._meta_range(d), ("2026-05-20", "2026-09-23"))

# 5) 기본값 회귀: 인자 기본이 None(현행 동작 보존)
src = open("/app/scripts/champion_robust_eval.py").read()
check("--train-start 기본 None", '"--train-start", default=None' in src, True)
check("--train-end 기본 None", '"--train-end", default=None' in src, True)

print(f"\n{PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)

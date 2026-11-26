#!/usr/bin/env python3
"""회귀 테스트 — champion_robust_eval._split_oos 의 clip_partial(CG350).

요구: ① 기본값(clip_partial=False)은 **옛 구현과 비트 동일**해야 한다(기존 기준선 수치 불변).
② clip_partial=True 는 학습 종료일을 가로지르는 창만 학습 이후 날짜로 잘라 OOS 에 살린다.
③ 학습 시작을 가로지르는 창·완전 겹침 창은 절단해도 제외(부분 관측을 OOS 로 오독하지 않는다).

실행(컨테이너 안에서 — champion_robust_eval 이 app.* 를 임포트한다):
  docker exec stock_xgboost_ml sh -c 'cd /app && python3 scripts/_split_oos_clip_test.py'
"""
import importlib.util
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("cre", os.path.join(HERE, "champion_robust_eval.py"))
cre = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cre)

FAILS = []


def check(name, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'} {name}: got={got!r} want={want!r}")
    if not ok:
        FAILS.append(name)


def old_split(windows, train_start, train_end):
    def keep(w):
        if not w:
            return False
        if w[-1] < train_start:
            return True
        return bool(train_end) and w[0] > train_end
    return [w for w in windows if keep(w)], [w for w in windows if not keep(w)]


random.seed(7)
dates = [f"2026-{m:02d}-{d:02d}" for m in range(1, 13) for d in range(1, 29)]
mismatch = 0
for _ in range(200):
    n = random.randint(1, 6)
    ix = sorted(random.sample(range(len(dates) - 30), n))
    wins = [dates[i:i + random.randint(3, 15)] for i in ix]
    ts = random.choice(dates)
    te = random.choice(dates)
    if cre._split_oos(wins, ts, te) != old_split(wins, ts, te):
        mismatch += 1
check("기본값 = 옛 구현 비트 동일(200회)", mismatch, 0)

wins = [
    ["2026-01-05", "2026-01-06", "2026-01-07"],
    ["2026-07-01", "2026-07-02", "2026-07-03", "2026-07-06", "2026-07-07"],
    ["2026-08-03", "2026-08-04", "2026-08-05"],
    ["2026-06-01", "2026-06-02", "2026-06-03"],
    ["2026-05-20", "2026-06-02", "2026-06-03"],
]
keep, ex = cre._split_oos(wins, "2026-06-01", "2026-07-03", clip_partial=True)
check("절단 ON: 채점 창 개수", len(keep), 3)
check("절단 ON: 가로지른 창은 학습 이후만 남는다", keep[1], ["2026-07-06", "2026-07-07"])
check("절단 ON: 제외는 겹침 2건(완전겹침·시작가로지름)", len(ex), 2)
check("절단 ON: 학습 시작 가로지르는 창은 제외 유지", wins[4] in ex, True)

keep2, ex2 = cre._split_oos([["2026-07-02", "2026-07-03"]], "2026-06-01", "2026-07-03",
                            clip_partial=True)
check("절단 후 1일뿐 → 제외", (len(keep2), len(ex2)), (0, 1))

keep3, ex3 = cre._split_oos([["2026-07-01", "2026-07-02"]], "2026-06-01", None, clip_partial=True)
check("train_end 없음 → 미래 창 채점 불가 유지", (len(keep3), len(ex3)), (0, 1))

print(f"\n{'ALL PASS' if not FAILS else 'FAILED: ' + ', '.join(FAILS)}")
raise SystemExit(1 if FAILS else 0)

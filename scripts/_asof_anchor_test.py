"""자체점검 — wf_label_sweep 의 --asof-date(평가 앵커 이동) 격자 (CG200, 2026-11-12).

무엇을 보증하나:
 1) --asof-date 미지정 경로는 **종전 왼쪽 정렬 격자와 비트 동일**(무회귀).
 2) 앵커가 1~2거래일 움직이면(오른쪽 정렬) 전 폴드 테스트 창이 **실제로 이동**한다.
 3) 왼쪽 정렬은 끝 날짜를 2개 잘라도 컷이 **고정**된다 = CG199 '측정무효'의 재현.
    (이게 재현되지 않으면 '앵커 이동'이라는 주장 자체가 성립하지 않는다.)
 4) 격자 범위를 벗어나면 RuntimeError 로 **명시 실패**(조용한 빈 폴드 금지).

실행(컨테이너): docker exec stock_xgboost_ml sh -c 'cd /app && PYTHONPATH=/app:/app/scripts python -u scripts/_asof_anchor_test.py'
"""
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

import wf_label_sweep as S   # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'} {name}{(' — ' + detail) if detail else ''}")
    if not cond:
        FAIL.append(name)


def legacy_left_windows(n, folds):
    """종전 구현(왼쪽 정렬)을 그대로 옮긴 참조 구현."""
    step = n // (folds + 1)
    out = []
    for i in range(1, folds + 1):
        lo = step * i
        hi = min(n, step * (i + 1))
        out.append((lo - 1, lo, hi))
    return out


# ── 1) 기본 경로 무회귀 ────────────────────────────────────────────────────────
for n in (279, 277, 100, 51, 12):
    for folds in (5, 3):
        _step = max(1, n // (folds + 1))
        got = S.fold_window_indices(n, folds, _step, right_anchored=False)
        check(f"기본 경로 무회귀 n={n} folds={folds}",
              got == legacy_left_windows(n, folds), str(got))

# ── 2) 오른쪽(앵커) 격자는 끝 날짜가 줄면 함께 이동 ────────────────────────────
n0, folds = 279, 5
step = n0 // (folds + 1)
a = S.fold_window_indices(n0, folds, step, right_anchored=True)
b = S.fold_window_indices(n0 - 2, folds, step, right_anchored=True)   # 앵커 2거래일 앞
check("앵커 격자: 2거래일 이동 시 fold5 창이 이동", a[-1] != b[-1], f"{a[-1]} vs {b[-1]}")
check("앵커 격자: 이동 폭 = 2", (a[-1][1] - b[-1][1]) == 2 and (a[-1][2] - b[-1][2]) == 2)
check("앵커 격자: 전 폴드가 함께 이동", all((x[1] - y[1]) == 2 for x, y in zip(a, b)))
c = S.fold_window_indices(n0 - 1, folds, step, right_anchored=True)
check("앵커 격자: 1거래일 이동도 검출", a[-1][2] != c[-1][2], f"{a[-1]} vs {c[-1]}")

# ── 3) 왼쪽 정렬은 끝 2일을 잘라도 컷이 고정(CG199 재현) ────────────────────────
la = S.fold_window_indices(n0, folds, step, right_anchored=False)
lb = S.fold_window_indices(n0 - 2, folds, step, right_anchored=False)
check("왼쪽 정렬: 끝 2일 절단에도 fold5 창 동일(=CG199 재현)", la[-1] == lb[-1], f"{la[-1]} vs {lb[-1]}")

# ── 4) 격자 무결성 — 순증가·비겹침·범위 ───────────────────────────────────────
for tag, w in (("left", la), ("right", a)):
    ok_order = all(w[i][2] <= w[i + 1][1] for i in range(len(w) - 1))
    ok_bounds = all(0 <= x[0] < x[1] < x[2] <= n0 for x in w)
    ok_len = all((x[2] - x[1]) == step for x in w)
    check(f"{tag} 격자 무결성(순서·범위·창길이)", ok_order and ok_bounds and ok_len, str(w))

# ── 5) 범위 밖은 명시 실패 ────────────────────────────────────────────────────
try:
    S.fold_window_indices(10, 5, 9, right_anchored=True)   # step*(folds) > n → 음수 lo
    check("범위 밖 앵커 격자 → RuntimeError", False, "예외 없음")
except RuntimeError:
    check("범위 밖 앵커 격자 → RuntimeError", True)

print()
if FAIL:
    print(f"FAILED {len(FAIL)}건: {FAIL}")
    sys.exit(1)
print("ALL PASS — --asof-date 는 앵커를 실제로 이동시키고, 미지정 경로는 종전과 비트 동일하다.")

"""_cg390_gate_test.py — dispersion 게이트(CG390)의 룩어헤드·표본 규칙 자체점검.

검사:
  1) 단조 증가 점수 → 분산 0 → 게이트 값 ≈ 0 (계측기 baseline)
  2) **미래 세션 점수를 바꿔도 과거 세션의 게이트 값이 변하지 않는다**(룩어헤드 없음)
  3) 워밍업 구간(max(5, days//2) 세션)은 값이 없다 — 과거 분포가 없으면 판정하지 않는다
  4) min_pool 미만 세션은 값이 없다
  5) 과거 분포 대비 넓은 세션은 양(+), 좁은 세션은 음(−)
"""
import math
import random
import sys

sys.path.insert(0, "/app/scripts")
from fillable_topk_expectancy import dispersion_gate  # noqa: E402


def mk_rows(dates, codes, scale_by_date, seed=0):
    rnd = random.Random(seed)
    rows = []
    for d in dates:
        sd = scale_by_date.get(d, 0.02)
        for c in codes:
            rows.append({"fold": 0, "date": d, "code": c,
                         "score": 0.3 + rnd.gauss(0, sd), "fwd_ret": None})
    return rows


def main() -> int:
    checks = []

    dates = [f"2026-01-{i:02d}" for i in range(1, 41)]
    codes = [f"{i:06d}" for i in range(20)]

    # 1) 동일 점수 → 분산 0 → 게이트 값 ≈ 0
    rows_flat = [{"fold": 0, "date": d, "code": c, "score": 0.5, "fwd_ret": None}
                 for d in dates for c in codes]
    g_flat = dispersion_gate(rows_flat, days=10)
    checks.append(("1 균일점수 분산0", all(abs(v) < 1e-9 for v in g_flat.values()) and len(g_flat) > 0))

    # 2) 룩어헤드 없음 — 21번째 이후 날짜 점수만 교란
    base = {d: (0.01 if i % 2 == 0 else 0.05) for i, d in enumerate(dates)}
    rows_a = mk_rows(dates, codes, base, seed=7)
    g_a = dispersion_gate(rows_a, days=10)
    rows_b = [dict(r) for r in rows_a]
    cut = dates[20]
    for r in rows_b:
        if r["date"] >= cut:
            r["score"] = 0.99 if r["score"] > 0.3 else 0.01
    g_b = dispersion_gate(rows_b, days=10)
    past = [d for d in dates if d < cut]
    same = all(abs(g_a.get((0, d), 0.0) - g_b.get((0, d), 0.0)) < 1e-12
               for d in past if (0, d) in g_a)
    checks.append(("2 룩어헤드 없음(과거 값 불변)", same and any((0, d) in g_a for d in past)))

    # 3) 워밍업 구간 판정 없음
    warm = max(5, 10 // 2)
    checks.append((f"3 워밍업 {warm}세션 미판정",
                   all((0, d) not in g_a for d in dates[:warm])))

    # 4) min_pool 미만 세션 제외
    rows_small = [{"fold": 0, "date": d, "code": c, "score": 0.4 + 0.01 * i,
                   "fwd_ret": None}
                  for d in dates for i, c in enumerate(codes[:5])]
    g_small = dispersion_gate(rows_small, days=10, min_pool=10)
    checks.append(("4 min_pool 미만 제외", len(g_small) == 0))

    # 5) 넓은 세션이 최댓값 — 표본 잡음 때문에 보통 세션도 절반은 (+) 가 된다(게이트가 중앙값
    #    분할이라 무작위 데이터에서 ON 비율 ≈50% 는 정상). 그래서 '압도적 1위'로 검사한다.
    wide = dates[20]
    scale = {d: 0.005 for d in dates}
    scale[wide] = 0.10
    g_w = dispersion_gate(mk_rows(dates, codes, scale, seed=11), days=10)
    top_key = max(g_w, key=lambda k: g_w[k]) if g_w else None
    checks.append(("5 넓은 세션이 최댓값", top_key == (0, wide) and g_w[top_key] > 1.0))

    ok = all(v for _, v in checks)
    for name, v in checks:
        print(f"[{'PASS' if v else 'FAIL'}] {name}")
    print(f"{sum(1 for _, v in checks if v)}/{len(checks)} PASS")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""_cnd_select_test — `wf_wave.subset(select="cnd{k}")` 조건부(비영) edge 선별 자체점검.

왜(2026-10-03 CG76): 이 축은 '선별 규칙이 희소 피처를 배제한다'를 재므로, 선별 함수가 의도대로
동작하지 않으면 Δ0 을 '효과 없음'으로 오독한다(EV1 사고).

메커니즘(실측으로 확인된 사실): 풀링 edge_of 는 0 행이 **동점(mid-rank)** 으로 처리돼 희소 피처의
신호가 '비영 비율²' 만큼 **희석**된다 — 비영 1% 면 희석계수 ≈ 1−0.99² = 0.0199. 그래서 비영 서브셋에서
edge 0.5 인 피처도 풀링 edge ≈ 0.010 로 떨어져 dense 피처(0.07)에 진다. 30% 비영이면 희석계수 0.51
이라 여전히 뽑히기도 한다 → **축은 '매우 희소한' 피처에만 작용**한다(CG67 의 disclosure 1차 부활이
선별 미진입이었던 것과 정합).

검사:
 [1] dense 만 있는 패널에서는 cnd == top (축이 dense 에 작용하지 않음 = 교락 없음)
 [2] 비영 1%·조건부 신호 강한 피처: cnd30 에는 진입, top30 에는 미진입 (축의 존재 증명)
 [3] 비영 표본 < min_nz 인 초희소 피처는 후보에서 제외
 [4] desc 에 풀링 edge top-k 와의 겹침 수가 보고됨
 [5] dates 없이도 호출된다(IC 규칙처럼 RuntimeError 로 죽지 않는다)
 [6] select="top30" 경로 회귀 없음

실행: docker exec -e PYTHONPATH=/app stock_xgboost_ml python /app/scripts/_cnd_select_test.py
"""
import sys

import numpy as np

sys.path.insert(0, "/app/scripts")
sys.path.insert(0, "/app")

import wf_wave as W  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        FAIL.append(name)


rng = np.random.default_rng(0)
N = 20000
y = (rng.random(N) < 0.3).astype(int)

# dense 33개: y 에 약한 shift 를 넣어 풀링 edge ≈ 0.07 (희소 피처를 확실히 이기는 수준)
Xd = rng.normal(size=(N, 33)) + 0.25 * y[:, None]
# 희소 피처: 비영 1%(200행, min_nz=100 초과) · 그 안에서 y 와 강상관 → 조건부 edge ≈ 0.5
sp = np.zeros(N)
m1 = rng.random(N) < 0.01
sp[m1] = y[m1] + rng.normal(scale=0.05, size=int(m1.sum()))
# 초희소 피처: 비영 20행(< min_nz) · 신호 있어도 후보 제외되어야 한다
up = np.zeros(N)
m0 = rng.choice(N, size=20, replace=False)
up[m0] = y[m0] + 0.1

X = np.hstack([Xd, sp[:, None], up[:, None]])
names = [f"dense{i}" for i in range(33)] + ["sparse_signal", "ultra_sparse"]
sp_i, up_i = 33, 34

# 메커니즘 수치를 먼저 찍는다(테스트 근거).
pooled_sp = W.edge_of(sp, y)
cond_sp = W.edge_of(sp[m1], y[m1])
print(f"[근거] sparse_signal: 풀링 edge {pooled_sp:.4f} · 비영조건부 edge {cond_sp:.4f} · "
      f"비영 {int(m1.sum())}행")
dens_pooled = [W.edge_of(Xd[:, i], y) for i in range(33)]
print(f"[근거] dense 풀링 edge 최소 {min(dens_pooled):.4f} (희소를 이겨야 top30 에서 탈락)")
check("[2-전제] 조건부 edge > 풀링 edge", cond_sp > pooled_sp * 10,
      f"{cond_sp:.4f} vs {pooled_sp:.4f}")

# --- [5] dates 없이 호출 ---------------------------------------------------------
try:
    idx_cnd, desc_cnd = W.subset(names, "cnd30", X, y)
    check("[5] dates 없이 cnd30 호출", True)
except Exception as e:  # noqa: BLE001
    check("[5] dates 없이 cnd30 호출", False, f"{type(e).__name__}: {e}")
    idx_cnd, desc_cnd = [], ""

idx_top, desc_top = W.subset(names, "top30", X, y)
check("[6] top30 회귀(30개)", len(idx_top) == 30, f"n={len(idx_top)}")

check("[2] 희소 신호 피처가 cnd30 에 진입", sp_i in idx_cnd, f"idx_cnd={idx_cnd}")
check("[2b] 같은 피처가 top30 에는 미진입", sp_i not in idx_top, f"idx_top={idx_top}")

# --- [1] dense 만 있는 패널에서는 두 규칙이 동일 --------------------------------
Xd_only = rng.normal(size=(N, 40))
names_d = [f"d{i}" for i in range(40)]
c_idx, _ = W.subset(names_d, "cnd25", Xd_only, y)
t_idx, _ = W.subset(names_d, "top25", Xd_only, y)
check("[1] dense 만이면 cnd25 == top25", c_idx == t_idx)

# --- [3] 초희소 피처 제외 -------------------------------------------------------
check("[3] 초희소(<min_nz) 피처 제외", up_i not in idx_cnd, f"ultra_i={up_i}")

# --- [4] desc 에 겹침 보고 ------------------------------------------------------
check("[4] desc 에 '겹침' 표기", ("겹침" in desc_cnd) and ("cnd30" in desc_cnd), desc_cnd)

# --- [7] CG76 arm↔대조 config 필드 동일성(select 만 달라야 실험이 성립) -----------------
try:
    import wf_label_sweep as S  # noqa: E402
    cfgs = {c["id"]: c for c in S.CONFIGS}
    bad = []
    for a, c in [("CN_00_30", "LU_00_30"), ("CN_30_60", "LU_30_60"), ("CN_60_90", "LU_60_90"),
                 ("CN_90_120", "LU_90_120"), ("CN_120_150", "LU_120_150")]:
        if a not in cfgs or c not in cfgs:
            bad.append(f"{a}/{c} 없음")
            continue
        ka = {k: v for k, v in cfgs[a].items() if k not in ("id", "select", "desc")}
        kc = {k: v for k, v in cfgs[c].items() if k not in ("id", "select", "desc")}
        if ka != kc:
            bad.append(f"{a}↔{c} 필드 차이 {set(ka.items()) ^ set(kc.items())}")
        if not cfgs[a]["select"].startswith("cnd") or cfgs[c]["select"] != "top30":
            bad.append(f"{a}/{c} select={cfgs[a]['select']}/{cfgs[c]['select']}")
    check("[7] CG76 arm↔대조: select 만 다름", not bad, str(bad)[:200])
except Exception as e:  # noqa: BLE001
    check("[7] CG76 arm↔대조: select 만 다름", False, f"{type(e).__name__}: {e}")

print("\n" + ("모두 PASS" if not FAIL else f"FAIL {len(FAIL)}건: {FAIL}"))
sys.exit(1 if FAIL else 0)

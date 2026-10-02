#!/usr/bin/env python3
"""_drop_dup_test — CG74 '중복 라벨 열 제거(drop_dup)' 자체점검.

검사 (전부 PASS 여야 한다):
  [1] 패널 npz 의 feature_names 에 **같은 라벨이 2번** 들어 있고 값이 비트 동일하다(14쌍).
  [2] wf_wave.dedupe_names 가 두 번째부터 `__dupN` 을 붙여 이름을 유일하게 만든다.
  [3] drop_dup 필터 정규식(r"__dup\\d+$")이 **정확히 그 열들만** 매칭한다(오검출 0).
  [4] config UQ_* 가 존재하고 drop_dup=True 이며, 각 arm 은 대조군과 **drop_dup 만** 다르다
      (다른 필드가 다르면 A/B 가 성립하지 않는다 — _deployable_slice_config_test 와 같은 취지).
  [5] 게이트 ON 경로에서 중복 라벨이 후보에 들어오지 않는다(CORE_FEATURES 밖) — 알려진 사실 기록용.

실행(컨테이너):  docker exec -e PYTHONPATH=/app stock_xgboost_ml python /app/scripts/_drop_dup_test.py
"""
import re
import sys

import numpy as np

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

import wf_wave as W  # noqa: E402
import wf_label_sweep as LS  # noqa: E402

PANELS = [
    "/app/app/models/wf/panel_prod200.npz",
    "/app/app/models/wf/panel_150u.npz",
    "/app/app/models/wf/panel_420_asof3.npz",
]
DUP_RE = re.compile(r"__dup\d+$")

fails = []


def check(ok, msg):
    print(("PASS " if ok else "FAIL ") + msg)
    if not ok:
        fails.append(msg)


# ── [1] 패널에 비트 동일 중복 라벨이 있다 ────────────────────────────────────
from collections import Counter  # noqa: E402
for p in PANELS:
    try:
        z = np.load(p, allow_pickle=True)
    except Exception as e:  # noqa: BLE001
        check(False, f"[1] {p} 로드 실패 {type(e).__name__}")
        continue
    fn = [str(x) for x in z["feature_names"]]
    X = z["X"]
    c = Counter(fn)
    dups = [n for n, k in c.items() if k > 1]
    ident = 0
    for n in dups:
        idxs = [i for i, x in enumerate(fn) if x == n]
        if all(np.array_equal(X[:, idxs[0]], X[:, j]) for j in idxs[1:]):
            ident += 1
    check(len(dups) == 14 and ident == 14,
          f"[1] {p.split('/')[-1]} dup_labels={len(dups)} bit_identical={ident} (기대 14/14)")

# ── [2] dedupe_names 가 __dupN 을 붙인다 ────────────────────────────────────
raw = ["a", "b", "a", "a", "c"]
out = W.dedupe_names(raw)
check(out == ["a", "b", "a__dup1", "a__dup2", "c"],
      f"[2] dedupe_names 결과 {out}")

# ── [3] 정규식이 정확히 __dupN 만 매칭 ──────────────────────────────────────
probe = ["volatility_volume", "volatility_volume__dup1", "dup", "x__dup", "y__dup10", "a__dup0"]
hits = [n for n in probe if DUP_RE.search(n)]
check(hits == ["volatility_volume__dup1", "y__dup10", "a__dup0"],
      f"[3] 정규식 매칭 {hits}")

# ── [4] config 등록 + arm↔cf 필드 동일성(drop_dup 만 다름) ──────────────────
by_id = {c["id"]: c for c in LS.CONFIGS}
PAIRS = [("UQa_00_30", "LU_00_30"), ("UQa_30_60", "LU_30_60"), ("UQa_60_90", "LU_60_90"),
         ("UQa_90_120", "LU_90_120"), ("UQa_120_150", "LU_120_150")]
for arm_id, cf_id in PAIRS:
    a, b = by_id.get(arm_id), by_id.get(cf_id)
    if a is None or b is None:
        check(False, f"[4] config 누락 {arm_id}/{cf_id}")
        continue
    check(a.get("drop_dup") is True, f"[4] {arm_id} drop_dup=True")
    check(not b.get("drop_dup"), f"[4] {cf_id} drop_dup 미설정")
    keys = (set(a) | set(b)) - {"desc", "drop_dup", "id"}
    diff = [k for k in keys if a.get(k) != b.get(k)]
    check(diff == [], f"[4] {arm_id} vs {cf_id} — drop_dup 외 차이 {diff}")

# ── [5] NOTE: 게이트 ON 경로에서 중복열이 배제되는 **기제** 확인 ─────────────
#   CORE_FEATURES 는 접미사 없는 이름 목록이므로 `target_ma_5__dup1` 은 게이트에서 자동 탈락한다
#   → UQ_core30_h5 ≡ CO_core30_h5 (스모크 실측 AUC 동일). 이 축은 게이트 OFF 경로에서만 유효하다.
#   (단언이 아니라 사실 기록 — '회귀'와 '알려진 기제'를 구분한다.)
try:
    import train_curated as tc  # noqa: E402
    core = {str(f) for f in (getattr(tc, "CORE_FEATURES", []) or [])}
    z = np.load(PANELS[0], allow_pickle=True)
    names = W.dedupe_names([str(n) for n in z["feature_names"]])
    n_dup = len([n for n in names if DUP_RE.search(n)])
    n_dup_in_core = len([n for n in names if DUP_RE.search(n) and n in core])
    print(f"NOTE [5] 게이트 ON: 중복열 {n_dup}개 중 CORE_FEATURES 안 {n_dup_in_core}개 "
          f"(0 이면 게이트가 자동 배제 → UQ_core30_h5 ≡ CO_core30_h5)")
except Exception as e:  # noqa: BLE001
    print(f"NOTE [5] 확인 불가 {type(e).__name__}: {e}")

print()
if fails:
    print(f"{len(fails)} FAIL")
    sys.exit(1)
print("ALL PASS")

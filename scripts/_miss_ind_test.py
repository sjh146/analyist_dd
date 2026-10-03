#!/usr/bin/env python3
"""_miss_ind_test — CG78 '피처 결측 지시자(missing indicator)' 자체점검.

검사 (전부 PASS 여야 한다):
  [1] config MIs_00_30..MIs_120_150 5개가 존재하고 id 가 유일하다.
  [2] 각 MIs 는 대조군 LU_* 와 (kind·horizon·q·select·codes_slice) 가 같고, 차이는 `derived` 뿐이다
      (다른 필드가 다르면 짝 Δ 가 '설정 차이'를 재게 된다).
  [3] MIs 의 derived.kind == "miss" 이고 top_k 가 양수다.
  [4] 기능: add_derived(kind="miss") 가 na_<src> 열을 만들고 값 == isnan(원본) 이며,
      이름 목록이 base_names + na_* 로 확장된다.
  [5] 사전 등록 규칙: sources 미지정 시 top_k 개를 고르고, 그 결측률이 1~90% 범위다.
  [6] 회귀: kind 미지정(기존 CG25) 경로는 Δk 파생(d{k}_{src})을 그대로 만든다.
  [7] 가드: 중복 라벨로 열 수가 부풀면 즉시 RuntimeError(조용한 오정의 방지).

실행(컨테이너):  docker exec -e PYTHONPATH=/app stock_xgboost_ml python /app/scripts/_miss_ind_test.py
"""
import ast
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

import wf_label_sweep as LS  # noqa: E402

PATH = "/app/scripts/wf_label_sweep.py"
SLICES = [(0, 30), (30, 60), (60, 90), (90, 120), (120, 150)]
MI_IDS = [f"MIs_{a:02d}_{b:02d}" for a, b in SLICES]
LU_IDS = [f"LU_{a:02d}_{b:02d}" for a, b in SLICES]
SAME_FIELDS = ("kind", "horizon", "q", "select", "codes_slice")

fails = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}{(' — ' + detail) if detail else ''}")
    if not cond:
        fails.append(name)


# ── AST 로 config 원문을 읽는다(런타임 CONFIGS 와 별개로 파일 자체를 검사) ──────────
with open(PATH, "r", encoding="utf-8") as fh:
    tree = ast.parse(fh.read())
cfgs = []
for node in ast.walk(tree):
    if isinstance(node, ast.Dict):
        try:
            d = {k.value: ast.literal_eval(v) for k, v in zip(node.keys, node.values)
                 if isinstance(k, ast.Constant) and isinstance(k.value, str)}
        except Exception:
            continue
        if "id" in d:
            cfgs.append(d)
by_id = {}
dups = []
for c in cfgs:
    if c["id"] in by_id:
        dups.append(c["id"])
    by_id[c["id"]] = c

check("config id 유일", not dups, f"중복={dups}")
check("[1] MIs_* 5개 존재", all(i in by_id for i in MI_IDS),
      f"누락={[i for i in MI_IDS if i not in by_id]}")
check("[2] 대조군 LU_* 5개 존재", all(i in by_id for i in LU_IDS),
      f"누락={[i for i in LU_IDS if i not in by_id]}")

for a, b in SLICES:
    mi, lu = by_id.get(f"MIs_{a:02d}_{b:02d}"), by_id.get(f"LU_{a:02d}_{b:02d}")
    if not mi or not lu:
        continue
    diff = {k for k in set(mi) | set(lu) if mi.get(k) != lu.get(k)}
    check(f"[2] MIs_{a:02d}_{b:02d} ↔ LU_{a:02d}_{b:02d} 차이가 derived 뿐",
          diff <= {"derived", "desc", "id"}, f"차이={sorted(diff)}")
    check(f"[3] MIs_{a:02d}_{b:02d}.derived.kind == miss",
          isinstance(mi.get("derived"), dict) and mi["derived"].get("kind") == "miss"
          and int(mi["derived"].get("top_k") or 0) > 0,
          f"derived={mi.get('derived')}")
    check(f"[2b] LU_{a:02d}_{b:02d} 는 derived·core_only 없음",
          not lu.get("derived") and not lu.get("core_only"))

# ── 기능 검사 ────────────────────────────────────────────────────────────────────
rng = np.random.default_rng(7)
n = 600
cols = {}
names = [f"f{i}" for i in range(6)]
for j, nm in enumerate(names):
    v = rng.normal(size=n)
    if j < 3:                      # 결측률 5% / 30% / 60% — 사전규칙 대상
        mask = rng.random(n) < (0.05, 0.30, 0.60)[j]
        v = v.astype(float)
        v[mask] = np.nan
    else:                          # 결측 없는 피처
        v = v.astype(float)
    cols[nm] = v
cols["f_dup"] = rng.normal(size=n)
df = pd.DataFrame(cols)
base_names = names + ["f_dup"]

out, ext = LS.add_derived(df, base_names, {"kind": "miss", "top_k": 3})
new = [c for c in ext if c not in base_names]
check("[4] na_* 3개 생성", len(new) == 3, f"new={new}")
check("[6] 기존 base_names 보존", ext[:len(base_names)] == list(base_names))
ok_val = all(np.array_equal(out[c].values, np.isnan(df[c[3:]].values).astype(float))
             for c in new)
check("[4] 값 == isnan(원본)", ok_val)

# 사전규칙: 결측률 내림차순 → f2(0.60) · f1(0.30) · f0(0.05)
check("[5] 사전규칙 top3 == [f2,f1,f0]", new == ["na_f2", "na_f1", "na_f0"], f"new={new}")
fracs = [np.isnan(df[c[3:]].values).mean() for c in new]
check("[5] 결측률 1~99.5% 범위", all(0.01 <= f <= 0.995 for f in fracs),
      f"fracs={[round(f, 3) for f in fracs]}")

# 회귀: kind 미지정 = 기존 Δk 경로
out2, ext2 = LS.add_derived(df.assign(stock_code="A"), base_names,
                            {"sources": ["f0", "f1"], "lags": [1]})
check("[6] Δk 회귀(d1_f0·d1_f1)", [c for c in ext2 if c not in base_names] == ["d1_f0", "d1_f1"],
      f"new={[c for c in ext2 if c not in base_names]}")

# 가드: 중복 라벨로 열이 부풀면 즉시 실패
df_dup = pd.concat([df, df[["f0"]]], axis=1)
try:
    LS.add_derived(df_dup, base_names, {"kind": "miss", "top_k": 3})
    check("[7] 중복 라벨 가드", False, "RuntimeError 미발생")
except RuntimeError as e:
    check("[7] 중복 라벨 가드", "열 수 불일치" in str(e), str(e)[:80])

# 잘못된 kind 는 즉시 실패(조용한 폴백 금지)
try:
    LS.add_derived(df, base_names, {"kind": "bogus", "sources": ["f0"]})
    check("[7b] 알 수 없는 kind 즉시 실패", False, "RuntimeError 미발생")
except RuntimeError as e:
    check("[7b] 알 수 없는 kind 즉시 실패", "알 수 없음" in str(e), str(e)[:60])

print()
if fails:
    print(f"FAIL {len(fails)}건: {fails}")
    sys.exit(1)
print("ALL PASS")

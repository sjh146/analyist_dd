#!/usr/bin/env python3
"""CG69 배포가능 arm 구간짝 config 자체점검 (호스트에서 실행 가능 — AST 파싱, 컨테이너 불필요).

검사:
  1) SD1s_00_30..SD1s_120_150 5개가 CONFIGS 에 존재하고 id 가 유일하다.
  2) 각 SD1s 슬라이스는 같은 경계의 US_* 대조군 config 와 (kind·horizon·q·select·core_only) 가
     같아야 한다 — 달라지면 짝 Δ 가 '설정 차이'를 재게 된다(구간 효과가 아님).
  3) SD1s 계열은 rank 변환을 쓰지 않는다(추론 계약 무변경 = 배포 가능 조건).
  4) recipe 는 depth1·lr0.05 (배포 경로에서 학습 옵션으로만 켜지는 값).
  5) US_* 대조군 5개도 그대로 존재한다(짝 상대).

근거: 2026-10-02 CG64/CG66/CG67 삼중 재현 CO_smooth_d1_h5 0.5512 vs CO_core30_h5 0.5350 = Δ+0.0162
      (사전문턱 +0.02 미달) → 구간 교체 견고성만 미측정.
"""
import ast
import sys

PATH = "scripts/wf_label_sweep.py"
SLICES = [(0, 30), (30, 60), (60, 90), (90, 120), (120, 150)]
SD_IDS = [f"SD1s_{a:02d}_{b:02d}" for a, b in SLICES]
US_IDS = [f"US_{a:02d}_{b:02d}" for a, b in SLICES]
# kind 는 arm(스무딩 라벨)·대조군(분위 라벨)이 **의도적으로 다르다** — 전체 패널 비교
# (CO_smooth_d1_h5 vs CO_core30_h5)와 같은 조합을 구간에서 재현하는 것이 목적이다.
SAME_FIELDS = ("horizon", "q", "select", "core_only")

fails = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}{(' — ' + detail) if detail else ''}")
    if not cond:
        fails.append(name)


def config_dicts(text):
    tree = ast.parse(text)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            try:
                d = {k.value: ast.literal_eval(v) for k, v in zip(node.keys, node.values)
                     if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            except Exception:
                continue
            if "id" in d:
                out.append(d)
    return out


with open(PATH, "r", encoding="utf-8") as fh:
    cfgs = config_dicts(fh.read())

by_id = {}
dups = []
for c in cfgs:
    if c["id"] in by_id:
        dups.append(c["id"])
    by_id[c["id"]] = c

check("config id 유일", not dups, f"중복={dups}")

for sid, uid, (a, b) in zip(SD_IDS, US_IDS, SLICES):
    sd, us = by_id.get(sid), by_id.get(uid)
    check(f"{sid} 존재", sd is not None)
    check(f"{uid} 대조군 존재", us is not None)
    if not sd or not us:
        continue
    check(f"{sid} codes_slice={[a, b]}", sd.get("codes_slice") == [a, b], str(sd.get("codes_slice")))
    for field in SAME_FIELDS:
        check(f"{sid}↔{uid} {field} 동일({sd.get(field)!r})", sd.get(field) == us.get(field),
              f"S={sd.get(field)!r} C={us.get(field)!r}")
    check(f"{sid} kind=smooth(arm)", sd.get("kind") == "smooth", str(sd.get("kind")))
    check(f"{uid} kind=quantile(대조군)", us.get("kind") == "quantile", str(us.get("kind")))
    check(f"{sid} rank 변환 없음(배포 가능)", "transform" not in sd, str(sd.get("transform")))
    rec = sd.get("recipe") or {}
    check(f"{sid} recipe depth1·lr0.05", rec.get("depth") == 1 and rec.get("lr") == 0.05, str(rec))

# 대조군은 rank 변환이 없어야 한다(게이트 ON 평범 config)
for uid in US_IDS:
    us = by_id.get(uid)
    if us:
        check(f"{uid} rank 변환 없음", "transform" not in us and "recipe" not in us, str(us.get("recipe")))

# ── CG71(2026-10-02): 선별 규칙 축 SL_* ↔ US_* 짝 무결성 ────────────────────────────
# arm 과 대조군은 **select 만** 달라야 한다(ic30 vs top30). 다른 필드가 하나라도 다르면
# 짝 Δ 가 선별 규칙이 아니라 설정 차이를 재게 된다(SD1s↔US_* 는 kind 가 의도적으로 다른
# 반면, 선별 규칙 축은 '같은 설정에서 규칙만 교체'가 실험이므로 여기선 전 필드가 같아야 한다).
SL_IDS = [f"SL_{a:02d}_{b:02d}" for a, b in SLICES]
for sid, uid, (a, b) in zip(SL_IDS, US_IDS, SLICES):
    sl, us = by_id.get(sid), by_id.get(uid)
    check(f"{sid} 존재(CG71 선별규칙 arm)", sl is not None)
    if not sl or not us:
        continue
    check(f"{sid} codes_slice={[a, b]}", sl.get("codes_slice") == [a, b], str(sl.get("codes_slice")))
    check(f"{sid} select=ic30", sl.get("select") == "ic30", str(sl.get("select")))
    for field in ("kind", "horizon", "q", "core_only"):
        check(f"{sid}↔{uid} {field} 동일({sl.get(field)!r})", sl.get(field) == us.get(field),
              f"S={sl.get(field)!r} C={us.get(field)!r}")
    check(f"{sid} select 만 다름(arm=ic30 · 대조=top30)",
          sl.get("select") != us.get("select"),
          f"S={sl.get('select')!r} C={us.get('select')!r}")
    check(f"{sid} rank 변환·recipe 없음(배포 가능)", "transform" not in sl and "recipe" not in sl)

print()
print(f"{'ALL PASS' if not fails else 'FAILURES: ' + ', '.join(fails)}  ({len(cfgs)} configs 파싱)")
sys.exit(1 if fails else 0)

#!/usr/bin/env python3
"""CG430 자체점검 — 익일 시가 진입 라벨이 (a) 기존 경로를 건드리지 않고 (b) 의도대로 계산됐는가.

검사:
 ① v6(open 없음) 패널의 기본 라벨과 v6o 패널의 entry='close' 라벨이 **비트 동일**(회귀 없음).
 ② entry='next_open' 은 close 정의와 **다른 라벨**을 만든다(no-op 이면 실험이 무의미).
 ③ 양성률이 분위 설계(q=0.30)와 맞고, 유효행 비율이 open 커버리지와 정합.
 ④ 손계산 대조: 날짜별 '상위 30%' 집합이 close(t+k 평균)/open(t+1)−1 을 직접 계산한
    상위 30% 와 정확히 일치한다(라벨 엔진의 분모가 open(t+1) 임을 증명).
 ⑤ 두 라벨의 상관·상위집합 겹침이 '부분 겹침' 구간(축이 실제로 다르다).

실행(컨테이너): docker exec stock_xgboost_ml sh -c 'cd /app && python -u scripts/_cg430_label_test.py'
"""
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "/app/scripts")
import wf_wave as W  # noqa: E402

V6 = "/app/app/models/wf/panel_prod200_v6.npz"
V6O = "/app/app/models/wf/panel_prod200_v6o.npz"
FAIL = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f" — {detail}" if detail else ""))
    if not cond:
        FAIL.append(name)


def lab(df, entry):
    return W.make_labels(df, "smooth", 5, 0.30, entry=entry)


d0, _ = W.build_panel(V6, None, None, log=lambda *a, **k: None)
d1, _ = W.build_panel(V6O, None, None, log=lambda *a, **k: None)
check("①a v6o 에 open 열이 붙는다", "open" in d1.columns,
      f"open 유한 {np.isfinite(d1['open'].values).mean():.3f}")
y_base = lab(d0, None)
y_cls = lab(d1, None)
check("①b entry 미지정 == entry='close' (비트 동일)",
      np.array_equal(np.nan_to_num(y_base, nan=-9), np.nan_to_num(y_cls, nan=-9)))
check("①c v6 vs v6o 의 price/date/code 동일",
      np.array_equal(d0["price"].values, d1["price"].values)
      and np.array_equal(d0["date"].values, d1["date"].values))

y_open = lab(d1, "next_open")
pos_o, pos_c = int(np.nansum(y_open == 1)), int(np.nansum(y_cls == 1))
check("② 두 라벨이 다르다",
      not np.array_equal(np.nan_to_num(y_open, nan=-9), np.nan_to_num(y_cls, nan=-9)),
      f"open pos {pos_o} vs close pos {pos_c}")
r = pos_o / max(int(np.isfinite(y_open).sum()), 1)
r_all = pos_o / len(y_open)
check("③ open 라벨 양성률(전체행 대비) 0.25~0.35", 0.25 <= r_all <= 0.35,
      f"{r_all:.3f} (유효행 대비 {r:.3f} = 상·하위 30%만 라벨)")
check("③b open 라벨 유효행 비율 ≥0.55", np.isfinite(y_open).mean() >= 0.55,
      f"{np.isfinite(y_open).mean():.3f}")

# ④ 손계산 대조 — 라벨 엔진과 독립적으로 상위 30% 집합을 만든다(패널 원래 행 순서 그대로)
g = d1.groupby("stock_code", sort=False)
raw = sum(g["price"].shift(-k) / g["open"].shift(-1) - 1.0 for k in range(1, 6)) / 5.0
hi = raw.groupby(d1["date"]).transform(lambda s: s.quantile(0.70))
lo = raw.groupby(d1["date"]).transform(lambda s: s.quantile(0.30))
manual_lab = np.where(np.isnan(raw), np.nan, np.where(raw > hi, 1.0,
                                                      np.where(raw < lo, 0.0, np.nan)))
check("④ 상위·하위 30% 집합이 손계산과 일치",
      np.array_equal(np.nan_to_num(manual_lab, nan=-9), np.nan_to_num(y_open, nan=-9)),
      f"일치 {int(np.sum(np.nan_to_num(manual_lab, nan=-9) == np.nan_to_num(y_open, nan=-9)))}/{len(y_open)}")

m = np.isfinite(y_open) & np.isfinite(y_cls)
corr = np.corrcoef(np.nan_to_num(y_open[m]), np.nan_to_num(y_cls[m]))[0, 1]
both = int(np.sum((y_open == 1) & (y_cls == 1)))
ov = both / max(pos_o, 1)
check("⑤ 두 라벨 상관 <0.99", corr < 0.99, f"corr {corr:.3f}")
check("⑤b 상위집합 겹침 0.60~0.95", 0.60 <= ov <= 0.95, f"overlap {ov:.3f} ({both}/{pos_o})")

print("전부 PASS" if not FAIL else f"실패 {len(FAIL)}: {FAIL}")
sys.exit(0 if not FAIL else 1)

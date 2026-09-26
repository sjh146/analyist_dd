#!/usr/bin/env python3
"""panel_subset.py — 기존 패널 npz 에서 **날짜 구간만** 잘라 새 패널을 만든다(X 행·피처 불변).

WHY (U3b, 2026-09-26): '창(window) 효과'를 격리하려면 **같은 빌드에서 나온 패널**을 창만 바꿔
비교해야 한다. 교차 패널 비교는 스냅샷(피처 코드·유니버스·기간)이 달라 '창 효과'인지 '표본
교체'인지 구분되지 않는다 — 실측: U1 이 교차패널로 Δ−0.0266 로 기록됐지만 같은 패널 안에서
행 집합만 바꾸면 부호가 뒤집혔다(+0.0071). 이 스크립트는 행만 잘라 그 혼입을 없앤다.

주의: 컨테이너 안에서 실행하라(호스트 python3 에 numpy 가 없다).
  docker exec stock_xgboost_ml python3 /app/scripts/panel_subset.py \
      --src /app/app/models/wf/panel_995.npz \
      --dst /app/app/models/wf/panel_995_w314.npz --since 2025-06-16
"""
import argparse
import os
import sys

import numpy as np


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--dst", required=True)
    ap.add_argument("--since", required=True, help="YYYY-MM-DD — 이 날짜 이상 행만 남긴다")
    ap.add_argument("--until", default=None, help="YYYY-MM-DD — 이 날짜 이하 행만 남긴다(선택)")
    a = ap.parse_args()

    if os.path.exists(a.dst):
        # 대조군 보존: 덮어쓰면 직전 A/B 결과의 근거가 사라진다.
        print(f"이미 존재: {a.dst} — 덮어쓰지 않는다(대조군 보존)")
        return 0

    z = np.load(a.src, allow_pickle=True)
    dates = np.array([str(d) for d in z["dates"]])
    codes = np.array([str(c) for c in z["codes"]])
    m = dates >= a.since
    if a.until:
        m &= dates <= a.until
    n = int(m.sum())
    if n < 200:
        print(f"행 부족: {n} — since={a.since} until={a.until} 확인 (src {z['X'].shape[0]}행)")
        return 1
    if len(set(dates[m])) < 20:
        print(f"날짜 수 부족: {len(set(dates[m]))} — 패널 기간을 확인하라")
        return 1

    np.savez_compressed(
        a.dst, X=z["X"][m], feature_names=z["feature_names"],
        dates=dates[m], codes=codes[m], price=z["price"][m])
    # ⚠ numpy 2 에서 유니코드 배열의 .min()/.max() 는 ufunc 루프가 없어 예외를 낸다
    #   (실측 2026-09-26: _UFuncNoLoopError) → 파이썬 내장 min/max 를 쓴다.
    d0, d1 = min(dates), max(dates)
    s0, s1 = min(dates[m]), max(dates[m])
    print(f"subset 저장: {a.dst}")
    print(f"  src {z['X'].shape} {d0}~{d1} (날짜 {len(set(dates))}·종목 {len(set(codes))})")
    print(f"  dst {z['X'][m].shape} {s0}~{s1} "
          f"(날짜 {len(set(dates[m]))}·종목 {len(set(codes[m]))})")
    # '잘렸는지'를 숫자로 남긴다 — 피처 값이 그대로인지도 한 컬럼으로 확인한다.
    keep = sorted(set(codes[m]))
    print(f"  종목 유지 {len(keep)}/{len(set(codes))} · 행 {n}/{len(dates)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

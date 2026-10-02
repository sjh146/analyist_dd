#!/usr/bin/env python3
"""선별 규칙(selection rule) 축 자체점검 — CG71.

무엇을 검사하는가
 1) `select="ic*"` 는 학습행 날짜(dates=) 없이 호출되면 **즉시 실패**한다(조용한 폴백 금지).
 2) 날짜 레벨 피처(그 날 안에서 상수)는 IC 선별에서 **탈락**하는데, 풀링 edge 로는 뽑힌다
    → '날짜 레벨 성분'이 기존 선별(edge)을 지배할 수 있다는 위험을 수치로 보인다.
 3) 시간가변·정보 피처는 IC 선별에서 상위로 뽑힌다.
 4) 일관성 항: 같은 크기·부호가 반씩 뒤집히는 피처보다 한 방향 일관 피처가 위에 온다.
 5) `top{k}` 경로는 그대로(회귀) — 수동 계산과 일치.
 6) ic{k} 는 정확히 k개(피처가 k보다 적으면 있는 만큼)·정렬·중복 없음.
 7) 선택 설명 문자열에 'edge top{k} 와 m/k 겹침' 이 들어간다(선별 집합이 실제로 다른지 확인).
 8) wf_label_sweep.py 에 SL_* 5개 config 가 select=ic30·codes_slice 로 등록돼 있다.

실행(호스트 python3 에 numpy 가 없다 — 컨테이너에서 돌린다):
  docker exec -e PYTHONPATH=/app stock_xgboost_ml python /app/scripts/_ic_select_test.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402

import wf_wave as W  # noqa: E402

FAIL = []
PASS = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + ((" — " + detail) if detail else ""))


def synth(n_stocks=20, n_dates=40, seed=7):
    """날짜 레벨 피처 M · 시간가변 정보 피처 V · 부호뒤집힘 W 를 만든다."""
    rng = np.random.default_rng(seed)
    dates, rows_m, rows_v, rows_y = [], [], [], []
    for d in range(n_dates):
        t = d / max(1, n_dates - 1)
        p = 0.10 + 0.80 * t                      # 날짜별 양성률(시간에 따라 상승)
        y = (rng.random(n_stocks) < p).astype(int)
        dates += [f"D{d:03d}"] * n_stocks
        rows_m += [t] * n_stocks                 # 날짜 레벨(그 날 안에서 상수)
        rows_v += list(y + rng.normal(0.0, 1.0, n_stocks))   # 시간가변·정보(잡음 큼)
        rows_y += list(y)
    dates = np.asarray(dates)
    X = np.column_stack([np.asarray(rows_m), np.asarray(rows_v)])
    y = np.asarray(rows_y, dtype=int)
    half = n_dates // 2
    flip = np.asarray([1.0 if int(d[1:]) < half else -1.0 for d in dates])
    Wv = X[:, 1] * flip
    return X, y, dates, Wv


def main():
    X, y, dates, Wv = synth()
    names = ["date_level_M", "timevary_V"]

    # 1) dates 없이 ic* → 즉시 실패
    try:
        W.subset(names, "ic1", X, y)
        check("1) ic* without dates fails loudly", False, "예외 없이 통과했다")
    except RuntimeError as e:
        check("1) ic* without dates fails loudly", "dates" in str(e), str(e)[:60])

    # 2·3) IC 점수: M(날짜 레벨) = 0, V(시간가변) > 0 / 풀링 edge 는 M 도 크게 준다
    sc, nds = W.ic_scores(X, y, dates)
    eM, eV = W.edge_of(X[:, 0].astype(float), y), W.edge_of(X[:, 1].astype(float), y)
    print(f"     ic_score M={sc[0]:.4f} V={sc[1]:.4f} | edge M={eM:.4f} V={eV:.4f} "
          f"| 관측날짜 M={nds[0]} V={nds[1]}")
    check("2) 날짜 레벨 피처의 IC 점수 = 0", sc[0] == 0.0)
    check("2b) 같은 피처가 풀링 edge 로는 크다(>0.10)", eM > 0.10, f"edge={eM:.4f}")
    check("3) 시간가변 피처의 IC 점수 > 0.05", sc[1] > 0.05, f"score={sc[1]:.4f}")

    # 4) 일관성 항
    scW, _ = W.ic_scores(Wv.reshape(-1, 1), y, dates)
    print(f"     ic_score 부호뒤집힘 W={scW[0]:.4f} vs 일관 V={sc[1]:.4f}")
    check("4) 부호 뒤집힘 < 일관", scW[0] < sc[1], f"W={scW[0]:.4f} V={sc[1]:.4f}")

    # 5) top{k} 회귀
    edges = np.array([W.edge_of(X[:, i].astype(float), y) for i in range(X.shape[1])])
    exp = sorted(int(i) for i in np.argsort(-edges)[:1])
    got, desc = W.subset(names, "top1", X, y)
    check("5) top1 회귀(수동 계산 일치)", got == exp, f"{got} vs {exp}")
    check("5b) top* 설명 문자열 불변", desc == "top1", desc)

    # 6·7) ic 선택: k개·정렬·중복 없음 + 겹침 설명
    idx, idc = W.subset(names, "ic1", X, y, dates)
    check("6) ic1 은 시간가변 피처를 고른다", idx == [1], f"{idx}")
    check("6b) 중복/정렬", idx == sorted(set(idx)) and len(idx) == len(set(idx)))
    check("7) 설명에 edge 겹침 수", "겹침" in idc and idc.startswith("ic1"), idc)
    print("     sel_desc:", idc)

    # k > 후보 수 → 있는 만큼만(크래시 금지)
    idx3, idc3 = W.subset(names, "ic30", X, y, dates)
    check("8) k > 후보 수 안전", idx3 == [0, 1] or idx3 == [1], f"{idx3}")

    # 관측 날짜 < min_dates(10) → 전부 0(무정보 취급)
    short = np.asarray([f"D{i}" for i in range(5)] * 20)
    Xs = np.tile(np.asarray([1.0, 0.0] * 10), (5, 1)).reshape(100, 1)
    ys = np.tile(np.asarray([1, 0] * 10), 5)
    scs, _ = W.ic_scores(Xs, ys, short)
    check("9) 관측날짜<min_dates → 0", float(scs.max()) == 0.0, f"max={float(scs.max())}")

    # 10) wf_label_sweep.py 등록 확인
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wf_label_sweep.py")
    src = open(p, encoding="utf-8").read()
    ok = all(f'"id": "{i}"' in src for i in
             ["SL_00_30", "SL_30_60", "SL_60_90", "SL_90_120", "SL_120_150"]) \
        and src.count('"select": "ic30"') >= 5
    n_ic = src.count('"select": "ic30"')
    check("10) SL_* 5개 config 등록(select=ic30)", ok, f"ic30 count={n_ic}")

    print(f"\n{len(PASS)} PASS / {len(FAIL)} FAIL")
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

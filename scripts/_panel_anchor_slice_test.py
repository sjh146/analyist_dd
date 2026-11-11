#!/usr/bin/env python3
"""자체점검 — 완성 패널 재사용 경로의 as-of 앵커 슬라이스 (2026-11-12 CG199 실사고).

왜: `--end-date 2026-11-05` 로 돌린 CG199 가 실제로는 패널 끝(2026-11-09) 데이터를 그대로 써서
CG195 와 **비트동일**한 값을 냈다(ctl 0.5106 · arm 0.5162). 구동기는 그것을 '노이즈(측정됨)'로
기록했다 — 가짜 재현. 원인: build_panel 의 **재사용 경로**가 end_date 를 경고만 하고 무시했다.

기대:
  ① end_date < 패널 끝 → 해당 날짜까지만 잘린 df (행 감소·max(date)=end_date)
  ② end_date > 패널 끝 → RuntimeError(명시 실패) — 옛 데이터로 '연장' 흉내 금지
  ③ end_date == 패널 끝 / 미지정 → 종전과 동일(무회귀)
실행: docker exec stock_xgboost_ml sh -c 'cd /app && PYTHONPATH=/app:/app/scripts python -u scripts/_panel_anchor_slice_test.py'
"""
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import wf_wave as W  # noqa: E402

PASS = []


def check(name, cond, detail=""):
    PASS.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + (f" — {detail}" if detail else ""))


def make_panel(path, dates, codes=("A", "B")):
    rows = len(dates) * len(codes)
    X = np.arange(rows, dtype=float).reshape(rows, 1)
    np.savez_compressed(path, X=X, feature_names=np.array(["f1"]),
                        dates=np.array([d for d in dates for _ in codes]),
                        codes=np.array([c for _ in dates for c in codes]),
                        price=np.linspace(100, 200, rows))


def main():
    dates = ["2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06", "2026-11-09"]
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "panel_t.npz")
        make_panel(p, dates)
        logs = []

        # ① 앵커 이동(슬라이스)
        df, names = W.build_panel(p, len(("A", "B")), 10, log=logs.append,
                                  end_date="2026-11-05")
        md = sorted(df["date"].astype(str).unique())
        check("① 슬라이스: max(date)==end_date", md[-1] == "2026-11-05", f"max={md[-1]}")
        check("① 슬라이스: 미래 행 제거", len(df) == 3 * 2, f"rows={len(df)}")
        check("① 슬라이스: 로그에 사실 기록",
              any("as-of 슬라이스" in s for s in logs), " / ".join(logs[-2:]))
        check("① 슬라이스: end_date 경고 없음(진짜 불일치와 분리)",
              not any("panel meta 불일치" in s for s in logs), " / ".join(logs[-2:]))

        # ② 패널보다 뒤 요청 → 명시 실패
        try:
            W.build_panel(p, 2, 10, log=logs.append, end_date="2026-11-20")
            check("② 뒤 요청 → RuntimeError", False, "예외 없음")
        except RuntimeError as e:
            check("② 뒤 요청 → RuntimeError", "보다 뒤" in str(e), str(e)[:120])

        # ③ 무회귀 — 같은 끝 / 미지정
        df2, _ = W.build_panel(p, 2, 10, log=logs.append, end_date="2026-11-09")
        check("③ end_date==패널끝 무회귀", len(df2) == len(dates) * 2, f"rows={len(df2)}")
        df3, _ = W.build_panel(p, 2, 10, log=logs.append, end_date=None)
        check("③ end_date 미지정 무회귀", len(df3) == len(dates) * 2, f"rows={len(df3)}")

    ok = all(PASS)
    print(f"\n=== {sum(PASS)}/{len(PASS)} PASS ===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

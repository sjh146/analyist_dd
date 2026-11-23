#!/usr/bin/env python3
"""CG450 준비물 ③ 회귀 — close-only(종가+누적거래량) 인트라데이 피처 경로.

왜 필요한가(2026-11-24): Naver 전구간 분봉 `minute_close_bars` 는 종가·누적거래량만 준다.
`minute_bars` 경로(open/high/low/거래대금 전제)를 그대로 쓰면 range/vwap_dev/tv_share 가
조용히 0 이 되어 '결측'과 '무정보'가 구분되지 않는다. 그래서 별도 정의(idc_*)를 두고,
이 테스트가 ① 손계산 값과 일치 ② 시간표기 정규화('0900'→'090000') ③ 누적→증분 변환
④ 결측 규약(0.0) ⑤ 기존 id_* 경로 불변 을 함께 고정한다.

실행: python3 scripts/_intraday_close_features_test.py   (전부 PASS 면 exit 0)
"""
from __future__ import annotations

import os
import sys

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "services", "xgboost-ml"))

from app.feature_engine.intraday_features import (  # noqa: E402
    close_only_feature_names,
    compute_close_only_features,
    compute_intraday_features,
    diff_cum_volume,
    feature_names,
    normalize_bar_time,
)

FAILS = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("PASS " if cond else "FAIL ") + name + ((" — " + detail) if (detail and not cond) else ""))
    if not cond:
        FAILS.append(name)


def close(exp: float, got: float, tol: float = 1e-9) -> bool:
    return abs(exp - got) <= tol


def main() -> int:
    check("1a '0900' → '090000'", normalize_bar_time("0900") == "090000")
    check("1b '09:00' → '090000'", normalize_bar_time("09:00") == "090000")
    check("1c '150100' 불변", normalize_bar_time("150100") == "150100")

    bars_cum = [("0900", 100.0, 10.0), ("0901", 101.0, 30.0), ("0902", 100.0, 40.0),
                ("0903", 102.0, 70.0)]
    d = diff_cum_volume(bars_cum)
    check("2a 증분 = [10,20,10,30]", [b[2] for b in d] == [10.0, 20.0, 10.0, 30.0], str(d))
    check("2b 종가 보존", [b[1] for b in d] == [100.0, 101.0, 100.0, 102.0])
    check("2c 리셋(음수) → 0 절단",
          diff_cum_volume([("0900", 1.0, 50.0), ("0901", 1.0, 20.0)])[1][2] == 0.0)

    closes = [100.0, 101.0, 100.0, 102.0]
    dv = 70.0
    f = compute_close_only_features(bars_cum, dv)
    mean_c = sum(closes) / len(closes)
    slope = float(np.polyfit(np.arange(len(closes)), closes, 1)[0]) / mean_c
    rets = [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))]
    check("3a ret = 0.02", close(0.02, f["idc_open30_ret"]), str(f["idc_open30_ret"]))
    check("3b range = (102-100)/102", close(2.0 / 102.0, f["idc_open30_range"]), str(f["idc_open30_range"]))
    check("3c slope = OLS/평균종가", close(slope, f["idc_open30_slope"]), "%r vs %r" % (f["idc_open30_slope"], slope))
    check("3d up_ratio = 2/3", close(2.0 / 3.0, f["idc_open30_up_ratio"]), str(f["idc_open30_up_ratio"]))
    check("3e realvol = pstdev(봉수익률)",
          close(float(np.std(rets)), f["idc_open30_realvol"]), str(f["idc_open30_realvol"]))
    check("3f vol_share = 70/70", close(1.0, f["idc_open30_vol_share"]), str(f["idc_open30_vol_share"]))
    check("3g n_bars = 4", close(4.0, f["idc_n_bars"]), str(f["idc_n_bars"]))

    bars2 = [("0900", 100.0, 10.0), ("1500", 190.0, 150.0), ("1501", 200.0, 160.0),
             ("1530", 220.0, 260.0), ("1531", 500.0, 270.0)]
    f2 = compute_close_only_features(bars2, 270.0)
    check("4a close30 ret = 220/200-1", close(0.10, f2["idc_close30_ret"]), str(f2["idc_close30_ret"]))
    check("4b close30 는 15:00·15:31 봉을 제외(포함됐다면 ret=500/190-1)",
          close(0.10, f2["idc_close30_ret"]) and not close(500.0 / 190.0 - 1.0, f2["idc_close30_ret"]),
          str(f2["idc_close30_ret"]))
    check("4c close30 vol_share = (260-150)/270", close(110.0 / 270.0, f2["idc_close30_vol_share"]),
          str(f2["idc_close30_vol_share"]))

    bars3 = [("0900", 100.0, 10.0), ("1200", 110.0, 100.0), ("1201", 110.0, 110.0),
             ("1530", 132.0, 300.0)]
    f3 = compute_close_only_features(bars3, 300.0)
    check("5a am_pm_ret_diff = 0.20-0.10", close(0.10, f3["idc_am_pm_ret_diff"]),
          str(f3["idc_am_pm_ret_diff"]))

    f4 = compute_close_only_features([], 0.0)
    check("6a 봉 없음 → 전부 0.0", all(v == 0.0 for v in f4.values()))
    f5 = compute_close_only_features([("0900", 100.0, 5.0)], 5.0)
    check("6b 창 내 1봉 → 창통계 0.0(계산 불가) 이고 n_bars=1",
          f5["idc_open30_ret"] == 0.0 and f5["idc_n_bars"] == 1.0, str(f5))
    f6 = compute_close_only_features([("0900", 0.0, 1.0), ("0901", 0.0, 2.0)], 2.0)
    check("6c 종가 0 → 창통계 0.0", f6["idc_open30_ret"] == 0.0 and f6["idc_open30_realvol"] == 0.0)
    f7 = compute_close_only_features(bars_cum, None)
    check("6d day_volume 없으면 vol_share=0.0(다른 통계는 그대로)",
          f7["idc_open30_vol_share"] == 0.0 and close(0.02, f7["idc_open30_ret"]))

    names = close_only_feature_names()
    check("7a close-only 이름 14개", len(names) == 14, str(len(names)))
    check("7b 전부 idc_ 접두사", all(n.startswith("idc_") for n in names))
    check("7c 기존 id_* 와 이름 충돌 없음", not (set(names) & set(feature_names())))
    check("7d 기존 id_* 이름 18개 유지(창2×(6통계+2점유)→16 + 파생2)",
          len(feature_names()) == 18, str(len(feature_names())))
    check("7e 같은 봉으로 두 경로 모두 계산 가능(별개 정의)",
          compute_intraday_features(
              [(b[0], b[1], b[1], b[1], b[1], 1.0, 1.0) for b in bars_cum], (4.0, 4.0))["id_n_bars"] == 4.0)

    print("-" * 60)
    print("RESULT %s (%d checks)" % ("ALL PASS" if not FAILS else "FAIL", len(FAILS)))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())

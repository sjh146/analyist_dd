#!/usr/bin/env python3
"""공매도(krx_short_selling) 피처 빌더 — CG323 데이터 축 (2026-11-18)

왜: 미배선 원천 스윕이 커버리지를 낡은 패널(panel_prod200, ~2026-09-23) 유니버스로 재서
krx_short_selling 을 BLOCKED(25/200)로 판정했지만, 신선 패널(panel_200liq_ffill_2605)로 재면
176/200 = 0.880 이다(data/reports/source_sweep_freshpanel_20261118.json).
현 217피처에는 사실상 공매도 정보가 없다(supply_market_features.short_selling_ratio 비영 2,373행).

출력: app/models/wf/ss_features.npz
  codes (U12), dates (U10), F (n_codes*n_dates, 5), names = [ss_ratio, ss_value_ratio,
  ss_ratio_ma5, ss_ratio_ma20, ss_ratio_z20]  — 마지막 사용일 기준 **1거래일 지연** 적용은
  패널 주입 단계(patch_panel_ss.py)에서 한다(빌더는 원천값만 정직하게 만든다).

⚠ as-of 주의: krx_short_selling 은 created_at 이 당일 10:3x 인 행이 많다(장중 부분 집계).
  따라서 소비 측은 반드시 t-1 이하 값만 쓰도록 지연을 건다.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import numpy as np

DB = ["docker", "exec", "stock_postgres", "psql", "-U", "stock_user", "-d", "stock_trading",
      "-t", "-A", "-F", "|", "-c"]
OUT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                                   "data", "reports", "ss_features.npz"))
NAMES = ["ss_ratio", "ss_ratio_ma5", "ss_ratio_ma20", "ss_ratio_z20"]
# ⚠ ss_value_ratio(공매도 대금/거래량)는 차원이 '주당 가격'이라 사실상 가격수준 프록시였다
#   (실측 std 4.0e6 = 종목 간 가격 분산). 공매도 신호가 아니라 가격 프록시이므로 뺀다(2026-11-18).


def psql(sql: str, timeout: int = 300) -> list[list[str]]:
    out = subprocess.run(DB + [sql], capture_output=True, text=True, timeout=timeout)
    if out.returncode != 0:
        raise RuntimeError(f"psql rc={out.returncode}: {out.stderr.strip()[:300]}")
    return [l.split("|") for l in out.stdout.splitlines() if l.strip()]


def main() -> int:
    rows = psql(
        "SELECT stock_code, trade_date::text, "
        "coalesce(short_volume,0), coalesce(short_value,0), coalesce(short_ratio,0), "
        "coalesce(total_volume,0) FROM krx_short_selling "
        "WHERE trade_date >= DATE '2026-01-01' ORDER BY stock_code, trade_date;")
    if not rows:
        print("원천 0행 — 중단", file=sys.stderr)
        return 2

    codes = sorted({r[0] for r in rows})
    dates = sorted({r[1] for r in rows})
    ci = {c: i for i, c in enumerate(codes)}
    di = {d: i for i, d in enumerate(dates)}
    nC, nD = len(codes), len(dates)
    raw = np.full((nC, nD), np.nan)
    for code, d, sv, sval, srat, tvol in rows:
        i, j = ci[code], di[d]
        r = float(srat)
        # short_ratio 가 0 이고 거래량이 있는 날은 '공매도 0' 이 아니라 '미집계'일 수 있다 →
        # total_volume>0 인데 short_volume==0 이면 0 으로 신뢰(실제 0 공매도), total_volume==0 이면 결측.
        if r == 0.0:
            r = 0.0 if float(tvol) > 0 else np.nan
        raw[i, j] = r

    def roll(a: np.ndarray, w: int, fn) -> np.ndarray:
        out = np.full_like(a, np.nan)
        for j in range(a.shape[1]):
            lo = max(0, j - w + 1)
            block = a[:, lo:j + 1]
            cnt = np.sum(~np.isnan(block), axis=1)
            with np.errstate(invalid="ignore"):
                s = fn(block, axis=1)
            out[:, j] = np.where(cnt >= max(2, w // 2), s, np.nan)
        return out

    ma5 = roll(raw, 5, np.nanmean)
    ma20 = roll(raw, 20, np.nanmean)
    sd20 = roll(raw, 20, np.nanstd)
    with np.errstate(invalid="ignore", divide="ignore"):
        z20 = (raw - ma20) / np.where(sd20 > 1e-9, sd20, np.nan)

    F = np.stack([raw, ma5, ma20, z20], axis=2)   # (nC, nD, 4)
    snap = {
        "ts": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "krx_short_selling",
        "n_codes": nC, "n_dates": nD, "dates": [dates[0], dates[-1]],
        "rows": len(rows),
        "nonzero_frac": {NAMES[k]: round(float(np.mean(~np.isnan(F[:, :, k]) + 0.0)), 4) for k in range(F.shape[2])},
        "note": "값은 원천 그대로. 소비 시 t-1 지연은 patch_panel_ss.py 가 건다.",
    }
    np.savez_compressed(OUT, codes=np.array(codes), dates=np.array(dates), F=F,
                        names=np.array(NAMES), meta=np.array(json.dumps(snap, ensure_ascii=False)))
    print(json.dumps(snap, ensure_ascii=False, indent=1))
    print("저장:", os.path.abspath(OUT))
    # 자기검증: 셀 수·결측률·상수 아님
    assert nC > 100 and nD > 60, (nC, nD)
    assert snap["nonzero_frac"]["ss_ratio"] > 0.5, snap["nonzero_frac"]
    assert float(np.nanstd(F[:, :, 0])) > 0, "ss_ratio 상수"
    print("자체검증 PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

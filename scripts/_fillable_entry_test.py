#!/usr/bin/env python3
"""_fillable_entry_test.py — fillable_topk_expectancy 의 --entry 배선 회귀 테스트 (CG420).

검증 목적:
  ① --entry close(기본) 는 기존 의미 그대로 `exit/신호일종가 − 1` 이어야 한다(비트 동일 회귀).
  ② --entry next_open 은 `exit/익일시가 − 1` 로 계산돼야 한다.
  ③ 진입 인덱스 ≤ 청산 인덱스(같은 가격 매수·매도)인 행은 버려야 한다.
  ④ 익일 시가가 없으면(None) 버려야 한다.

pytest 없는 스택이므로 PASS/FAIL 직접 출력, 실패 시 rc=1. 호스트에서 돈다:
  python3 scripts/_fillable_entry_test.py
"""
from __future__ import annotations

import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fillable_topk_expectancy as fe  # noqa: E402

FAIL = 0


def check(name, cond, extra=""):
    global FAIL
    ok = bool(cond)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {extra}" if extra else ""))
    if not ok:
        FAIL += 1


D0 = dt.date(2026, 1, 5)
DATES = [D0 + dt.timedelta(days=i) for i in range(8)]
# 종가 100 → 130 (i=5), 시가 101/102/... (i=1 = 102)
SERIES = {"AAA": {"d": DATES,
                  "o": [101.0, 102.0, 103.0, 104.0, 105.0, 126.0, 127.0, 128.0],
                  "c": [100.0, 105.0, 110.0, 115.0, 120.0, 130.0, 131.0, 132.0],
                  "tv": [2e9] * 8}}


def rows(*, entry_date="2026-01-05", open_none=False):
    s = {k: {kk: list(vv) for kk, vv in v.items()} for k, v in SERIES.items()}
    if open_none:
        s["AAA"]["o"][1] = None
    r = {"code": "AAA", "date": entry_date, "score": 0.9, "fold": 0}
    return [r], s


def close_pct(px_open, px_close):
    return (px_close / px_open - 1.0) * 100.0


# ① 기본(entry=close, exit=close_h): 기존 의미 유지
rows_a, s = rows()
kept, drop = fe.enrich(rows_a, s, 5, "close_h")
check("기본 entry=close 는 exit/종가 기준", len(kept) == 1 and abs(kept[0]["gross_pct"] - close_pct(100.0, 130.0)) < 1e-9,
      f"gross={kept[0]['gross_pct'] if kept else None} drop={drop}")
check("기본 경로에서 entry_px == 신호일 종가", kept and abs(kept[0]["entry_px"] - 100.0) < 1e-9)

# ② entry=next_open: 익일 시가 진입
rows_b, s = rows()
kept2, drop2 = fe.enrich(rows_b, s, 5, "close_h", "next_open")
check("entry=next_open 은 exit/익일시가 기준", len(kept2) == 1 and abs(kept2[0]["gross_pct"] - close_pct(102.0, 130.0)) < 1e-9,
      f"gross={kept2[0]['gross_pct'] if kept2 else None} drop={drop2}")
check("entry=next_open 의 entry_px == 익일 시가", kept2 and abs(kept2[0]["entry_px"] - 102.0) < 1e-9)
check("두 경로 gross 가 실제로 다르다", kept and kept2 and abs(kept[0]["gross_pct"] - kept2[0]["gross_pct"]) > 1e-6)

# ③ next_open 진입 + next_open 청산 = 같은 가격 → 행 폐기
rows_c, s = rows()
kept3, drop3 = fe.enrich(rows_c, s, 1, "next_open", "next_open")
check("진입·청산이 같은 인덱스면 폐기", len(kept3) == 0 and drop3 == 1, f"kept={len(kept3)} drop={drop3}")

# ④ 익일 시가 결측 → 폐기
rows_d, s = rows(open_none=True)
kept4, drop4 = fe.enrich(rows_d, s, 5, "close_h", "next_open")
check("익일 시가 결측이면 폐기", len(kept4) == 0 and drop4 == 1, f"kept={len(kept4)} drop={drop4}")
# 같은 행이 entry=close 에서는 살아 있어야 한다(결측은 진입가에만 영향)
rows_e, s = rows(open_none=True)
kept5, drop5 = fe.enrich(rows_e, s, 5, "close_h")
check("같은 결측 행이 entry=close 에서는 유지", len(kept5) == 1, f"kept={len(kept5)} drop={drop5}")

# ⑤ 기존 exit 경로 회귀: entry=close + exit=next_open → o[i+1]
rows_f, s = rows()
kept6, _ = fe.enrich(rows_f, s, 1, "next_open")
check("entry=close + exit=next_open 기존 의미 유지", kept6 and abs(kept6[0]["gross_pct"] - close_pct(100.0, 102.0)) < 1e-9,
      f"gross={kept6[0]['gross_pct'] if kept6 else None}")

print(f"\n{'ALL PASS' if FAIL == 0 else f'{FAIL} FAIL'}")
sys.exit(1 if FAIL else 0)

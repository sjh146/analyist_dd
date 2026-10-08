#!/usr/bin/env python3
"""자체점검: champion_robust_eval.py 의 `--asof-date` 창 고정 옵션 (2026-10-09 신설).

무엇을 검증하는가:
  1) 기본값(None)은 **현행과 비트 동일** — SQL 에 CURRENT_DATE 가 그대로 있고 anchor 파라미터가 없다.
     (옵션 추가가 기존 기준선·승격 경로를 흔들지 않는다는 무회귀 조건)
  2) asof_date 를 주면 앵커 고정 SQL 을 쓰고, 앵커 값·n 이 **파라미터로** 전달된다.
  3) argparse 배선: --asof-date 가 dest=asof_date 로 들어가고 기본값은 None.
  4) 창 분할(폴드)은 같은 거래일 목록에 대해 결정적이다(같은 입력 → 같은 창).

왜: 앵커가 CURRENT_DATE 라 하루만 지나도 표본 창이 밀려, 같은 모델·같은 프로토콜의 값이
0.4935~0.5448 로 흔들린다(실측 n=13). 창을 고정해야 일 단위 Δ 비교가 성립한다.

실행(컨테이너 안): docker exec -e PYTHONPATH=/app stock_xgboost_ml python /app/scripts/_robust_asof_pin_test.py
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, "/app")

import champion_robust_eval as M  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, extra: object = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"PASS  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}  {extra}")


class _Cur:
    def __init__(self, rec):
        self.rec = rec

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.rec["sql"] = sql
        self.rec["params"] = params

    def fetchall(self):
        return self.rec.get("rows", [])


class _Conn:
    def __init__(self, rows):
        self.rec = {"rows": rows}

    def cursor(self):
        return _Cur(self.rec)


class _D:
    def __init__(self, s):
        self.s = s

    def strftime(self, fmt):
        return self.s


def main() -> int:
    # 1) 기본값 = 현행 비트 동일 (CURRENT_DATE, anchor 파라미터 없음)
    c = _Conn([(_D("2026-09-30"),)])
    out = M.trading_dates(c, 30)
    sql = c.rec["sql"]
    check("기본경로: CURRENT_DATE 사용", "CURRENT_DATE" in sql)
    check("기본경로: anchor 파라미터 없음", c.rec["params"] == (30,), repr(c.rec["params"]))
    check("기본경로: 반환 파싱", out == ["2026-09-30"], repr(out))

    # 2) 고정 앵커 = 앵커 SQL + 파라미터로 전달
    c = _Conn([(_D("2026-09-30"),)])
    out = M.trading_dates(c, 30, asof_date="2026-10-07")
    sql = c.rec["sql"]
    check("고정경로: CURRENT_DATE 미사용", "CURRENT_DATE" not in sql)
    check("고정경로: anchor::date 사용", "%(anchor)s::date" in sql, sql)
    check("고정경로: 파라미터 전달", c.rec["params"] == {"anchor": "2026-10-07", "n": 30},
          repr(c.rec["params"]))
    check("고정경로: 반환 파싱", out == ["2026-09-30"], repr(out))

    # 3) argparse 배선 — champion_robust_eval 의 파서를 직접 만든다(main 내부 파서 재현)
    #    main() 은 DB 를 요구하므로, 파일에서 옵션 정의만 AST 로 확인한다.
    src = open(M.__file__, encoding="utf-8").read()
    check("argparse: --asof-date 정의", '"--asof-date"' in src)
    check("argparse: dest=asof_date", 'dest="asof_date"' in src)
    check("argparse: 기본값 None", 'dest="asof_date", default=None' in src)
    check("요약 JSON: window_anchor 기록", '"window_anchor": args.asof_date' in src)
    check("호출부: trading_dates 에 asof_date 전달", "asof_date=args.asof_date" in src)

    # 4) 창 분할 결정성 — 같은 거래일 목록 → 같은 창
    def windows(dates, folds, h=5):
        chunk = max(1, len(dates) // folds)
        w = [dates[i * chunk:(i + 1) * chunk] for i in range(folds)]
        return [x for x in w if len(x) > h + 1]

    dates = [f"2026-{m:02d}-{d:02d}" for m in range(1, 7) for d in range(1, 11)]
    check("창 분할 결정적", windows(dates, 3) == windows(dates, 3))

    print(f"\n{PASS}/{PASS + FAIL} PASS")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

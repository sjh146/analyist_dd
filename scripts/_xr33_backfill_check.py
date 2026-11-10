#!/usr/bin/env python3
"""XR33 셋업 검증 — 4주 호스트 오프라인(2026-10-11~11-09) 공백이 실제로 메워졌는가.

리서처 R33 은 "수급(investor flows)은 일일 크론 `--days 5` 라 10-12~10-31 은 어떤 경로도
담당하지 않는다 → 60일 창 1회 백필 필요"라고 넘겼다. 엔지니어는 실행 전에 **DB 만 읽어**
공백 구간의 커버리지를 실측한다(백필 재실행 여부 판단). 읽기 전용.

실행: python3 scripts/_xr33_backfill_check.py
"""
import json
import subprocess
import sys

WIN = ("2026-10-12", "2026-10-31")


def psql(sql):
    out = subprocess.run(
        ["docker", "exec", "stock_postgres", "sh", "-c",
         'psql -U $POSTGRES_USER -d $POSTGRES_DB -Atc "%s"' % sql.replace('"', '\\"')],
        capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit("psql 실패: " + out.stderr.strip()[:300])
    return out.stdout.strip()


def main():
    a, b = WIN
    res = {}
    res["market_data"] = {
        "max": psql("select max(trade_date) from market_data"),
        "days_in_window": int(psql(
            "select count(distinct trade_date) from market_data "
            "where trade_date between '%s' and '%s'" % (a, b))),
    }
    res["foreign_institutional"] = {
        "max": psql("select max(trade_date) from foreign_institutional"),
        "days_in_window": int(psql(
            "select count(distinct trade_date) from foreign_institutional "
            "where trade_date between '%s' and '%s'" % (a, b))),
        "rows_in_window": int(psql(
            "select count(*) from foreign_institutional "
            "where trade_date between '%s' and '%s'" % (a, b))),
        "nonzero_qty_ratio": float(psql(
            "select round(avg(case when foreign_net_buy_qty is not null "
            "and foreign_net_buy_qty<>0 then 1 else 0 end)::numeric,4) "
            "from foreign_institutional where trade_date between '%s' and '%s'" % (a, b))),
    }
    res["supply_market_features"] = {
        "max": psql("select max(trade_date) from supply_market_features"),
        "days_in_window": int(psql(
            "select count(distinct trade_date) from supply_market_features "
            "where trade_date between '%s' and '%s'" % (a, b))),
    }
    # 2026-10-12~10-31 평일 = 15일(공휴일 없음)
    exp = 15
    ok = all(v["days_in_window"] == exp for v in res.values())
    res["expected_trading_days"] = exp
    res["verdict"] = "complete" if ok else "gap_remaining"
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

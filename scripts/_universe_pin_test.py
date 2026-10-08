"""유니버스 동결(UNIVERSE_ASOF_DATE) 자체점검 — 현행 무회귀 + 동결 시 재현성.

실행: docker exec -e PYTHONPATH=/app stock_xgboost_ml python /app/scripts/_universe_pin_test.py
"""
import datetime as dt
import os
import subprocess
import sys

import psycopg2

from app.training.universe import _default_date_from

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {name} {detail}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {detail}")


def conn():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD"),
    )


def universe(asof=None):
    env = dict(os.environ)
    env["PYTHONPATH"] = "/app"
    if asof:
        env["UNIVERSE_ASOF_DATE"] = asof
    else:
        env.pop("UNIVERSE_ASOF_DATE", None)
    code = ("import os;from app.training.universe import select_training_universe as s;"
            "import psycopg2;c=psycopg2.connect(host=os.environ.get('POSTGRES_HOST','postgres'),"
            "port=os.environ.get('POSTGRES_PORT','5432'),dbname=os.environ['POSTGRES_DB'],"
            "user=os.environ['POSTGRES_USER'],password=os.environ['POSTGRES_PASSWORD']);"
            "print(','.join(s(c,limit=200,min_days=30,seed=0)))")
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    if out.returncode != 0:
        print(out.stderr[-500:])
        return None
    return out.stdout.strip().split(",")


def main():
    print("== 1. 기본값 무회귀 ==")
    os.environ.pop("UNIVERSE_ASOF_DATE", None)
    expect = (dt.datetime.now() - dt.timedelta(days=60)).strftime("%Y-%m-%d")
    check("default == now-60", _default_date_from(60) == expect, f"({_default_date_from(60)})")

    print("== 2. 동결 시 창 고정 ==")
    os.environ["UNIVERSE_ASOF_DATE"] = "2026-10-02"
    check("asof-60", _default_date_from(60) == "2026-08-03", f"({_default_date_from(60)})")
    os.environ.pop("UNIVERSE_ASOF_DATE", None)

    print("== 3. 프로세스 간 재현성(동결) ==")
    a = universe("2026-10-02")
    b = universe("2026-10-02")
    check("동결 2회 동일", a is not None and a == b, f"n={None if a is None else len(a)}")
    check("동결 200종목", a is not None and len(a) == 200)

    print("== 4. 미동결 재현성(같은 날) ==")
    u1, u2 = universe(), universe()
    check("미동결 2회 동일(같은 날)", u1 == u2, f"n={None if u1 is None else len(u1)}")

    print("== 5. 날짜 민감도(동결 창을 7일 밀면) ==")
    c = universe("2026-10-09")
    check("동결 프로세스 모두 200", all(x is not None and len(x) == 200 for x in (a, c)))
    inter = len(set(a) & set(c))
    print(f"     asof 2026-10-02 vs 2026-10-09 교집합 = {inter}/200 "
          f"({100*inter/200:.1f}% 유지) — 창 7일 이동이 표본 대부분을 교체함을 기록")
    check("날짜 이동이 표본을 실제로 교체(<90%)", inter < 180, f"inter={inter}")

    print(f"\n결과: PASS={PASS} FAIL={FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

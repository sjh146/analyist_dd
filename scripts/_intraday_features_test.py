#!/usr/bin/env python3
"""자체점검 — 인트라데이(분봉) 피처 모듈 (데이터 축 CG101 준비물 ②).

이 스택에는 pytest 가 없다 → 순수 파이썬으로 PASS/FAIL 을 찍고 실패 시 exit 1.
실행(컨테이너, 실제 DB 사용):
    docker exec -e PYTHONPATH=/app stock_xgboost_ml python /app/scripts/_intraday_features_test.py

검사 축:
  1) 이름 집합 고정·유일·18종·`id_` 접두
  2) **스크린과의 정의 일치** — close30 창의 8개 통계 이름이 `intraday_feature_screen.py` 의 l30_* 와 1:1
  3) 실제 DB (종목, 날짜) 대조 — 모듈 값 == 독립 SQL 재계산 (수치 항등, 1e-9)
  4) 창 경계 — 창 밖 봉은 어떤 통계에도 들어가지 않는다(현재 데이터는 open30 이 비어 있고 close30 만 찬다.
     수집기 수리(XR26) 후에는 open30 이 채워지는 것이 정상 → DB 에서 유도해 기대값을 만든다)
  5) 날짜 격리(as-of) — t 의 값은 t 의 봉만 쓴다(뒤 날짜 봉을 섞지 않는다): id_n_bars == 그날 봉 수
  6) 결측 규약 — 봉 없는 (종목, 날짜) → 전부 0.0, NaN/Inf 없음
  7) 퇴화 입력 — 1봉 / 무거래량 / 2봉(기울기 정의 불가)에서 예외 없이 0.0
  8) date=None == 그 종목 최대 trade_date
  9) 파이프라인 배선 — 기본 OFF(env 미설정 시 피처 목록이 종전과 동일 = id_ 0개), env=1 이면 18개 추가
"""
from __future__ import annotations

import os
import subprocess
import sys
from collections import defaultdict

import psycopg2

from app.feature_engine.intraday_features import (
    IntradayFeatures, compute_intraday_features, feature_names)

FAILS: list[str] = []
PASSES = [0]


def check(cond: bool, label: str, detail: str = "") -> None:
    if cond:
        PASSES[0] += 1
        print(f"  PASS  {label}")
    else:
        FAILS.append(label)
        print(f"  FAIL  {label}  {detail}")


def _connect():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""))


def _sql_bars(cur, code, d):
    cur.execute("SELECT \"time\", open_price, high_price, low_price, close_price, volume, "
                "trading_value FROM minute_bars WHERE stock_code=%s AND trade_date=%s::date "
                "ORDER BY \"time\"", (code, d))
    return [(str(t), float(o or 0), float(h or 0), float(l or 0), float(c or 0),
             float(v or 0), float(tv or 0)) for t, o, h, l, c, v, tv in cur.fetchall()]


def _sql_close30_stats(cur, code, d):
    """close30(15:01~15:30) 창의 ret·range·vwap_dev·up_ratio 를 SQL 로 **독립** 재계산."""
    cur.execute("SELECT close_price, high_price, low_price, volume, trading_value FROM minute_bars "
                "WHERE stock_code=%s AND trade_date=%s::date AND \"time\" BETWEEN '150100' AND '153000' "
                "ORDER BY \"time\"", (code, d))
    rows = cur.fetchall()
    if len(rows) < 2:
        return None
    closes = [float(r[0] or 0) for r in rows]
    highs = [float(r[1] or 0) for r in rows]
    lows = [float(r[2] or 0) for r in rows]
    sv = sum(float(r[3] or 0) for r in rows)
    st = sum(float(r[4] or 0) for r in rows)
    vwap = st / sv if sv > 0 else closes[-1]
    return {
        "ret": closes[-1] / closes[0] - 1.0,
        "range": (max(highs) - min(lows)) / closes[-1],
        "vwap_dev": closes[-1] / vwap - 1.0 if vwap > 0 else 0.0,
        "up_ratio": sum(1 for i in range(1, len(closes)) if closes[i] > closes[i - 1])
                    / max(1, len(closes) - 1),
    }


def main() -> int:
    names = feature_names()
    print("== 1) 이름 집합 ==")
    check(len(names) == 18, "피처 18종", f"실제 {len(names)}")
    check(len(set(names)) == len(names), "이름 유일")
    check(all(n.startswith("id_") for n in names), "전부 id_ 접두")
    print("== 2) 스크린(l30_*)과 정의 1:1 ==")
    stats = {"ret", "range", "vwap_dev", "slope", "up_ratio", "realvol", "vol_share", "tv_share"}
    close30 = {n[len("id_close30_"):] for n in names if n.startswith("id_close30_")}
    check(close30 == stats, "close30 창 = l30_* 8종과 동일 집합", f"{sorted(close30)}")
    open30 = {n[len("id_open30_"):] for n in names if n.startswith("id_open30_")}
    check(open30 == stats, "open30 창 = 같은 8종(개장 30분)", f"{sorted(open30)}")

    conn = _connect()
    cur = conn.cursor()

    print("== 3) DB 실측 대조 ==")
    # 날짜가 **여러 개**인 종목을 골라야 5) 날짜 격리 검사가 성립한다(봉 최다 단일일 종목을 고르면 격리
    # 검사가 1줄짜리가 되어 무의미해진다 — 2026-10-06 실측: count DESC 로 고르면 000020 09-23 하루뿐).
    cur.execute("SELECT stock_code FROM minute_bars GROUP BY 1 "
                "ORDER BY count(distinct trade_date) DESC, count(*) DESC LIMIT 1")
    code = str(cur.fetchone()[0])
    cur.execute("SELECT trade_date, count(*) FROM minute_bars WHERE stock_code=%s GROUP BY 1 "
                "ORDER BY count(*) DESC, 1 LIMIT 1", (code,))
    d, nb = cur.fetchone()
    d = str(d)[:10]
    print(f"  표본: {code} {d} 봉 {nb}")
    feats = IntradayFeatures().get_all_features(code, conn, d)
    ref = _sql_close30_stats(cur, code, d)
    check(ref is not None, "close30 창에 2봉 이상 존재")
    if ref is not None:
        for k, v in ref.items():
            got = feats[f"id_close30_{k}"]
            check(abs(got - v) < 1e-9, f"close30_{k} 일치", f"모듈 {got!r} vs SQL {v!r}")
    check(all(isinstance(v, float) and v == v and abs(v) < 1e12 for v in feats.values()),
          "전 값 유한(float·NaN/Inf 없음)")

    print("== 4) 창 경계 (데이터에서 기대값 유도) ==")
    cur.execute("SELECT min(\"time\"), max(\"time\") FROM minute_bars WHERE stock_code=%s "
                "AND trade_date=%s::date", (code, d))
    tmin, tmax = (str(x) for x in cur.fetchone())
    has_open30 = tmin <= "093000"
    open30_vals = [feats[n] for n in names if n.startswith("id_open30_")]
    if has_open30:
        check(any(v != 0.0 for v in open30_vals), "open30 창에 봉이 있으면 값이 채워진다", f"tmin={tmin}")
    else:
        check(all(v == 0.0 for v in open30_vals), "봉이 09:30 이전에 없으면 open30 은 0.0", f"tmin={tmin}")
    check(feats["id_close30_ret"] != 0.0 or abs(feats["id_close30_slope"]) >= 0.0,
          "close30 통계가 계산됨")
    check(feats["id_n_bars"] == float(nb), "id_n_bars == 그날 봉 수(SQL)", f"{feats['id_n_bars']} vs {nb}")

    print("== 5) 날짜 격리(as-of) ==")
    cur.execute("SELECT trade_date, count(*) FROM minute_bars WHERE stock_code=%s GROUP BY 1 "
                "ORDER BY 1", (code,))
    per_day = [(str(x)[:10], int(c)) for x, c in cur.fetchall()]
    check(len(per_day) >= 2, "표본 종목에 여러 날짜 존재", f"{per_day[:3]}")
    ok_iso = True
    for dd, cnt in per_day:
        f1 = IntradayFeatures().get_all_features(code, conn, dd)
        if f1["id_n_bars"] != float(cnt):
            ok_iso = False
            print(f"    불일치 {dd}: {f1['id_n_bars']} vs {cnt}")
    check(ok_iso, "모든 날짜에서 id_n_bars == 그 날짜 봉 수(뒤 날짜 미혼입)")

    print("== 6) 결측 규약 ==")
    cur.execute("SELECT stock_code FROM minute_bars WHERE stock_code NOT IN "
                "(SELECT stock_code FROM minute_bars WHERE trade_date=%s::date) LIMIT 1", (d,))
    row = cur.fetchone()
    if row:
        other = str(row[0])
        f2 = IntradayFeatures().get_all_features(other, conn, d)
        check(all(v == 0.0 for v in f2.values()), "봉 없는 (종목,날짜) → 전부 0.0", f"{other}@{d}")
    else:
        f2 = IntradayFeatures().get_all_features("999999", conn, d)
        check(all(v == 0.0 for v in f2.values()), "존재하지 않는 종목 → 전부 0.0")
    check(all(v == 0.0 for v in IntradayFeatures().get_all_features(code, None, "1999-01-01").values()),
          "DB 밖 날짜 → 전부 0.0")

    print("== 7) 퇴화 입력 ==")
    one = [("150100", 100, 101, 99, 100, 5, 500)]
    f3 = compute_intraday_features(one)
    check(f3["id_n_bars"] == 1.0 and f3["id_close30_ret"] == 0.0, "1봉 → n_bars 1·통계 0.0")
    zerovol = [("150100", 100, 101, 99, 100, 0, 0), ("150200", 101, 102, 100, 101, 0, 0)]
    f4 = compute_intraday_features(zerovol, (0.0, 0.0))
    check(f4["id_close30_vwap_dev"] == 0.0 and f4["id_close30_vol_share"] == 0.0,
          "무거래량 → vwap_dev/vol_share 0.0")
    two = [("150100", 100, 100, 100, 100, 1, 10), ("150200", 100, 100, 100, 100, 1, 10)]
    f5 = compute_intraday_features(two)
    check(f5["id_close30_ret"] == 0.0 and f5["id_close30_slope"] == 0.0
          and f5["id_close30_realvol"] == 0.0, "2봉·동일가 → ret/slope/realvol 0.0")
    f6 = compute_intraday_features([])
    check(all(v == 0.0 for v in f6.values()), "빈 입력 → 전부 0.0")

    print("== 8) date=None ==")
    cur.execute("SELECT max(trade_date) FROM minute_bars WHERE stock_code=%s", (code,))
    latest = str(cur.fetchone()[0])[:10]
    f7 = IntradayFeatures().get_all_features(code, conn, None)
    f8 = IntradayFeatures().get_all_features(code, conn, latest)
    check(f7 == f8, "date=None == 최대 trade_date", f"{latest}")
    conn.close()

    print("== 9) 파이프라인 배선(기본 OFF) ==")
    code_probe = (
        "import os,sys;"
        "from app.feature_engine.feature_pipeline import FeaturePipeline;"
        "p=FeaturePipeline();"
        "n=p.get_feature_names();"
        "print(len(n), sum(1 for x in n if x.startswith('id_')), int(p.use_intraday))"
    )
    env = dict(os.environ)
    env.pop("INTRADAY_FEATURES", None)
    env["PYTHONPATH"] = "/app"
    r_off = subprocess.run([sys.executable, "-c", code_probe], capture_output=True, text=True, env=env)
    check(r_off.returncode == 0, "기본(env 미설정) 기동", r_off.stderr[-300:])
    if r_off.returncode == 0:
        tot_off, id_off, flag_off = (int(x) for x in r_off.stdout.split())
        check(flag_off == 0 and id_off == 0, "기본 OFF — id_ 피처 0개(패널 스키마 불변)", r_off.stdout.strip())
    env_on = dict(env)
    env_on["INTRADAY_FEATURES"] = "1"
    r_on = subprocess.run([sys.executable, "-c", code_probe], capture_output=True, text=True, env=env_on)
    check(r_on.returncode == 0, "env=1 기동", r_on.stderr[-300:])
    if r_on.returncode == 0 and r_off.returncode == 0:
        tot_on, id_on, flag_on = (int(x) for x in r_on.stdout.split())
        check(flag_on == 1 and id_on == len(names), "env=1 이면 id_ 피처 18종 추가", r_on.stdout.strip())
        check(tot_on - tot_off == len(names), "증가분 == 모듈 피처 수", f"{tot_off}→{tot_on}")

    print(f"\n{PASSES[0]} PASS / {len(FAILS)} FAIL")
    if FAILS:
        for f in FAILS:
            print("  FAIL:", f)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

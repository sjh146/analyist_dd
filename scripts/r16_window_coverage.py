#!/usr/bin/env python3
"""R16 — R10 19개 피처 커버리지를 **모델 창**(기본 250일) 기준으로 실측한다. (읽기 전용)

WHY: R10 이 `feature_coverage` 에 쓴 커버리지는 격자 전체(464일)를 분모로 한 값이라, 모델이
실제로 쓰는 최근 창(90~250일)에서는 값이 다르다. 목적은 R16 의 질문 — "격자에서 0.9% 로 보이던
피처가 모델 창에서는 쓸 수 있는가" 그리고 "무효가 아니라 **분모(유니버스)** 문제인가" — 를
숫자로 확정하는 것이다. 질문이 바뀌면 결론도 바뀐다(실측 2026-09-25: 수급 4종은 시장 분모
0.0784 → 값 보유 338종목 안에서는 0.9370).

⚠ 이 스크립트는 `feature_coverage` 에 **아무것도 쓰지 않는다** — 의도적이다.
   `feature_coverage` 는 feature_name 이 PK 인 **현재상태 테이블**이다. 같은 19행을
   window_days=250 으로 upsert 하면 464일 값이 사라지고, 그 테이블을 세는 북극성 지표
   (alive/dead · 종목상수 기준선)가 '창 정의 변경'만으로 뒤집힌다(실측 2026-09-25:
   부분 재계산 한 번에 alive/dead 76/97 → 16/3, stock_constant 0.3816 → 0.625).
   → 창 기준 실측치는 JSON+표로만 남기고, 엔지니어 인계는 docs/QUANT_MODEL_BACKLOG.json 의
     XR16(이미 생성)이 담당한다.

정의는 `build_supply_market_features.compute_coverage_rows` 를 그대로 import 해 쓴다 —
같은 정의를 두 곳에서 다시 구현하면 판정이 갈라진다(계약 #2 NaN≠0 포함: 커버리지는
`IS NOT NULL AND != 0` 으로 센다).

사용:
  /usr/bin/python3 scripts/r16_window_coverage.py --window-days 250           # 실측 + JSON + 표
  /usr/bin/python3 scripts/r16_window_coverage.py --check --window-days 250   # 마지막 줄에 개수(정수)
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import psycopg2  # noqa: E402

import build_supply_market_features as bsmf  # noqa: E402

CACHE = os.path.join(PROJ, "data/reports/r16_window_coverage.json")
SELECT_THRESHOLD = 0.044      # top30 선별 문턱(≈) — 이 아래는 선별에 못 든다
UNIQ_MIN = 2                  # 종목별 서로 다른 값이 2개 이상이어야 횡단면 변별
XSEC_MARKET_LEVEL = 0.99      # 계약 #6: 시장 전체 동일값이면 횡단면 피처로 쓰지 않는다
UNIVERSE_MISSING = 0.50       # 결측 50% 이상 = 원천 유니버스 부족(피처 결함이 아니다)
CACHE_MAX_HOURS = 24


def env_from_dotenv():
    """POSTGRES_* 가 환경에 없으면 .env 에서 채운다(단독 실행 대비). 값은 출력하지 않는다."""
    path = os.path.join(PROJ, ".env")
    try:
        with open(path, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln or ln.startswith("#") or "=" not in ln:
                    continue
                k, v = ln.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k.startswith("POSTGRES_") and not os.environ.get(k):
                    os.environ[k] = v
    except OSError:
        pass


def connect():
    env_from_dotenv()
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT", "5434")),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""))


def measure(window_days):
    """저장된 피처 테이블 ⋈ market_data 격자의 최근 window_days 창을 실측한다."""
    import pandas as pd  # noqa: E402

    cols = ", ".join("s." + c for c in bsmf.FEATURES)
    sql = f"""
        SELECT s.stock_code, s.trade_date, {cols}
        FROM {bsmf.TABLE} s
        JOIN market_data m
          ON m.stock_code = s.stock_code AND m.trade_date = s.trade_date
        WHERE m.trade_date >= (SELECT MAX(trade_date) - %s FROM market_data)
    """
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(trade_date) FROM market_data")
            last = cur.fetchone()[0]
            if last is None:
                raise RuntimeError("market_data 가 비었다")
            cur.execute(sql, (window_days - 1,))
            rows = cur.fetchall()
            names = [d[0] for d in cur.description]
    finally:
        conn.close()

    df = pd.DataFrame(rows, columns=names)
    if df.empty:
        raise RuntimeError("창 안에 격자 행이 없다 — supply_market_features 확인")
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    grid = df.set_index(["stock_code", "trade_date"])
    computed_at = datetime.now(timezone.utc)
    cov = bsmf.compute_coverage_rows(grid, window_days, computed_at)
    meta = {
        "window_days": int(window_days),
        "last_market_date": str(last),
        "window_from": str((last - timedelta(days=window_days - 1))),
        "grid_rows": int(len(grid)),
        "stocks": int(grid.index.get_level_values("stock_code").nunique()),
        "dates": int(grid.index.get_level_values("trade_date").nunique()),
        "computed_at": computed_at.isoformat(timespec="seconds"),
        "thresholds": {"select": SELECT_THRESHOLD, "uniq_min": UNIQ_MIN,
                       "xsec_market_level": XSEC_MARKET_LEVEL,
                       "universe_missing": UNIVERSE_MISSING},
    }
    return meta, cov


def verdict(r):
    """피처 1개의 판정 — '왜 못 쓰는가'를 계약 항목으로 돌려준다.

    순서 주의: xsec(시장레벨) 판정을 **결측이 낮은 피처에만** 적용한다. `nunique` 는 NaN 을
    세지 않으므로, 결측이 99% 인 피처는 '값 1개인 날'이 다수라 xsec 가 1.0 에 가깝게 나온다 —
    그건 시장레벨이 아니라 **원천 부재**다(실측: institution_ownership_pct null 1.0 / xsec 1.0).
    """
    nz, uniq, xsec, nul = (r["nonzero_ratio"], r["stock_unique_median"],
                           r["cross_section_constant_ratio"], r["null_ratio"])
    if nul >= UNIVERSE_MISSING:
        if nz >= SELECT_THRESHOLD and uniq >= UNIQ_MIN:
            return "universe_limited(조건부 — 값 보유 행에서 nz %.3f)" % (nz / max(1e-9, 1.0 - nul))
        return "universe_missing(원천 부족 — 피처 결함 아님)"
    if xsec >= XSEC_MARKET_LEVEL:
        return "market_level(계약#6 횡단면 금지)"
    if nz >= SELECT_THRESHOLD and uniq >= UNIQ_MIN:
        return "SELECTABLE(문턱 %.1fx)" % (nz / SELECT_THRESHOLD)
    if nz >= SELECT_THRESHOLD:
        return "xsec_flat(종목별 값 1개 — 횡단면 무변별)"
    return "below(<문턱; %.0f%%)" % (100.0 * nz / SELECT_THRESHOLD)


def load_cache(window_days, max_hours=CACHE_MAX_HOURS):
    try:
        with open(CACHE, encoding="utf-8") as f:
            c = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if int(c.get("meta", {}).get("window_days", -1)) != int(window_days):
        return None
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(c["meta"]["computed_at"])
    except (KeyError, ValueError):
        return None
    if age > timedelta(hours=max_hours):
        return None
    return c


def main():
    ap = argparse.ArgumentParser(description="R16 — 창 기준 커버리지 실측(읽기 전용)")
    ap.add_argument("--window-days", type=int, default=250)
    ap.add_argument("--check", action="store_true",
                    help="마지막 줄에 '엔지니어 제안 가능' 피처 수(정수)를 출력")
    ap.add_argument("--no-cache", action="store_true")
    a = ap.parse_args()

    c = None if a.no_cache else load_cache(a.window_days)
    if c is None:
        meta, cov = measure(a.window_days)
        for r in cov:
            # compute_coverage_rows 는 computed_at 을 datetime 으로 넣는다 → JSON 직렬화 불가.
            r["computed_at"] = str(r["computed_at"])
            r["verdict"] = verdict(r)
        c = {"meta": meta, "features": cov}
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        tmp = CACHE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CACHE)
        src = "실측"
    else:
        src = "캐시(%.1fh 전)" % ((datetime.now(timezone.utc)
                               - datetime.fromisoformat(c["meta"]["computed_at"])).total_seconds() / 3600)

    usable = [r for r in c["features"] if r["verdict"].startswith("SELECTABLE")]
    meta = c["meta"]
    print("[R16] 창 %s일 (%s ~ %s) | 격자 %d행 / %d종목 / %d거래일 | 판정 %s"
          % (meta["window_days"], meta["window_from"], meta["last_market_date"],
             meta["grid_rows"], meta["stocks"], meta["dates"], src))
    print("  %-30s %8s %8s %8s %8s  %s" % ("feature", "nonzero", "null", "uniq_med", "xsec", "verdict"))
    for r in sorted(c["features"], key=lambda x: -x["nonzero_ratio"]):
        print("  %-30s %8.4f %8.4f %8s %8.4f  %s"
              % (r["feature_name"], r["nonzero_ratio"], r["null_ratio"],
                 ("%g" % r["stock_unique_median"]), r["cross_section_constant_ratio"], r["verdict"]))
    print("  → SELECTABLE %d개: %s" % (len(usable), ", ".join(r["feature_name"] for r in usable) or "없음"))
    print("  → JSON: %s (feature_coverage 에는 쓰지 않음 — 창 정의가 전역 기준선을 뒤집는다)"
          % os.path.relpath(CACHE, PROJ))
    if a.check:
        print(len(usable))
    return 0


if __name__ == "__main__":
    sys.exit(main())

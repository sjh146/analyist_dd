#!/usr/bin/env python3
"""KIS OpenAPI 로 **시장 전체 일별 프로그램매매 대금** 을 수집해 program_trading_ratio 피처를 살린다.

무엇을 하는가
  KIS `comp-program-trade-daily` (TR_ID FHPPG04600001, 프로그램매매 종합조회(일별)) 를
  시장별(KOSPI: FID_MRKT_CLS_CODE=K, KOSDAQ: Q)로 조회해 `krx_program_trading` 에 **1행/일/시장** 을 적재한다.

리더와의 정합 (services/xgboost-ml/app/feature_engine/feature_pipeline.py 515~533행)
    features["program_trading_ratio"] = (foreign_buy_value + foreign_sell_value) / total_value
    WHERE trade_date=%s AND market='KOSPI'
  → 즉 이 피처는 **프로그램 매매 회전율 비중** = (프로그램 매수 + 프로그램 매도) / 시장 전체 거래대금.
  KIS 일별 프로그램 API 는 투자자(외국인/기관) 구분 값을 주지 않는다(장중 API 인
  comp-program-trade-today 만 외국인 분리를 제공하고, 과거 일자 백필은 불가).
  그래서 컬럼명은 foreign_* 이지만 값은 **시장 전체 프로그램 매수/매도 대금** 을 넣는다
  (13_krx_derivatives_program_migration.sql 의 COMMENT 참고).

필드 매핑 (실측 응답, 2026-09-18)
  whol_smtn_shnu_tr_pbmn : 전체 프로그램 매수 대금 (백만원) → foreign_buy_value  (×1e6 = 원)
  whol_smtn_seln_tr_pbmn : 전체 프로그램 매도 대금 (백만원) → foreign_sell_value (×1e6 = 원)
  whol_smtn_ntby_tr_pbmn : 전체 프로그램 순매수 대금(백만원) → net_buy_value     (×1e6 = 원)
  total_value            : 시장 전체 거래대금(원) — krx_trading(investor_type='Total') 를 우선 사용,
                           없으면 market_data 의 해당 시장 종목 거래대금 합계.

호출 정책
  - KIS 토큰은 파일 캐시(data/kis/token_cache.json) 재사용, 없으면 1회 발급(분당 1회 제한).
  - HTTP 는 curl 사용(파이썬 requests/urllib 는 KIS WAF 403 — 리포 관례).
  - 호출 간 3.0s + 지터. 날짜 구간은 20일 창으로 청크.

사용 (호스트):
  set -a; . .env; set +a
  /usr/bin/python3 scripts/kis_program_trading_collect.py --dry-run
  /usr/bin/python3 scripts/kis_program_trading_collect.py --from 20260501 --to 20260923
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

import psycopg2

PROJ = os.environ.get("PROJ_DIR", "/home/jhshi/analyist_dd")
TOKEN_PATH = os.path.join(PROJ, os.environ.get("KIS_TOKEN_PATH", "data/kis/token_cache.json"))

PROGRAM_PATH = "/uapi/domestic-stock/v1/quotations/comp-program-trade-daily"
PROGRAM_TR_ID = "FHPPG04600001"
MARKETS = {"KOSPI": "K", "KOSDAQ": "Q"}     # FID_MRKT_CLS_CODE
CHUNK_DAYS = 20
DELAY = float(os.environ.get("KIS_REQUEST_DELAY", "3.0"))
JITTER = float(os.environ.get("KIS_REQUEST_JITTER", "0.5"))

PROGRAM_UNIT = 1_000_000   # KIS 프로그램 대금 단위 = 백만원 → 원

PG = dict(
    host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
    port=int(os.environ.get("POSTGRES_PORT", "5434")),
    user=os.environ.get("POSTGRES_USER", "stock_user"),
    password=os.environ.get("POSTGRES_PASSWORD", ""),
    dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
)

UPSERT = """
INSERT INTO krx_program_trading
  (trade_date, market, foreign_buy_value, foreign_sell_value, total_value, net_buy_value)
VALUES (%s,%s,%s,%s,%s,%s)
ON CONFLICT (trade_date, market) DO UPDATE SET
  foreign_buy_value=EXCLUDED.foreign_buy_value,
  foreign_sell_value=EXCLUDED.foreign_sell_value,
  total_value=EXCLUDED.total_value,
  net_buy_value=EXCLUDED.net_buy_value
"""


class KisError(Exception):
    pass


def load_env():
    env = {}
    with open(os.path.join(PROJ, ".env"), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def curl(args, timeout=30):
    cmd = ["curl", "--silent", "--show-error", "--location", "--max-time", str(timeout),
           "--compressed", "--write-out", "\n__HTTP_STATUS__%{http_code}"] + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 10)
    body, _, marker = r.stdout.rpartition("\n__HTTP_STATUS__")
    return (int(marker.strip()) if marker.strip().isdigit() else 0), body


def get_token(appkey, appsecret, base_url):
    if os.path.exists(TOKEN_PATH):
        try:
            d = json.load(open(TOKEN_PATH, encoding="utf-8"))
            if d.get("access_token") and d.get("expire_at", 0) > time.time() + 300:
                return d["access_token"]
        except Exception:  # noqa: BLE001 — 캐시 손상 시 재발급
            pass
    status, out = curl(["--request", "POST", "--url", base_url + "/oauth2/tokenP",
                        "--header", "Content-Type: application/json",
                        "--data", json.dumps({"grant_type": "client_credentials",
                                              "appkey": appkey, "appsecret": appsecret})])
    try:
        d = json.loads(out)
    except Exception as e:  # noqa: BLE001
        raise KisError(f"토큰 응답 비JSON(HTTP {status})") from e
    if not d.get("access_token"):
        raise KisError(f"토큰 발급 실패: {d.get('error_code')} {d.get('error_description') or d.get('msg1')}")
    os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
    json.dump({"access_token": d["access_token"],
               "expire_at": time.time() + int(d.get("expires_in", 86400))},
              open(TOKEN_PATH, "w", encoding="utf-8"))
    print("KIS 토큰 발급(파일 캐시 저장)", flush=True)
    return d["access_token"]


def call(appkey, appsecret, base_url, token, path, tr_id, params):
    url = base_url + path + "?" + urlencode(params)
    headers = {"authorization": f"Bearer {token}", "appkey": appkey, "appsecret": appsecret,
               "tr_id": tr_id, "content-type": "application/json; charset=utf-8"}
    args = ["--request", "GET", "--url", url]
    for k, v in headers.items():
        args += ["--header", f"{k}: {v}"]
    status, out = curl(args)
    if status in (401, 403, 429) or status >= 500:
        raise KisError(f"{path} HTTP {status} — 중단")
    try:
        d = json.loads(out)
    except Exception as e:  # noqa: BLE001
        raise KisError(f"{path} 비JSON 응답(HTTP {status}) {out[:150]}") from e
    if d.get("rt_cd") != "0":
        raise KisError(f"{path} rt_cd={d.get('rt_cd')} [{d.get('msg_cd')}] {d.get('msg1')}")
    return d.get("output") or []


def num(v):
    s = str(v if v is not None else "").strip().replace(",", "")
    if not s or s == "-":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def chunks(start, end):
    d = start
    while d <= end:
        yield d, min(d + timedelta(days=CHUNK_DAYS - 1), end)
        d = d + timedelta(days=CHUNK_DAYS)


def main():
    ap = argparse.ArgumentParser(description="KIS 일별 프로그램매매 대금 → krx_program_trading")
    ap.add_argument("--from", dest="d_from", default=None, help="YYYYMMDD 또는 YYYY-MM-DD")
    ap.add_argument("--to", dest="d_to", default=None)
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--limit", type=int, default=0, help="점검용 시장 수 제한(1=KOSPI만)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    today = date.today()
    start = (date.fromisoformat(args.d_from) if "-" in (args.d_from or "")
             else datetime.strptime(args.d_from, "%Y%m%d").date()) if args.d_from \
        else today - timedelta(days=args.days)
    end = (date.fromisoformat(args.d_to) if "-" in (args.d_to or "")
           else datetime.strptime(args.d_to, "%Y%m%d").date()) if args.d_to else today
    if end >= today:
        end = today - timedelta(days=1)

    mkts = list(MARKETS.items())[:args.limit] if args.limit else list(MARKETS.items())
    windows = list(chunks(start, end))
    print(f"구간 {start} ~ {end}: {len(windows)}창 × {len(mkts)}시장 = {len(windows) * len(mkts)}콜", flush=True)
    if args.dry_run:
        return 0

    env = load_env()
    appkey, appsecret = env.get("KIS_APP_KEY", ""), env.get("KIS_APP_SECRET", "")
    base_url = env.get("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443").rstrip("/")
    if not appkey or not appsecret:
        print("KIS_APP_KEY/KIS_APP_SECRET 미설정", flush=True)
        return 2

    token = get_token(appkey, appsecret, base_url)
    conn = psycopg2.connect(**PG)
    rows_written = 0
    try:
        for market, cls in mkts:
            for w_from, w_to in windows:
                out = call(appkey, appsecret, base_url, token, PROGRAM_PATH, PROGRAM_TR_ID, {
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_MRKT_CLS_CODE": cls,
                    "FID_INPUT_DATE_1": w_from.strftime("%Y%m%d"),
                    "FID_INPUT_DATE_2": w_to.strftime("%Y%m%d"),
                })
                for r in out:
                    ds = str(r.get("stck_bsop_date") or "").strip()
                    if len(ds) != 8 or not ds.isdigit():
                        continue
                    d = datetime.strptime(ds, "%Y%m%d").date()
                    buy = num(r.get("whol_smtn_shnu_tr_pbmn"))
                    sell = num(r.get("whol_smtn_seln_tr_pbmn"))
                    net = num(r.get("whol_smtn_ntby_tr_pbmn"))
                    if buy is None and sell is None:
                        continue
                    cur = conn.cursor()
                    cur.execute("SELECT trading_value FROM krx_trading "
                                "WHERE trade_date=%s AND market=%s AND investor_type='Total' LIMIT 1",
                                (d, market))
                    tv = cur.fetchone()
                    total = int(tv[0]) if tv and tv[0] else None
                    if total is None:   # 폴백: market_data 거래대금 합
                        cur.execute("""SELECT SUM(md.trading_value) FROM market_data md
                                       JOIN stocks s ON s.stock_code = md.stock_code
                                       WHERE md.trade_date = %s AND s.market = %s""", (d, market))
                        tv2 = cur.fetchone()
                        total = int(tv2[0]) if tv2 and tv2[0] else None
                    cur.execute(UPSERT, (
                        d, market,
                        int(buy * PROGRAM_UNIT) if buy is not None else None,
                        int(sell * PROGRAM_UNIT) if sell is not None else None,
                        total,
                        int(net * PROGRAM_UNIT) if net is not None else None,
                    ))
                    cur.close()
                    rows_written += 1
                conn.commit()
                print(f"{market} {w_from}~{w_to}: {len(out)}행 upsert(누적 {rows_written})", flush=True)
                time.sleep(DELAY + random.uniform(0, JITTER))
    except KisError as e:
        print(f"중단: {e}", flush=True)
        return 3
    finally:
        conn.close()
    print(f"완료: {rows_written}행", flush=True)
    return 0


if __name__ == "__main__":
    random.seed()
    sys.exit(main())

#!/usr/bin/env python3
"""KRX OpenAPI 로 **파생상품(지수선물) + 코스피200 지수** 를 수집해 파생 피처를 살린다.

무엇을 하는가
  1) drv/fut_bydd_trd  (선물 일별매매정보, 주식선물 外)  → krx_derivatives (상품별 1행/일)
  2) idx/kospi_dd_trd  (KOSPI 시리즈 일별시세정보)        → 코스피200 현물지수(교차검증용)
  3) 1)+2) 로 futures_options 1행/일 (futures_price, basis) → basis / basis_change_5d 피처

피처 ↔ 리더 매핑 (services/xgboost-ml/app/feature_engine/)
  - futures_premium    = krx_derivatives.close_price WHERE index_name='KOSPI200'  (feature_pipeline 764~780행)
  - derivatives_volume = SUM(krx_derivatives.volume) WHERE trade_date=...          (feature_pipeline 782~797행)
  - basis              = futures_options.basis                                     (market_features 194~225행)
  - basis_change_5d    = basis(t) − basis(t−5거래일)  (위 리더와 동일 정의, LIMIT 6 의 rows[0]-rows[5])

basis 정의 (근거)
  베이시스 = **코스피200 선물(프론트월) 종가 − 코스피200 지수** (지수 포인트).
   - KRX 응답의 SPOT_PRC 가 그 행의 현물지수(코스피200 F 202612 행의 SPOT_PRC=1090.23)를 직접 준다.
     → idx/kospi_dd_trd 의 '코스피 200'.CLSPRC_IDX 와 교차검증(불일치 시 idx 값 우선, 경고 로그).
   - 리포의 옛 수집기 services/yfinance-collector/app/collectors/derivatives_collector.py 는
     같은 값의 **비율판** (futures−spot)/spot×100 을 basis 로 썼지만(데이터 0행, pykrx 의존으로 실패),
     여기서는 리더가 정의를 강제하지 않으므로 지수 포인트 차(표준 '베이시스')로 적재한다.
     futures_options.futures_price 에 선물 종가를 함께 남겨 두 정의 모두 재구성 가능하다.

호출 정책 (IP 차단 이력 대응 — scripts/krx_daily.py 와 동일 원칙)
  - 날짜 1개 = 2콜(선물 + 지수). 호출 간 3.0s + 지터 0~0.5s (env 로 조정).
  - 401/403/429/5xx 또는 respCode!=200 → **재시도 없이 즉시 중단**.
  - 실행당 호출 상한(기본 600) + 진행파일로 재개.

사용 (호스트):
  set -a; . .env; set +a
  /usr/bin/python3 scripts/krx_derivatives_collect.py --dry-run
  /usr/bin/python3 scripts/krx_derivatives_collect.py --from 20260501 --to 20260923
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta

import psycopg2

PROJ = os.environ.get("PROJ_DIR", "/home/jhshi/analyist_dd")
HOLIDAY_PATH = os.path.join(PROJ, "data", "krx_holidays.json")
PROGRESS_DIR = os.path.join(PROJ, "data", "krx")

DELAY = float(os.environ.get("KRX_REQUEST_DELAY", "3.0"))
JITTER = float(os.environ.get("KRX_REQUEST_JITTER", "0.5"))
MAX_CALLS = int(os.environ.get("KRX_MAX_CALLS", "600"))

FUT_PATH = "drv/fut_bydd_trd"     # 선물 일별매매정보(주식선물 外) — 승인·실측 200 OK
IDX_PATH = "idx/kospi_dd_trd"     # KOSPI 시리즈 일별시세정보 — 승인·실측 200 OK
BASE_CANDIDATES = [u.strip() for u in os.environ.get(
    "KRX_BASE_URLS", "https://data-dbg.krx.co.kr/svc/apis").split(",") if u.strip()]

# 상품명 → index_name. 리더가 index_name='KOSPI200' 을 유일하게 찾아야 하므로
# 코스피200 선물만 'KOSPI200' 이고 나머지는 다른 이름을 쓴다(중복 매칭 방지).
INDEX_MAP = {
    "코스피200 선물": "KOSPI200",
    "미니코스피200 선물": "KOSPI200_MINI",
    "코스닥150 선물": "KOSDAQ150",
    "KRX300 선물": "KRX300",
    "미국달러 선물": "USD_KRW",
    "엔 선물": "JPY_KRW",
    "유로 선물": "EUR_KRW",
    "위안 선물": "CNY_KRW",
    "3년국채 선물": "KTB3Y",
    "10년국채 선물": "KTB10Y",
    "30년국채 선물": "KTB30Y",
    "금 선물": "GOLD_FUT",
    "변동성지수 선물": "VKOSPI_FUT",
}
SPOT_INDEX_NAME = "코스피 200"      # idx/kospi_dd_trd 의 IDX_NM

PG = dict(
    host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
    port=int(os.environ.get("POSTGRES_PORT", "5434")),
    user=os.environ.get("POSTGRES_USER", "stock_user"),
    password=os.environ.get("POSTGRES_PASSWORD", ""),
    dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
)

UPSERT_DERIV = """
INSERT INTO krx_derivatives
  (trade_date, product_type, contract_type, price, close_price, volume,
   open_interest, change_pct, index_name)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT (trade_date, product_type) DO UPDATE SET
  contract_type=EXCLUDED.contract_type, price=EXCLUDED.price,
  close_price=EXCLUDED.close_price, volume=EXCLUDED.volume,
  open_interest=EXCLUDED.open_interest, change_pct=EXCLUDED.change_pct,
  index_name=EXCLUDED.index_name
"""

UPSERT_FUTOPT = """
INSERT INTO futures_options (trade_date, futures_price, basis)
VALUES (%s,%s,%s)
ON CONFLICT (trade_date) DO UPDATE SET
  futures_price=EXCLUDED.futures_price, basis=EXCLUDED.basis
"""


class KrxBlocked(Exception):
    """차단/비승인/오류 신호 — 재시도 금지, 즉시 종료 (IP 보호)."""


def num(v):
    """'1,234' / '' / '-' → float|None. (KRX 는 빈 값을 '' 로 준다)"""
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if not s or s in {"-"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def inum(v):
    n = num(v)
    return int(n) if n is not None else None


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def fetch(path, key, base, basdd):
    url = f"{base}/{path}?basDd={basdd}"
    req = urllib.request.Request(url, headers={"AUTH_KEY": key, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8", "replace")
            code = r.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        code = e.code
    except Exception as e:  # noqa: BLE001
        raise KrxBlocked(f"전송 오류({e}) — 재시도하지 않고 중단 (IP 보호)")
    if code in (401, 403, 429) or code >= 500:
        raise KrxBlocked(f"{path} HTTP {code} {raw.strip()[:120]} — 재시도 없이 종료")
    try:
        data = json.loads(raw)
    except ValueError:
        raise KrxBlocked(f"{path} 비JSON 응답(HTTP {code}) {raw.strip()[:120]}")
    if data.get("respCode") not in (None, "", "200", "0"):
        raise KrxBlocked(f"{path} respCode={data.get('respCode')} {data.get('respMsg')}")
    rows = data.get("OutBlock_1")
    if rows is None:
        raise KrxBlocked(f"{path} OutBlock_1 없음(HTTP {code})")
    return rows


def trading_dates(start, end, holidays):
    out, d = [], start
    while d <= end:
        if d.weekday() < 5 and d.isoformat() not in holidays:
            out.append(d)
        d += timedelta(days=1)
    return out


def front_contract(rows):
    """프론트월 계약 선정 = 정규장 응답 중 종가가 있는 계약 중 거래량 최대 (동률 시 코드순).

    스프레드(ISU_NM 에 ' SP ', ISU_CD 'D...')는 교환 가능한 '선물 계약'이 아니므로
    단독 계약이 하나라도 있으면 그것들 중에서 고른다(통화/금리 선물에서 스프레드가
    최대 거래량을 차지하는 경우 close_price 가 스프레드 값이 되는 것을 막는다).
    """
    cand = [r for r in rows
            if (r.get("MKT_NM") or "").strip() == "정규" and num(r.get("TDD_CLSPRC")) is not None]
    if not cand:
        return None
    single = [r for r in cand if " SP " not in (r.get("ISU_NM") or "")
              and not str(r.get("ISU_CD") or "").startswith("D")]
    pool = single or cand
    return sorted(pool, key=lambda r: (-(inum(r.get("ACC_TRDVOL")) or 0), str(r.get("ISU_NM"))))[0]


def contract_code(isu_nm):
    """'코스피200 F 202612 (주간)' → '202612', '... SP 2612-2703 (주간)' → '2612-2703' (varchar(10))."""
    s = (isu_nm or "").replace("(주간)", "").replace("(야간)", "").strip()
    for tok in (" F ", " SP "):
        if tok in s:
            return s.split(tok)[-1].strip().replace(" ", "")[:10]
    return s[:10]


def build_rows(rows, basdd, d):
    """KRX 선물 응답 → krx_derivatives 행 목록. 야간(시간외) 세션은 제외(정규장 기준)."""
    out = {}
    for r in rows:
        if (r.get("MKT_NM") or "").strip() != "정규":
            continue
        prod = (r.get("PROD_NM") or "").strip()
        if not prod:
            continue
        out.setdefault(prod, []).append(r)

    res = []
    for prod, prows in out.items():
        front = front_contract(prows)
        if front is None:                      # 그날 그 상품에 체결이 없음 → 스킵
            continue
        close = num(front.get("TDD_CLSPRC"))
        vol = sum(inum(r.get("ACC_TRDVOL")) or 0 for r in prows)
        oi = sum(inum(r.get("ACC_OPNINT_QTY")) or 0 for r in prows)
        prev_diff = num(front.get("CMPPREVDD_PRC"))
        chg = None
        if prev_diff is not None and close is not None:
            prev = close - prev_diff
            if prev > 0:
                chg = round(prev_diff / prev * 100, 2)
        res.append((
            d, prod[:30], contract_code(front.get("ISU_NM")), round(close, 2), round(close, 2),
            vol, oi, chg, INDEX_MAP.get(prod),
        ))
    return res


def spot_from_idx(rows):
    for r in rows:
        if (r.get("IDX_NM") or "").strip() == SPOT_INDEX_NAME:
            return num(r.get("CLSPRC_IDX"))
    return None


def resolve_base(key):
    for base in BASE_CANDIDATES:
        try:
            rows = fetch(FUT_PATH, key, base, "20260918")
            if rows:
                print(f"KRX base: {base} (응답 {len(rows)}행)", flush=True)
                return base
        except Exception as e:  # noqa: BLE001
            print(f"  base 후보 실패 {base}: {e}", flush=True)
    raise KrxBlocked("KRX OpenAPI 호스트를 찾지 못했습니다")


def main():
    ap = argparse.ArgumentParser(description="KRX 파생(지수선물)·코스피200 지수 → krx_derivatives/futures_options")
    ap.add_argument("--from", dest="d_from", default=None, help="YYYYMMDD 또는 YYYY-MM-DD")
    ap.add_argument("--to", dest="d_to", default=None)
    ap.add_argument("--days", type=int, default=0, help="오늘 기준 최근 N일")
    ap.add_argument("--limit", type=int, default=0, help="점검용 날짜 수 제한")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    key = os.environ.get("KRX_API_KEY", "").strip()
    today = date.today()
    start = (date.fromisoformat(args.d_from) if "-" in (args.d_from or "")
             else datetime.strptime(args.d_from, "%Y%m%d").date()) if args.d_from \
        else today - timedelta(days=args.days or 90)
    end = (date.fromisoformat(args.d_to) if "-" in (args.d_to or "")
           else datetime.strptime(args.d_to, "%Y%m%d").date()) if args.d_to else today
    if end >= today:
        end = today - timedelta(days=1)

    holidays = set(load_json(HOLIDAY_PATH, []))
    dates = trading_dates(start, end, holidays)
    if args.limit:
        dates = dates[:args.limit]
    calls = len(dates) * 2
    print(f"구간 {start} ~ {end}: 영업일 {len(dates)}일 × 2콜 = {calls}콜 "
          f"(예상 {calls * (DELAY + JITTER / 2) / 60:.1f}분, 상한 {MAX_CALLS}콜)", flush=True)
    if args.dry_run:
        print("[dry-run] 첫 5일:", ", ".join(d.strftime("%Y%m%d") for d in dates[:5]), flush=True)
        return 0
    if not key:
        print("KRX_API_KEY 미설정", flush=True)
        return 2

    os.makedirs(PROGRESS_DIR, exist_ok=True)
    prog = os.path.join(PROGRESS_DIR, f"deriv_progress_{start:%Y%m%d}_{end:%Y%m%d}.txt")
    done = set()
    if os.path.exists(prog):
        with open(prog) as f:
            done = {l.strip() for l in f if l.strip()}

    base = resolve_base(key)
    conn = psycopg2.connect(**PG)
    n_days = n_deriv = n_skip = 0
    try:
        for d in dates:
            basdd = d.strftime("%Y%m%d")
            if basdd in done:
                continue
            if n_days * 2 >= MAX_CALLS:
                print(f"호출 상한 도달 — 중단(다음 실행에서 재개)", flush=True)
                break

            fut_rows = fetch(FUT_PATH, key, base, basdd)
            time.sleep(DELAY + random.uniform(0, JITTER))
            idx_rows = fetch(IDX_PATH, key, base, basdd)
            time.sleep(DELAY + random.uniform(0, JITTER))

            d_rows = build_rows(fut_rows, basdd, d)
            if not d_rows:
                print(f"{basdd}: 선물 시세 없음 → 휴장/미반영 스킵", flush=True)
                n_skip += 1
                with open(prog, "a") as f:
                    f.write(basdd + "\n")
                continue

            cur = conn.cursor()
            cur.executemany(UPSERT_DERIV, d_rows)

            k200 = next((r for r in d_rows if r[8] == "KOSPI200"), None)
            if k200:
                front_row = front_contract([r for r in fut_rows
                                            if (r.get("PROD_NM") or "").strip() == "코스피200 선물"
                                            and (r.get("MKT_NM") or "").strip() == "정규"])
                f_close = num(front_row.get("TDD_CLSPRC")) if front_row else None
                spot = num(front_row.get("SPOT_PRC")) if front_row else None
                idx_spot = spot_from_idx(idx_rows)
                if idx_spot is not None and spot is not None and abs(idx_spot - spot) > 0.01:
                    print(f"  경고 {basdd}: SPOT_PRC({spot}) != idx 코스피200({idx_spot}) → idx 값 사용", flush=True)
                if idx_spot is not None:
                    spot = idx_spot
                if f_close is not None and spot is not None:
                    cur.execute(UPSERT_FUTOPT, (d, round(f_close, 4), round(f_close - spot, 4)))
            conn.commit()
            cur.close()
            n_days += 1
            n_deriv += len(d_rows)
            with open(prog, "a") as f:
                f.write(basdd + "\n")
            print(f"{basdd}: krx_derivatives {len(d_rows)}상품 적재 (누적 {n_days}일)", flush=True)
    except KrxBlocked as e:
        print(f"중단: {e}", flush=True)
        return 3
    finally:
        conn.close()

    print(f"완료: {n_days}영업일, {n_deriv}행, 스킵 {n_skip}일", flush=True)
    return 0


if __name__ == "__main__":
    random.seed()
    sys.exit(main())

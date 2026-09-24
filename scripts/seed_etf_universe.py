#!/usr/bin/env python3
"""ETF/ETN 유니버스 시딩 — KRX 종목 마스터(.mst) 파싱 → ``stocks`` upsert.

KIS/KRX 마스터 파일(kospi_code.mst / kosdaq_code.mst)은 고정폭 레코드이며,
ETF/ETN은 ``그룹코드`` 로 구분된다(실측 2026-09):
    EF = ETF, EN = ETN (그 외 ST=주식, DR=예탁증서, BC=펀드 등)

레코드 레이아웃(실측, cp949, 줄바꿈 제외 288바이트):
    0..8    단축코드(6자리 + 우측 공백) — 종목코드
    9..20   표준코드(12자리, 예: KR700000D0009)
    21..60  한글명(40바이트, cp949, 공백 패딩)
    61..62  그룹코드(2바이트)

기존 종목은 건너뛰고(``ON CONFLICT ... DO NOTHING``), 기존 행의
``instrument_type`` 은 절대 덮어쓰지 않는다(회귀 방지).

사용 (호스트):
    set -a; . ./.env; set +a
    export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434
    python3 scripts/seed_etf_universe.py --dry-run
    python3 scripts/seed_etf_universe.py --markets kospi,kosdaq --types ETF,ETN
    python3 scripts/seed_etf_universe.py --limit 10
"""
from __future__ import annotations

import argparse
import io
import logging
import os
import sys
import urllib.request
import zipfile

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("seed_etf_universe")

# ─── 레코드 레이아웃 (실측 고정폭) ──────────────────────────────────────
SHORT_CODE_START, SHORT_CODE_END = 0, 9
STD_CODE_START, STD_CODE_END = 9, 21
NAME_START, NAME_END = 21, 61
GROUP_START, GROUP_END = 61, 63
MIN_RECORD_LEN = GROUP_END  # 63바이트 미만은 무시

# 그룹코드 → instrument_type 매핑
GROUP_TO_TYPE = {
    "EF": "ETF",
    "EN": "ETN",
    # 확장 여지: "DR": "DR", "BC": "FUND" 등은 이번 범위 아님
}

# 마스터 파일 다운로드 URL (KIS/KRX 공통 배포)
MASTER_URLS = {
    "kospi": "https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip",
    "kosdaq": "https://new.real.download.dws.co.kr/common/master/kosdaq_code.mst.zip",
}
MARKET_FOR_FILE = {"kospi": "KOSPI", "kosdaq": "KOSDAQ"}


def parse_record(raw: bytes):
    """고정폭 레코드(줄바꿈 제외) → dict | None.

    잘못된 라인(너무 짧음/디코딩 실패/빈 코드)은 ``None`` (호출부에서 무시).
    """
    if not raw or len(raw) < MIN_RECORD_LEN:
        return None
    try:
        short = raw[SHORT_CODE_START:SHORT_CODE_END].decode("latin-1").strip()
        std = raw[STD_CODE_START:STD_CODE_END].decode("latin-1").strip()
        name = raw[NAME_START:NAME_END].decode("cp949").strip()
        group = raw[GROUP_START:GROUP_END].decode("latin-1").strip()
    except (UnicodeDecodeError, ValueError):
        return None
    if not short:
        return None
    return {
        "stock_code": short,
        "std_code": std,
        "stock_name": name or short,
        "group": group,
    }


def parse_master(data: bytes, market: str):
    """마스터 파일 전체 → 레코드 dict 리스트(그룹코드 포함 전부).

    ``market`` 은 KOSPI/KOSDAQ — 마스터 파일 기준.
    """
    records = []
    for line in data.split(b"\n"):
        rec = parse_record(line)
        if rec is None:
            continue
        rec["market"] = market
        records.append(rec)
    return records


def download_master(market: str) -> bytes:
    """zip 다운로드 → 내부 .mst 바이트 반환."""
    url = MASTER_URLS[market]
    log.info("다운로드: %s", url)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        blob = resp.read()
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        name = next(n for n in zf.namelist() if n.endswith(".mst"))
        return zf.read(name)


def filter_targets(records, types):
    """그룹코드가 요청한 instrument_type 에 해당하는 레코드만 + instrument_type 부여."""
    wanted = {t.upper() for t in types}
    out = []
    for r in records:
        itype = GROUP_TO_TYPE.get(r["group"])
        if itype is None or itype not in wanted:
            continue
        r["instrument_type"] = itype
        out.append(r)
    return out


# ─── PostgreSQL ────────────────────────────────────────────────────────

UPSERT_SQL = """
    INSERT INTO stocks (stock_code, stock_name, market, instrument_type, sector)
    VALUES (%s, %s, %s, %s, %s)
    ON CONFLICT (stock_code) DO NOTHING
"""


def pg_connect():
    import psycopg2

    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT", "5434")),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def upsert_stocks(records, dry_run=False):
    """stocks upsert (중복 skip, instrument_type 덮어쓰기 금지).

    반환: {"inserted": n, "skipped": n}
    """
    inserted = skipped = 0
    if dry_run:
        for r in records:
            log.info(
                "[dry-run] %s %s %s (%s) sector=%s",
                r["market"], r["stock_code"], r["stock_name"],
                r["instrument_type"], r["instrument_type"],
            )
            inserted += 1
        return {"inserted": inserted, "skipped": 0}

    conn = pg_connect()
    try:
        cur = conn.cursor()
        for r in records:
            try:
                cur.execute(
                    UPSERT_SQL,
                    (
                        r["stock_code"],
                        r["stock_name"],
                        r["market"],
                        r["instrument_type"],
                        r["instrument_type"],  # sector 표기: ETF/ETN
                    ),
                )
                if cur.rowcount:
                    inserted += 1
                else:
                    skipped += 1
            except Exception as e:  # noqa: BLE001 — 개별 레코드 실패는 로그 후 계속
                log.warning("저장 실패 %s(%s): %s", r["stock_code"], r["stock_name"], e)
                skipped += 1
        conn.commit()
        cur.close()
    finally:
        conn.close()
    return {"inserted": inserted, "skipped": skipped}


# ─── CLI ───────────────────────────────────────────────────────────────

def main(argv=None):
    ap = argparse.ArgumentParser(description="ETF/ETN 유니버스 시딩 (KRX 마스터 → stocks)")
    ap.add_argument("--markets", default="kospi,kosdaq",
                    help="마스터 파일 시장 (콤마 구분, 기본: kospi,kosdaq)")
    ap.add_argument("--types", default="ETF,ETN",
                    help="시딩할 instrument_type (콤마 구분, 기본: ETF,ETN)")
    ap.add_argument("--dry-run", action="store_true",
                    help="DB에 쓰지 않고 파싱 결과만 출력")
    ap.add_argument("--limit", type=int, default=0,
                    help="시딩 종목 수 제한(0=전체) — 점검용")
    args = ap.parse_args(argv)

    markets = [m.strip().lower() for m in args.markets.split(",") if m.strip()]
    types = [t.strip().upper() for t in args.types.split(",") if t.strip()]

    all_targets = []
    for market in markets:
        data = download_master(market)
        records = parse_master(data, MARKET_FOR_FILE[market])
        targets = filter_targets(records, types)
        log.info("%s: 전체 %d 레코드 → %s 대상 %d건",
                 market.upper(), len(records), ",".join(types), len(targets))
        all_targets.extend(targets)

    if args.limit:
        all_targets = all_targets[: args.limit]

    if not all_targets:
        log.warning("시딩 대상 없음")
        return 1

    # 중복 코드 제거 (kospi/kosdaq 간 이론상 겹침 방지 — 첫 등장 우선)
    seen = set()
    deduped = []
    for r in all_targets:
        if r["stock_code"] in seen:
            continue
        seen.add(r["stock_code"])
        deduped.append(r)
    log.info("중복 제거 후 시딩 대상 %d건", len(deduped))

    result = upsert_stocks(deduped, dry_run=args.dry_run)
    log.info("완료: inserted=%d skipped=%d (dry_run=%s)",
             result["inserted"], result["skipped"], args.dry_run)

    # 샘플 출력
    for r in deduped[:10]:
        log.info("  %s %s %s [%s]", r["market"], r["stock_code"],
                 r["stock_name"], r["instrument_type"])
    return 0


if __name__ == "__main__":
    sys.exit(main())

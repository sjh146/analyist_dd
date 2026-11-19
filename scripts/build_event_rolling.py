#!/usr/bin/env python3
"""build_event_rolling — event_features(5일 롤링 카운트) → 레벨·추세·묶음 피처 8개.

WHY (스펙 docs/spec_event_rolling.md, 작성 2026-11-19 23:05)
  기존 event_*_5d 54개가 nnz<5% 로 DEAD_SPARSE 판정. 이벤트는 원래 드물게 발생하므로
  **5일 창 단독으로는 임계를 못 넘는다**. 정의를 "발생 여부" 중심에서 **레벨(20행 평균)·
  추세(5행/20행)·묶음(자금조달·지배구조·영업·리스크)** 으로 바꿔 재판정한다. 이 스크립트는
  그 재판정에 쓸 신규 테이블 `event_rolling_features` 를 만든다(신규 테이블 1개만 —
  기존 테이블은 SELECT 만, 쓰기 금지).

실측 근거 (2026-11-20, 호스트 /usr/bin/python3 + psycopg2, 조회 명령 — 전부 그대로 재현 가능):
  # SELECT column_name, data_type FROM information_schema.columns
  #   WHERE table_name='event_features' ORDER BY ordinal_position
  #   → stock_code, trade_date, event_*_5d 16개, disclosure_count_5d, computed_at
  # SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM event_features
  #   → 223,204행 · 2025-06-17 ~ 2026-11-18 (스펙 "223,204행" 일치)
  # SELECT COUNT(DISTINCT trade_date) FROM event_features            → 324
  # SELECT COUNT(DISTINCT stock_code) FROM event_features            → 2,710
  #   all-zero 행 0건 → 이 테이블은 **비영 행만 저장**한다(행 = 이벤트가 있는 날).
  #   종목당 행수 median 76 / max 224 → "직전 20행"은 **이벤트-시간 창**(캘린더 20일 아님).
  # 패널 200종목 중 192 커버 — 스펙 "192 커버" 일치:
  #   SELECT COUNT(DISTINCT stock_code) FROM event_features
  #   WHERE stock_code = ANY(<panel_prod200_v4.npz 의 codes 200개>)  → 192

정의 (스펙 §1 그대로 — 추측 금지, 아래 해석은 전부 docstring 에 기록한다)
  - 입력은 event_features 의 **5일 롤링 카운트**(이미 창합) → 원천 재집계 금지(이중계산 방지).
  - as-of: 출력 (종목, D) 는 **trade_date < D 인 행만** 쓴다(당일 행 제외 — 엄격).
  - "총 이벤트 5d 카운트" = 16개 event_*_5d 의 합. **disclosure_count_5d 는 총합에서 제외**
    (스펙 §1 산출 8 이 disclosure 를 별도 컬럼으로 분리하므로 이중 반영 안 함. 미배정
    delisting·patent 는 포함). 이 정의가 아니면(공시 포함) any_60d 가 '비영 행만 저장'인
    원천에서 항상 1 로 상수화된다.
  - event_level_20d  = 직전 20행 총합의 평균
  - event_trend_5v20 = (직전 5행 평균) / (직전 20행 평균 + 1e-9)   ← 분모에만 1e-9 (스펙 문구)
  - event_any_60d    = 직전 60행(부족하면 있는 만큼) 중 총합>0 이면 1, 아니면 0.
                       직전 행이 1개라도 있어야 계산 — 0행이면 그 (종목,D) 행 자체를 내보내지
                       않는다(0 채움 금지).
  - funding/governance/business/risk_level_20d = 각 묶음 합의 직전 20행 평균
      funding    = capital_increase+cb_bw+mna+treasury
      governance = stake_change+exec_change
      business   = contract+new_product+partnership+realized
      risk       = litigation+disaster+regulation+recall
  - disclosure_level_20d = disclosure_count_5d 의 직전 20행 평균
  - **창 부족(직전 20행 미만)은 null** — 0으로 메우지 않는다(스펙 §5).
  - 출력은 종목·거래일별로 캘린더(=원천의 고유 trade_date)마다 나간다 — 이벤트가 없는 날에도
    나가므로 패널 패치의 as-of 조인이 빈틈없다.

멱등 설계
  - 출력 행(D)은 event_features 에만 의존하고 **이 테이블을 참조하지 않는다** → 범위
    DELETE 후 COPY 로 적재해도 범위 분할·재실행이 안전하다(같은 입력 2회 → 같은 행수).
  - 실행 시각을 출력에 넣지 않는다 → 같은 입력 → 같은 JSON 바이트.

자기신고(3분리, scripts/dq_claim.py record_claim 재사용)
  - source_rows   = event_features 에서 읽은 행수
  - claimed_rows  = 파서(compute_rows)가 만들어낸 출력 행수
  - persisted_rows= 실제 테이블 행수 델타. source>0 인데 claimed==0 이면 **exit 4**.
  - --dry-run 은 DB·claim 을 일절 건드리지 않는다(출력 JSON 에 dry_run=true 로만 남긴다).

사용 (호스트, cwd=리포 루트 — .env 의 POSTGRES_* 또는 기본 127.0.0.1:5434):
    /usr/bin/python3 scripts/build_event_rolling.py --dry-run
    /usr/bin/python3 scripts/build_event_rolling.py
"""
from __future__ import annotations

import argparse
import bisect
import io
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import psycopg2  # noqa: E402
from dq_claim import record_claim  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)

PG = dict(host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
          port=int(os.environ.get("POSTGRES_PORT", "5434")),
          dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
          user=os.environ.get("POSTGRES_USER", "stock_user"),
          password=os.environ.get("POSTGRES_PASSWORD", ""))

SRC_TABLE = "event_features"
DEFAULT_TABLE = "event_rolling_features"
DEFAULT_FROM = "2025-08-01"      # 스펙 기본값(패널 시작 2025-08-04 의 직전 값 보장)
DEFAULT_TO = "2026-11-18"        # 스펙 기본값(원천 최종일)
DEFAULT_JSON = os.path.join(PROJ, "data", "reports", "event_rolling.json")

# 총 이벤트 5d 카운트를 이루는 16개 이벤트 컬럼(disclosure_count_5d 제외 — 위 해석 참조)
EVENT_16 = [
    "event_capital_increase_5d", "event_cb_bw_5d", "event_contract_5d", "event_delisting_5d",
    "event_disaster_5d", "event_exec_change_5d", "event_litigation_5d", "event_mna_5d",
    "event_new_product_5d", "event_partnership_5d", "event_patent_5d", "event_realized_5d",
    "event_recall_5d", "event_regulation_5d", "event_stake_change_5d", "event_treasury_5d",
]
GROUP_COLS = {  # 스펙 §1 산출 4~7 묶음 정의 그대로
    "funding": ["event_capital_increase_5d", "event_cb_bw_5d", "event_mna_5d", "event_treasury_5d"],
    "governance": ["event_stake_change_5d", "event_exec_change_5d"],
    "business": ["event_contract_5d", "event_new_product_5d", "event_partnership_5d",
                 "event_realized_5d"],
    "risk": ["event_litigation_5d", "event_disaster_5d", "event_regulation_5d", "event_recall_5d"],
}
NEW_COLS = [  # 저장 컬럼 순서 = 스펙 §1 산출 순서
    "event_level_20d", "event_trend_5v20", "event_any_60d",
    "funding_level_20d", "governance_level_20d", "business_level_20d",
    "risk_level_20d", "disclosure_level_20d",
]
TABLE_COLS = ["stock_code", "trade_date"] + NEW_COLS
_TABLE_RE = re.compile(r"^[a-z_][a-z0-9_]*$")

DDL = """CREATE TABLE IF NOT EXISTS %(table)s (
    stock_code            VARCHAR(10)      NOT NULL,
    trade_date            DATE             NOT NULL,
    event_level_20d       DOUBLE PRECISION,
    event_trend_5v20      DOUBLE PRECISION,
    event_any_60d         INTEGER,
    funding_level_20d     DOUBLE PRECISION,
    governance_level_20d  DOUBLE PRECISION,
    business_level_20d    DOUBLE PRECISION,
    risk_level_20d        DOUBLE PRECISION,
    disclosure_level_20d  DOUBLE PRECISION,
    computed_at           TIMESTAMPTZ      NOT NULL DEFAULT now(),
    PRIMARY KEY (stock_code, trade_date)
)"""


def compute_rows(src_rows, calendar):
    """종목별 소스 행(비영 행만) → 출력 행 8컬럼. **순수 함수**(DB 없음) — 테스트 대상.

    src_rows: {code: [(date_str, total16, funding, governance, business, risk, disclosure), ...]}
              (날짜 무순 허용 — 내부에서 정렬)
    calendar: 출력 거래일(date_str) 리스트(정렬됨)
    반환: [(code, D, event_level_20d, event_trend_5v20, event_any_60d,
            funding_level_20d, governance_level_20d, business_level_20d,
            risk_level_20d, disclosure_level_20d), ...]
    규칙(스펙 §1): as-of 는 trade_date < D(당일 행 제외, 엄격). 레벨·추세·묶음 레벨은 직전
    20행이 있어야 계산(미만 → None, 0 채움 금지). any_60d 는 직전 행 ≥1 이면 계산(최대 60행).
    직전 행 0개면 그 (code,D) 행 자체를 내보내지 않는다(모든 컬럼이 null 인 행은 무의미).
    """
    out = []
    for code, rows in src_rows.items():
        rows = sorted(rows)
        dates = [r[0] for r in rows]
        tot = [float(r[1]) for r in rows]
        fund = [float(r[2]) for r in rows]
        gov = [float(r[3]) for r in rows]
        biz = [float(r[4]) for r in rows]
        risk = [float(r[5]) for r in rows]
        disc = [float(r[6]) for r in rows]
        for D in calendar:
            i = bisect.bisect_left(dates, D)  # trade_date < D 인 행 수
            if i == 0:
                continue  # 직전 행 없음 → 행 미방출(0 채움 금지)
            if i >= 20:
                level = sum(tot[i - 20:i]) / 20.0
                trend = (sum(tot[i - 5:i]) / 5.0) / (level + 1e-9)
                fl = sum(fund[i - 20:i]) / 20.0
                gl = sum(gov[i - 20:i]) / 20.0
                bl = sum(biz[i - 20:i]) / 20.0
                rl = sum(risk[i - 20:i]) / 20.0
                dl = sum(disc[i - 20:i]) / 20.0
            else:
                level = trend = fl = gl = bl = rl = dl = None  # 창 부족 → null
            any60 = 1 if any(t > 0 for t in tot[max(0, i - 60):i]) else 0
            out.append((code, D, level, trend, any60, fl, gl, bl, rl, dl))
    return out


def replace_range(store, rows, from_d, to_d):
    """범위 DELETE 후 INSERT(멱등 upsert)의 **순수 구현** — DB 경로와 같은 의미론(테스트 ⑤).

    DB 경로는 이 함수와 동일하게 `DELETE ... WHERE trade_date BETWEEN from AND to` 후 COPY 로
    삽입한다. 출력 행(D)은 event_features 에만 의존하므로 범위 재실행·분할 실행이 안전하다.
    같은 입력 2회 → 행수·값 불변.
    """
    for key in [k for k in store if from_d <= k[1] <= to_d]:
        del store[key]
    for row in rows:
        store[(row[0], row[1])] = tuple(row[2:])
    return len(store)


def _read_source(conn, to_d):
    """event_features SELECT(읽기 전용 — 기존 테이블 쓰기 금지). trade_date <= to 만 읽는다.

    쿼리: SELECT stock_code, trade_date::text, 16개 event_*_5d, disclosure_count_5d
          FROM event_features WHERE trade_date <= %(to)s ORDER BY stock_code, trade_date
    반환: (src_rows dict, 읽은 행수)
    """
    cols = ", ".join(EVENT_16)
    sql = ("SELECT stock_code, trade_date::text, %s, disclosure_count_5d "
           "FROM %s WHERE trade_date <= %%s ORDER BY stock_code, trade_date"
           % (cols, SRC_TABLE))
    cur = conn.cursor()
    src = defaultdict(list)
    n = 0
    try:
        cur.execute(sql, (to_d,))
        for code, d, *vals, disc in cur:
            total = float(sum(vals))
            src[code].append((d, total,
                              float(vals[0] + vals[1] + vals[7] + vals[15]),   # funding
                              float(vals[14] + vals[5]),                       # governance
                              float(vals[2] + vals[8] + vals[9] + vals[11]),   # business
                              float(vals[6] + vals[4] + vals[13] + vals[12]),  # risk
                              float(disc)))
            n += 1
    finally:
        cur.close()
    return src, n


def _col_stats(rows):
    """출력 행 위 피처별 nnz_ratio(비영/전체) · null_ratio(창 부족) — 자기신고용."""
    n = len(rows)
    out = {}
    for j, col in enumerate(NEW_COLS):
        nz = sum(1 for r in rows if r[2 + j] is not None and r[2 + j] != 0)
        nn = sum(1 for r in rows if r[2 + j] is None)
        out[col] = {"nnz_ratio": (nz / n) if n else 0.0, "null_ratio": (nn / n) if n else 0.0}
    return out


def _write_json(path, payload):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _db_load(conn, table, from_d, to_d, rows):
    """DDL → 범위 DELETE → COPY(한 커넥션). 반환 (before, after) 행수."""
    cur = conn.cursor()
    try:
        cur.execute(DDL % {"table": table})
        cur.execute("SELECT COUNT(*) FROM %s" % table)
        before = int(cur.fetchone()[0])
        cur.execute("DELETE FROM %s WHERE trade_date BETWEEN %%s AND %%s" % table,
                    (from_d, to_d))
        buf = io.StringIO()
        for row in rows:
            code, D = row[0], row[1]
            buf.write(",".join(
                [code, D] + [("\\N" if v is None else repr(v)) for v in row[2:]]) + "\n")
        buf.seek(0)
        cur.copy_from(buf, table, sep=",", columns=TABLE_COLS)
        conn.commit()
        cur.execute("SELECT COUNT(*) FROM %s" % table)
        after = int(cur.fetchone()[0])
    finally:
        cur.close()
    return before, after


def main(argv=None):
    ap = argparse.ArgumentParser(description="event_features → event_rolling_features "
                                             "(스펙 docs/spec_event_rolling.md §1)")
    ap.add_argument("--from", dest="from_d", default=DEFAULT_FROM)
    ap.add_argument("--to", dest="to_d", default=DEFAULT_TO)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out-table", default=DEFAULT_TABLE)
    ap.add_argument("--json-out", default=DEFAULT_JSON)
    args = ap.parse_args(argv)

    try:
        date.fromisoformat(args.from_d), date.fromisoformat(args.to_d)
    except ValueError:
        print("날짜 형식 오류: --from/--to 는 YYYY-MM-DD", file=sys.stderr)
        return 1
    if args.from_d > args.to_d:
        print("--from > --to", file=sys.stderr)
        return 1
    if not _TABLE_RE.match(args.out_table):
        print("테이블명 거부: %r" % args.out_table, file=sys.stderr)
        return 1

    conn = psycopg2.connect(**PG)
    try:
        src, n_src = _read_source(conn, args.to_d)
    finally:
        conn.close()
    calendar = sorted({d for rows in src.values() for d, *_ in rows
                       if args.from_d <= d <= args.to_d})
    rows = compute_rows(src, calendar)

    if n_src > 0 and len(rows) == 0:
        # dq_claim 계약: 소스는 받았는데 파서가 0행 → 파서 실패 신호(exit 4)
        print("[build_event_rolling] 소스 %d행을 읽었지만 출력 0행 — 파서/구간 실패(exit 4)"
              % n_src, file=sys.stderr)
        return 4

    stats = _col_stats(rows)
    payload = {
        "runner": "build_event_rolling",
        "spec": "docs/spec_event_rolling.md",
        "table": args.out_table,
        "dry_run": bool(args.dry_run),
        "params": {"from": args.from_d, "to": args.to_d},
        "rows": len(rows),
        "codes": len(src),
        "date_range": [args.from_d, args.to_d],
        "nonzero_ratio": stats,
        "defs": {
            "total": {"cols": EVENT_16, "excludes": "disclosure_count_5d (별도 컬럼)"},
            "groups": GROUP_COLS,
            "windows": {
                "level": "직전 20행 평균(trade_date < D 엄격)",
                "trend": "직전 5행 평균 / (직전 20행 평균 + 1e-9)",
                "any_60d": "직전 60행(부족 시 있는 만큼) 중 총합>0 → 1, 직전 0행이면 행 미방출",
                "short_window": "직전 20행 미만 → null(0 채움 금지)",
            },
        },
        "claim": {"runner": "build_event_rolling",
                  "table_name": args.out_table,
                  "source_rows": n_src,
                  "claimed_rows": len(rows),
                  "persisted_rows": None,          # 멱등 재실행이면 0(델타) — table_rows_after 와 함께 읽는다
                  "table_rows_after": None},
    }

    print("[build_event_rolling] 소스 %s행(%s) → 출력 %d행 · %d종목 · %s~%s%s"
          % (n_src, SRC_TABLE, len(rows), len(src), args.from_d, args.to_d,
             " (dry-run)" if args.dry_run else ""))
    for col, s in stats.items():
        print("  %-22s nnz=%7.4f null=%7.4f" % (col, s["nnz_ratio"], s["null_ratio"]))

    if args.dry_run:
        _write_json(args.json_out, payload)
        print("[build_event_rolling] json -> %s (dry_run=true, DB 미접근)" % args.json_out)
        return 0

    conn = psycopg2.connect(**PG)
    try:
        before, after = _db_load(conn, args.out_table, args.from_d, args.to_d, rows)
        persisted = after - before
        record_claim(conn, runner="build_event_rolling", table_name=args.out_table,
                     claimed_rows=len(rows), persisted_rows=persisted,
                     source_rows=n_src,
                     note="from=%s to=%s calendar=%ddays delete_range_upsert"
                          % (args.from_d, args.to_d, len(calendar)))
    finally:
        conn.close()
    payload["claim"]["persisted_rows"] = persisted
    payload["claim"]["table_rows_after"] = after
    _write_json(args.json_out, payload)
    print("[build_event_rolling] 적재 %s: %d -> %d (persisted=%d) | json -> %s"
          % (args.out_table, before, after, persisted, args.json_out))
    return 0


if __name__ == "__main__":
    sys.exit(main())

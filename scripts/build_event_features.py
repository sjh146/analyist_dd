#!/usr/bin/env python3
"""build_event_features — DART 공시(disclosures) → 이벤트 피처 테이블(event_features).

무엇을 하는가
  공시 report_nm 을 16개 이벤트 유형으로 분류하고, 종목×거래일마다 **직전 5거래일 창**의
  이벤트 건수를 만든다. 죽어 있던 event_*_5d 18개 + disclosure_count_5d 를 부활시키는 것이 목적
  (docs/DEAD_FEATURE_REVIVAL.md #1).

as-of 규율 (중요)
  창은 **D-1 에서 끝난다**: feat[D] = events in [D-5 .. D-1]. 당일(D) 공시는 쓰지 않는다 —
  장 마감 후 접수된 공시가 그날 종가 기준 피처에 섞이는 누수를 구조적으로 차단한다.
  비거래일(주말·휴일) 접수분은 **다음 거래일**의 이벤트로 귀속한다.

설계 메모
  - pandas 없이 동작한다(호스트 /usr/bin/python3 기준, psycopg2 만 필요).
  - 종목별 이벤트를 캘린더 인덱스로 모아 bisect 로 창 합계를 구한다(메모리 O(이벤트수)).
  - 멱등: 시작 전 대상 구간을 DELETE 후 COPY 한다(재실행 안전).

사용
  cd /home/jhshi/analyist_dd && set -a && . ./.env && set +a
  export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434 PROJ_DIR=/home/jhshi/analyist_dd
  /usr/bin/python3 scripts/build_event_features.py --since 2024-06-01
  /usr/bin/python3 scripts/build_event_features.py --since 2026-08-01 --dry-run
"""

import argparse
import bisect
import io
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import psycopg2  # noqa: E402

PG = dict(host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
          port=int(os.environ.get("POSTGRES_PORT", "5434")),
          dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
          user=os.environ.get("POSTGRES_USER", "stock_user"),
          password=os.environ.get("POSTGRES_PASSWORD", ""))

# 이벤트 유형 → report_nm 키워드. 여러 패턴에 걸리면 **모두** 센다(멀티라벨, 문서화됨).
EVENT_PATTERNS = [
    ("capital_increase", r"유상증자|제3자배정|주주배정"),
    ("cb_bw", r"전환사채|신주인수권부사채|교환사채"),
    ("contract", r"공급계약|수주|납품계약|용역계약|단일판매"),
    ("delisting", r"상장폐지|관리종목|거래정지|상장적격성|감사의견"),
    ("disaster", r"재해|화재|붕괴|조업중단|생산중단"),
    ("exec_change", r"대표이사|임원|등기이사|사외이사|대표자"),
    ("litigation", r"소송|가처분|법적분쟁|판결"),
    ("mna", r"합병|회사분할|영업양수|영업양도|최대주주\s*변경|주식교환"),
    ("new_product", r"신제품|신규\s*출시|개발\s*완료|상용화"),
    ("partnership", r"업무협약|MOU|제휴|공동\s*개발"),
    ("patent", r"특허|상표|실용신안|디자인권"),
    ("realized", r"영업이익|매출액|손익구조|잠정|실적"),
    ("recall", r"리콜|회수|결함|판매중지"),
    ("regulation", r"규제|제재|과징금|시정명령|영업정지"),
    ("stake_change", r"대량보유|지분|주식등의|보유비율"),
    ("treasury", r"자기주식|자사주"),
]
COMPILED = [(name, re.compile(pat)) for name, pat in EVENT_PATTERNS]
# 정기공시(사업/반기/분기/기한연장)는 이벤트가 아니다 — disclosure_count_5d 에서 제외한다.
PERIODIC = re.compile(r"사업보고서|반기보고서|분기보고서|제출기한연장")
COLS = [f"event_{n}_5d" for n, _ in EVENT_PATTERNS] + ["disclosure_count_5d"]
WINDOW = 5          # 직전 5거래일


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def classify(name: str):
    """report_nm → 매칭되는 이벤트 유형 인덱스 목록. 정기공시면 빈 목록."""
    idx = [i for i, (_, rx) in enumerate(COMPILED) if rx.search(name)]
    return idx


def main():
    ap = argparse.ArgumentParser(description="DART 공시 → 이벤트 피처")
    ap.add_argument("--since", default="2024-06-01", help="피처 시작일(거래일 캘린더 기준)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit-stocks", type=int, default=0, help="디버그용 종목 수 제한")
    a = ap.parse_args()
    t0 = time.time()

    conn = psycopg2.connect(**PG)
    cur = conn.cursor()

    # ── 거래일 캘린더 ─────────────────────────────────────────────────────
    cur.execute("SELECT DISTINCT trade_date FROM market_data WHERE trade_date >= %s "
                "ORDER BY trade_date", (a.since,))
    cal = [r[0] for r in cur.fetchall()]
    if not cal:
        log("거래일 캘린더가 비었다 — market_data 확인")
        return 1
    pos = {d: i for i, d in enumerate(cal)}
    log(f"거래일 {len(cal)}일 ({cal[0]} ~ {cal[-1]})")

    # ── 공시 로드 + 분류 ─────────────────────────────────────────────────
    cur.execute("""SELECT stock_code, rcept_dt, report_nm FROM disclosures
                   WHERE rcept_dt >= %s AND stock_code IS NOT NULL""", (a.since,))
    rows = cur.fetchall()
    log(f"공시 {len(rows)}건 로드")

    # 종목 → (유형 인덱스 → 캘린더 위치 정렬 리스트) / (전체 이벤트 위치 리스트)
    ev = defaultdict(lambda: defaultdict(list))
    cnt = defaultdict(list)
    unmatched = matched = 0
    for code, rcept, nm in rows:
        nm = nm or ""
        idxs = classify(nm)
        # 비거래일(주말·휴일) 접수분 → 다음 거래일로 귀속
        p = pos.get(rcept)
        if p is None:
            for j in range(bisect.bisect_left(cal, rcept), len(cal)):
                p = j
                break
            if p is None:
                continue
        if PERIODIC.search(nm):
            continue          # 정기공시는 이벤트도 카운트도 아니다
        cnt[code].append(p)
        if idxs:
            matched += 1
            for i in idxs:
                ev[code][i].append(p)
        else:
            unmatched += 1
    log(f"분류: 이벤트 {matched}건 / 유형 미매칭(비정기) {unmatched}건 / 종목 {len(cnt)}개")

    # ── (종목 × 거래일) 격자에서 창 합계 ─────────────────────────────────
    q = """SELECT m.stock_code, m.trade_date FROM market_data m
           WHERE m.trade_date >= %s GROUP BY 1, 2 ORDER BY 1, 2"""
    if a.limit_stocks:
        q = q.replace("GROUP BY 1, 2", "AND m.stock_code IN (SELECT stock_code FROM market_data "
                                      f"GROUP BY 1 LIMIT {int(a.limit_stocks)}) GROUP BY 1, 2")
    cur.execute(q, (a.since,))
    grid = cur.fetchall()
    log(f"격자 {len(grid)}행 (종목×거래일)")

    out = io.StringIO()
    written = 0
    ncols = len(COLS)
    for code, d in grid:
        i = pos.get(d)
        if i is None or i == 0:
            continue
        lo, hi = max(0, i - WINDOW), i            # [D-5 .. D-1]  (당일 제외 = 누수 차단)
        vals = []
        for k in range(len(EVENT_PATTERNS)):
            lst = ev[code].get(k)
            vals.append((bisect.bisect_left(lst, hi) - bisect.bisect_left(lst, lo)) if lst else 0)
        cl = cnt.get(code)
        tot = (bisect.bisect_left(cl, hi) - bisect.bisect_left(cl, lo)) if cl else 0
        if not any(vals) and tot == 0:
            continue                       # 전부 0 인 행은 저장하지 않는다(공간 절약)
        out.write(f"{code}\t{d}\t" + "\t".join(str(v) for v in vals) + f"\t{tot}\n")
        written += 1
    log(f"생성 {written}행 (전부 0 인 행 제외)")

    if a.dry_run:
        log("dry-run: DB 쓰기 생략")
        cur.close(); conn.close()
        return 0

    # ── 멱등 적재: 구간 DELETE 후 COPY ───────────────────────────────────
    cur.execute("DELETE FROM event_features WHERE trade_date >= %s", (a.since,))
    deleted = cur.rowcount
    out.seek(0)
    cur.copy_expert(
        f"COPY event_features (stock_code, trade_date, {', '.join(COLS)}) FROM STDIN", out)
    conn.commit()
    cur.execute("SELECT COUNT(*), COUNT(*) FILTER (WHERE disclosure_count_5d > 0), "
                "COUNT(*) FILTER (WHERE event_contract_5d > 0) FROM event_features")
    total, with_disc, with_contract = cur.fetchone()
    log(f"적재 완료: 삭제 {deleted}행 → 삽입 {total}행 "
        f"(이벤트 보유 {with_disc}행 / 공급계약 보유 {with_contract}행)")

    # 자기신고 (프로젝트 규약: 소스/파서/저장 3분리)
    try:
        from dq_claim import record_claim
        record_claim(conn, "build_event_features", "event_features",
                     claimed_rows=written, persisted_rows=total, source_rows=len(rows),
                     note=f"disclosures={len(rows)} matched={matched} grid={len(grid)} "
                          f"since={a.since} window=D-5..D-1")
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        log(f"자기신고 생략: {exc}")

    log(f"완료 ({time.time() - t0:.1f}s)")
    cur.close(); conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

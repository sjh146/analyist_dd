#!/usr/bin/env python3
"""dart_disclosure_backfill — DART 공시 인덱스(disclosures) 백필.

무엇을 하는가
  DART `list.json` 을 **월 단위 창**으로 훑어 정기공시(사업/반기/분기보고서)의 접수일(rcept_dt)을
  `disclosures` 에 적재한다. 이 테이블이 있으면 재무 피처의 공시 지연을 **가정(90/45일) 대신
  실제 접수일**로 계산할 수 있고, 연간/반기 혼재로 깨진 성장률 피처도 기간을 구분할 수 있다.

설계 (기존 인프라 재사용)
  - FinancialCollector._request 를 그대로 쓴다(crtfc_key 주입·재시도·JSON 파싱을 이미 갖고 있다).
  - Rate limit: 1.5초 + 0.3~0.8초 지터(dart_financial_backfill.py 의 실측 교훈과 동일 정책).
  - 월 단위 창 + 페이지 루프 → page_no 가 커지지 않아 안전하다.
  - **자기신고 필수**: dq_claim.record_claim 으로 source_rows/claimed/persisted 를 남긴다.
    "몇 건 받아서 몇 건 저장했는가"가 없으면 조용한 실패를 감지할 수 없다(2026-09-24 교훈).

사용 (호스트 /usr/bin/python3 — 기본 python3 는 psycopg2 없는 venv)
  cd /home/jhshi/analyist_dd && set -a && . ./.env && set +a
  export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434 PROJ_DIR=/home/jhshi/analyist_dd
  /usr/bin/python3 scripts/dart_disclosure_backfill.py --since 2025-01-01 --max-calls 400
"""

import argparse
import os
import sys
import time
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import psycopg2  # noqa: E402

PG = dict(host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
          port=int(os.environ.get("POSTGRES_PORT", "5434")),
          dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
          user=os.environ.get("POSTGRES_USER", "stock_user"),
          password=os.environ.get("POSTGRES_PASSWORD", ""))

# 정기공시 코드: A = 사업/반기/분기보고서 (재무제표의 원천)
PBLNTF_TY = "A"
PAGE_COUNT = 100          # DART 최대
DELAY_BASE = 1.5          # 초
JITTER = (0.3, 0.8)


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def months_between(start: date, end: date):
    """월 단위 (bgn_de, end_de) 창 목록."""
    out, cur = [], date(start.year, start.month, 1)
    while cur <= end:
        last = (date(cur.year + (cur.month == 12), (cur.month % 12) + 1, 1) - timedelta(days=1))
        out.append((max(cur, start), min(last, end)))
        cur = last + timedelta(days=1)
    return out


def main():
    ap = argparse.ArgumentParser(description="DART 공시 인덱스 백필")
    ap.add_argument("--since", default="2025-01-01")
    ap.add_argument("--until", default=None)
    ap.add_argument("--max-calls", type=int, default=400)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    key = os.environ.get("DART_API_KEY", "").strip()
    if not key:
        log("DART_API_KEY 없음 — 중단(키 값은 출력하지 않는다)")
        return 2

    # 기존 수집기 재사용 (crtfc_key 주입·재시도·파싱 포함)
    sys.path.insert(0, "/home/jhshi/analyist_dd/services/yfinance-collector")
    try:
        from app.collectors.financial_collector import FinancialCollector  # type: ignore
    except Exception as exc:  # noqa: BLE001
        log(f"수집기 import 실패({exc}) → 직접 호출로 폴백")
        FinancialCollector = None

    since = datetime.strptime(a.since, "%Y-%m-%d").date()
    until = datetime.strptime(a.until, "%Y-%m-%d").date() if a.until else date.today()
    windows = months_between(since, until)
    log(f"기간 {since} ~ {until} → {len(windows)}개 월 창, 예산 {a.max_calls}콜")

    conn = psycopg2.connect(**PG)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM disclosures")
    rows_before = int(cur.fetchone()[0])

    fc = FinancialCollector(api_key=key) if FinancialCollector else None
    calls = src_rows = kept = inserted = 0
    hit_limit = False

    def fetch(params):
        if fc is not None:
            return fc._request("list.json", dict(params))   # noqa: SLF001 - 의도적 재사용
        import requests
        p = dict(params)
        p["crtfc_key"] = key
        r = requests.get("https://opendart.fss.or.kr/api/list.json", params=p, timeout=20)
        return r.json()

    for (d0, d1) in windows:
        page = 1
        prev_first = None
        while True:
            if calls >= a.max_calls:
                log(f"예산 소진({calls}콜) — 다음 실행에서 이어서")
                hit_limit = True
                break
            try:
                resp = fetch({"bgn_de": d0.strftime("%Y%m%d"), "end_de": d1.strftime("%Y%m%d"),
                              "pblntf_ty": PBLNTF_TY, "page_no": page, "page_count": PAGE_COUNT})
            except Exception as exc:  # noqa: BLE001
                log(f"{d0} p{page} 요청 실패: {exc}")
                break
            calls += 1
            time.sleep(DELAY_BASE + __import__("random").uniform(*JITTER))

            status = str(resp.get("status", ""))
            items = resp.get("list") or []
            if status == "013":        # 조회된 데이터 없음 — 정상(빈 창)
                break
            if status != "000":
                log(f"{d0} p{page} DART 오류 status={status} msg={resp.get('message')}")
                break

            batch, skipped = [], 0
            for it in items:
                code = (it.get("stock_code") or "").strip()
                if not code:            # 비상장 법인 → 우리 관심 밖
                    skipped += 1
                    continue
                batch.append((code, it.get("rcept_dt"), it.get("rcept_no"),
                              (it.get("report_nm") or "")[:200], it.get("corp_code")))
            src_rows += len(items)
            kept += len(batch)

            if batch and not a.dry_run:
                cur.executemany(
                    """INSERT INTO disclosures (stock_code, rcept_dt, rcept_no, report_nm, corp_code)
                       VALUES (%s, to_date(%s,'YYYYMMDD'), %s, %s, %s)
                       ON CONFLICT (stock_code, rcept_no) DO NOTHING""",
                    batch)
                conn.commit()
                # ⚠ 멱등 upsert 에서는 "시도 행수"가 아니라 **실제 삽입 행수**를 자기신고해야 한다.
                # 시도(kept)를 claimed 로 보고하면 재실행 시 차이가 갭으로 잡혀 오탐이 된다
                # (실측 2026-09-25: claimed 22,278 / persisted 15,895 → gap 6,383 오탐).
                inserted += max(cur.rowcount, 0)

            log(f"  {d0:%Y-%m} p{page}: {len(items)}건 (적재대상 {len(batch)}, 비상장 {skipped})")

            # ── 종료조건 ① total_count 기반 ──────────────────────────────────
            # 실측(2025-09-25): DART list.json 은 **total_count 를 넘는 page_no 에 대해 마지막 페이지를
            # 반복 반환**한다. 2025-05 는 total=3300(33페이지)인데 p50·p100·p200·p500 이 전부 같은
            # 응답이었다. 이 검사가 없으면 예산을 태우며 같은 페이지를 영원히 돈다
            # (실측 피해: 214페이지를 돌고도 신규 945행뿐 = 95% 중복).
            total = int(resp.get("total_count") or 0)
            if total and page * PAGE_COUNT >= total:
                break
            # ── 종료조건 ② 반복 감지(이중 안전장치) ──────────────────────────
            first_no = items[0].get("rcept_no") if items else None
            if first_no and first_no == prev_first:
                log(f"  {d0:%Y-%m} p{page}: 직전 페이지와 동일 응답 → 중단(API 상한 추정)")
                break
            prev_first = first_no

            if len(items) < PAGE_COUNT:
                break
            page += 1
        if hit_limit:
            break

    cur.execute("SELECT COUNT(*) FROM disclosures")
    after = int(cur.fetchone()[0])
    delta = after - rows_before

    # 자기신고 (소스 수신 vs 저장 vs 실제 델타)
    try:
        from dq_claim import record_claim
    except ImportError:
        record_claim = None
    if record_claim and not a.dry_run:
        try:
            # claimed_rows = **파서가 만들어낸 행수(kept)** 이지 실제 삽입 행수가 아니다.
            # 이 구분이 중요하다: parse_failure 규칙은 `claimed == 0 AND source > 0`(= API 는 줬는데
            # 파서/필터가 아무것도 못 만듦)을 잡는다. claimed 를 삽입 행수로 바꾸면 **이미 적재된
            # 창을 재실행할 때 inserted=0** 이 되어 정상 재실행이 파서 실패로 오탐된다(실측 2026-09-25).
            # 반대로 gap(claimed - persisted)은 멱등 upsert 에서 중복 재수신만큼 정상적으로 존재하므로
            # 임계값을 관대하게 둔다(dq_snapshot SPECS: warn 500 / breach 5000).
            record_claim(conn, "dart_disclosure_backfill", "disclosures",
                         claimed_rows=kept, persisted_rows=delta, source_rows=src_rows,
                         note=(f"windows={len(windows)} calls={calls} since={since} "
                               f"파서생성={kept}(비상장 제외) 삽입={inserted} 실델타={delta}"))
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            log(f"자기신고 실패: {exc}")

    log(f"완료: 콜 {calls} / 소스 {src_rows}행 / 적재시도 {kept} / 실제 삽입 {inserted} / "
        f"테이블 델타 {delta} (테이블 {rows_before}→{after})")
    if src_rows > 0 and kept == 0:
        log("★ 소스는 행을 줬는데 적재 대상이 0 — 파서/필터 점검 필요")
    if inserted != delta:
        log(f"! 삽입({inserted})과 델타({delta})가 다르다 — 같은 창을 동시에 도는 프로세스가 있는지 확인")
    if hit_limit:
        log("예산으로 중단 — 다음 실행에서 이어서(멱등 upsert)")
    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

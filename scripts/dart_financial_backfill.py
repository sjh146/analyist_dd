#!/usr/bin/env python3
"""
DART 재무 이력 백필 — 안전 우선 설계 (KRX 7일 차단 교훈 반영)

전략: 연간 사업보고서(11011) 1회 호출로 3개 기간(당기/전기/전전기) 추출 → 종목당 호출 최소화.
안전장치 (순서대로):
  1. Rate limit: 최소 1.5s 간격 + 0.3~0.8s 랜덤 지터 (실효 ~2s → DART 1 QPS 한도의 절반 이하)
  2. 일일 예산: MAX_CALLS (기본 2500) 초과 시 즉시 중단 + 내일 재개 (checkpoint)
  3. 오류 백오프: 429/5xx/네트워크 오류 → 5s→15s→45s→90s 지수 백오프 (최대 4회 재시도)
  4. 서킷 브레이커: 연속 10회 실패 → 15분 정지 후 재개
  5. 체크포인트: 진행 상태를 JSON으로 저장 → 재시작 시 이어서
  6. 스코프: 이미 3개년 이상 보유한 종목 스킵, --dry-run 모드 지원
  7. 응답 검증: status != '000' 또는 항목 0건 → 실패로 집계 (차단 회피 로그)

사용법:
  python3 scripts/dart_financial_backfill.py [--limit N] [--dry-run] [--year 2025] [--max-calls N]
  # 2026 반기(최신 공시) 확대 — 당기(thstrm)만 저장 + 재무상태표 전기말(2025-12-31) 동시 기록
  python3 scripts/dart_financial_backfill.py --reprt-code 11012 --year 2026 \
      --state-file data/dart/backfill_state_11012.json --max-calls 3000
"""
import argparse
import json
import logging
import os
import random
import sys
import time
from datetime import datetime, date

BASE_DIR = "/home/dduckbeagy/analyist_dd"
sys.path.insert(0, os.path.join(BASE_DIR, "services/yfinance-collector"))
sys.path.insert(0, os.path.join(BASE_DIR, "services/yfinance-collector/app"))

import psycopg2  # noqa: E402
from collectors.financial_collector import FinancialCollector  # noqa: E402

# ── 설정 ──────────────────────────────────────────────────────────────
MIN_INTERVAL = 1.5          # 최소 호출 간격 (초)
JITTER_RANGE = (0.3, 0.8)   # 랜덤 지터 범위
MAX_CALLS_DEFAULT = 2500    # 일일 예산 (DART 일일 한도 10,000의 25%)
BACKOFF_STEPS = [5, 15, 45, 90]   # 지수 백오프 (초)
CIRCUIT_BREAK_THRESHOLD = 10      # 연속 실패 시 서킷 브레이커
CIRCUIT_BREAK_PAUSE = 900         # 15분 정지
MIN_ANNUAL_ROWS = 3               # 이 조건 충족 시 스킵 (3개년)
CHECKPOINT = os.path.join(BASE_DIR, "data/dart/backfill_state.json")
LOG_DIR = os.path.join(BASE_DIR, "data/dart")

# DART 계정과목 — 정규화(공백·괄호내용 제거) 후 매칭, 손익계산서는 IS/CIS 모두 허용
# (실측: 삼성전자=IS, SK하이닉스=CIS, '영업활동 현금흐름' 공백, '(손실)' 접미사 등 변형)
import re as _re


def _norm(nm):
    return _re.sub(r"[\(\[].*?[\)\]]", "", nm).replace(" ", "")


ACCOUNT_MAP = [
    ("매출액", ("IS", "CIS"), "revenue"),
    # 실측 확장 (2026-09-24): 매출액 미매칭 사유 = 업종별 표기 차이
    #   NAVER='영업수익'(CIS), 한국전력='수익(매출액)'(정규화 시 '수익'), 일부='총매출액'/'매출'
    ("영업수익", ("IS", "CIS"), "revenue"),
    ("수익", ("IS", "CIS"), "revenue"),
    ("총매출액", ("IS", "CIS"), "revenue"),
    ("매출", ("IS", "CIS"), "revenue"),
    ("영업이익", ("IS", "CIS"), "operating_profit"),
    ("당기순이익", ("IS", "CIS"), "net_income"),
    # 분기/반기 보고서는 손익 최종 라인 명칭이 다르다 (실측: 11012 = '반기순이익',
    # 11013/11014 = '분기순이익'). 정규화로는 잡히지 않아 별도 등록.
    ("반기순이익", ("IS", "CIS"), "net_income"),
    ("분기순이익", ("IS", "CIS"), "net_income"),
    ("자산총계", ("BS",), "total_assets"),
    ("자본총계", ("BS",), "total_equity"),
    ("부채총계", ("BS",), "total_debt"),
    ("영업활동현금흐름", ("CF",), "operating_cash_flow"),
    ("영업으로부터창출된현금흐름", ("CF",), "operating_cash_flow"),  # 대체 명칭
    ("매출원가", ("IS", "CIS"), "_cost_of_sales"),
    ("매출총이익", ("IS", "CIS"), "_gross_profit"),
]
ACCOUNT_MAP_NORM = [(_norm(n), divs, col) for n, divs, col in ACCOUNT_MAP]

# 재무상태표(BS) 항목 — 분기 보고서에서 전기말(frmtrm_amount) 값이 직전 회계연도말이다.
BS_COLS = {"total_assets", "total_equity", "total_debt"}

# 보고서 코드 → 결산 기준월-일 (report_date 접미사)
REPORT_SUFFIX = {"11011": "12-31", "11012": "06-30", "11013": "03-31", "11014": "09-30"}

# ── 로깅 ──────────────────────────────────────────────────────────────
os.makedirs(LOG_DIR, exist_ok=True)
log_path = os.path.join(LOG_DIR, f"backfill_{datetime.now().strftime('%Y%m%d')}.log")
logging.basicConfig(
    filename=log_path, level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
console = logging.StreamHandler()
console.setLevel(logging.INFO)
logging.getLogger().addHandler(console)
log = logging.getLogger("dart-backfill")


def load_env():
    env = {}
    with open(os.path.join(BASE_DIR, ".env")) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k] = v
    return env


def load_mapping():
    with open(os.path.join(BASE_DIR, "data/dart/corp_mapping.json")) as f:
        return json.load(f)


def load_checkpoint(path=None):
    path = path or CHECKPOINT
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"done": [], "failed": [], "last_run": None}


def save_checkpoint(state, path=None):
    path = path or CHECKPOINT
    state["last_run"] = datetime.now().isoformat()
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, path)


def db_conn(env):
    # 호스트 실행: docker 내부 호스트명(postgres)은 호스트에서 안 풀림 → localhost:5434 폴백
    candidates = [
        (env.get("POSTGRES_HOST", "localhost"), int(env.get("POSTGRES_PORT", 5434))),
        ("localhost", 5434),
    ]
    last_err = None
    for host, port in candidates:
        try:
            return psycopg2.connect(
                host=host, port=port,
                dbname=env.get("POSTGRES_DB", "postgres"),
                user=env.get("POSTGRES_USER", "postgres"),
                password=env.get("POSTGRES_PASSWORD", ""),
                connect_timeout=5,
            )
        except psycopg2.OperationalError as e:
            last_err = e
            continue
    raise last_err


def get_existing_periods(conn, code, suffix):
    """이미 보유한 해당 결산기(예: 12-31 = 연간) 행 수."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM financial_statements "
            "WHERE stock_code=%s AND to_char(report_date,'MM-DD')=%s",
            (code, suffix),
        )
        return cur.fetchone()[0]


def extract_periods(items, year, suffix="12-31", quarterly=False):
    """fnlttSinglAcntAll 응답 → {report_date(str): {col: value}}

    annual(11011): 3개 기간(당기/전기/전전기) 모두 → y, y-1, y-2 의 12-31
    quarterly(11012/11013/11014): 당기(thstrm)만 + 재무상태표 항목의 전기말(frmtrm)을
    (year-1)-12-31 로 기록 (실측: 11012 응답의 BS frmtrm_amount = 직전 회계연도말 값,
    IS/CF frmtrm_amount 는 None 이라 잘못된 분기 날짜가 생기지 않는다).
    """
    periods = {f"{year}-{suffix}": {}}
    if not quarterly:
        for y in (year - 1, year - 2):
            periods[f"{y}-12-31"] = {}
    prev_yearend = f"{year - 1}-12-31"

    def _f(item, key):
        raw = item.get(key) or "0"
        try:
            return float(str(raw).replace(",", ""))
        except ValueError:
            return None

    for item in items:
        nm = _norm(item.get("account_nm", ""))
        sj = item.get("sj_div", "")
        col = next((c for n, divs, c in ACCOUNT_MAP_NORM if n == nm and sj in divs), None)
        if col is None:
            continue
        if quarterly:
            v = _f(item, "thstrm_amount")
            if v is not None:
                periods[f"{year}-{suffix}"][col] = v
            if col in BS_COLS:
                v = _f(item, "frmtrm_amount")
                if v is not None:
                    periods.setdefault(prev_yearend, {})[col] = v
        else:
            for py, key in [(year, "thstrm_amount"), (year - 1, "frmtrm_amount"),
                            (year - 2, "bfefrmtrm_amount")]:
                v = _f(item, key)
                if v is not None:
                    periods[f"{py}-12-31"][col] = v
    return {d: v for d, v in periods.items() if v}


def build_rows(stock_code, periods):
    """{report_date: {col: value}} → financial_statements 행 리스트 (gross_profit/debt_ratio 계산)."""
    rows = []
    for rd in sorted(periods.keys()):
        vals = periods[rd]
        if not vals:
            continue  # 해당 기간 데이터 없음
        row = {"stock_code": stock_code, "report_date": rd}
        for col in ("revenue", "operating_profit", "net_income", "total_assets",
                    "total_equity", "total_debt", "operating_cash_flow"):
            row[col] = vals.get(col)
        rev, cost = vals.get("revenue"), vals.get("_cost_of_sales")
        gross = (rev - cost) if (rev is not None and cost is not None) else vals.get("_gross_profit")
        if gross is not None:
            row["gross_profit"] = gross
        debt, equity = vals.get("total_debt"), vals.get("total_equity")
        if debt is not None and equity and equity > 0:
            row["debt_ratio"] = round(debt / equity * 100.0, 2)  # 스크리너 debt_score용
        rows.append(row)
    return rows


def save_rows(conn, rows):
    """ON CONFLICT (stock_code, report_date) DO UPDATE — 기존 저장 관례."""
    if not rows:
        return
    sql = """
        INSERT INTO financial_statements
            (stock_code, report_date, revenue, operating_profit, net_income,
             total_assets, total_equity, total_debt, operating_cash_flow,
             gross_profit, debt_ratio)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (stock_code, report_date) DO UPDATE SET
            revenue = COALESCE(EXCLUDED.revenue, financial_statements.revenue),
            operating_profit = COALESCE(EXCLUDED.operating_profit, financial_statements.operating_profit),
            net_income = COALESCE(EXCLUDED.net_income, financial_statements.net_income),
            total_assets = COALESCE(EXCLUDED.total_assets, financial_statements.total_assets),
            total_equity = COALESCE(EXCLUDED.total_equity, financial_statements.total_equity),
            total_debt = COALESCE(EXCLUDED.total_debt, financial_statements.total_debt),
            operating_cash_flow = COALESCE(EXCLUDED.operating_cash_flow, financial_statements.operating_cash_flow),
            gross_profit = COALESCE(EXCLUDED.gross_profit, financial_statements.gross_profit),
            debt_ratio = COALESCE(EXCLUDED.debt_ratio, financial_statements.debt_ratio)
    """
    with conn.cursor() as cur:
        cur.executemany(sql, [
            (r["stock_code"], r["report_date"], r.get("revenue"), r.get("operating_profit"),
             r.get("net_income"), r.get("total_assets"), r.get("total_equity"),
             r.get("total_debt"), r.get("operating_cash_flow"), r.get("gross_profit"),
             r.get("debt_ratio"))
            for r in rows
        ])
    conn.commit()


def fetch_report(fc, code, corp_code, year, reprt_code):
    """CFS(연결) 우선 → 연결재무제표가 없으면(status 013) OFS(별도)로 폴백.

    실측: 연결 대상이 아닌 소형/외국계 종목은 CFS=013 → OFS 로만 데이터가 온다.
    returns (data, calls)
    """
    used, last = 0, None
    for fs_div in ("CFS", "OFS"):
        data = fc._request("fnlttSinglAcntAll.json", {
            "corp_code": corp_code,
            "bsns_year": str(year),
            "reprt_code": reprt_code,
            "fs_div": fs_div,
        })
        used += 1
        last = data
        if data and data.get("status") == "000" and data.get("list"):
            if fs_div == "OFS":
                log.info("%s: CFS 없음 → OFS(별도) 사용", code)
            return data, used
        if not data or data.get("status") != "013":
            break  # 013(데이터 없음)이 아니면 OFS 재시도는 무의미
    return last, used


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="처리할 종목 수 제한 (0=전체)")
    ap.add_argument("--dry-run", action="store_true", help="실제 호출 없이 범위/점검만")
    ap.add_argument("--year", type=int, default=2025, help="기준 연도 (보고서 bsns_year)")
    ap.add_argument("--max-calls", type=int, default=MAX_CALLS_DEFAULT, help="일일 예산")
    ap.add_argument("--codes", default="", help="특정 종목만 처리 (콤마 구분, 테스트용)")
    ap.add_argument("--reprt-code", default="11011", choices=sorted(REPORT_SUFFIX),
                    help="11011 사업보고서(연간) / 11012 반기 / 11013 1분기 / 11014 3분기")
    ap.add_argument("--state-file", default="", help="체크포인트 경로 (기본 data/dart/backfill_state.json)")
    ap.add_argument("--force", action="store_true",
                    help="완료/실패 목록과 기존 보유 스킵을 무시하고 재조회 (매출액 누락 보정 등)")
    ap.add_argument("--skip-after", type=int, default=0,
                    help="이 보유 행 수 이상이면 스킵 (0=연간 3 / 분기 1)")
    args = ap.parse_args()

    suffix = REPORT_SUFFIX[args.reprt_code]
    quarterly = args.reprt_code != "11011"
    state_path = args.state_file or CHECKPOINT
    skip_threshold = args.skip_after or (1 if quarterly else MIN_ANNUAL_ROWS)

    env = load_env()
    key = env.get("DART_API_KEY", "")
    if not key or key.startswith("your_"):
        log.error("DART_API_KEY 없음 — 중단")
        sys.exit(1)

    mapping = load_mapping()
    state = load_checkpoint(state_path)
    fc = FinancialCollector(api_key=key)

    conn = db_conn(env)

    # 스코프: corp_mapping + 시세 유니버스 (market_data 우선, 미수집이면 stocks)
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT stock_code FROM market_data")
        active = {r[0] for r in cur.fetchall()}
        source = "market_data"
        if not active:
            # 신규 배포/백필 전에는 market_data 가 비어 있다 → 종목 마스터로 대체
            cur.execute("SELECT stock_code FROM stocks")
            active = {r[0] for r in cur.fetchall()}
            source = "stocks(market_data 미수집)"
    done, failed = set(), set()
    if not args.force:
        done, failed = set(state["done"]), set(state["failed"])
    scope = [c for c in mapping if c in active and c not in done and c not in failed]
    if args.codes:
        scope = [c.strip() for c in args.codes.split(",") if c.strip() in mapping]
        log.info("--codes 지정: %s", scope)
    log.info(f"스코프: 매핑 {len(mapping)} / 유니버스 {len(active)} ({source}) / 대상 {len(scope)} "
             f"(완료 {len(done)}, 실패 {len(failed)}) / reprt={args.reprt_code}({suffix}) year={args.year} "
             f"force={args.force} state={state_path}")

    if args.dry_run:
        log.info("[dry-run] 실행 안 함 — 대상 종목 수: %d, 예상 호출: ~%d, 예상 소요: ~%d분",
                 len(scope), len(scope), int(len(scope) * 2.2 / 60))
        conn.close()
        return

    if args.limit:
        scope = scope[: args.limit]

    calls = 0
    consec_fail = 0
    ok, fail = 0, 0
    last_request = 0.0

    for code in scope:
        # 일일 예산 체크
        if calls >= args.max_calls:
            log.warning("일일 예산 도달 (%d) — 중단. 내일 재개 (checkpoint).", calls)
            break

        # 서킷 브레이커
        if consec_fail >= CIRCUIT_BREAK_THRESHOLD:
            log.warning("연속 실패 %d회 — %d초 정지", consec_fail, CIRCUIT_BREAK_PAUSE)
            time.sleep(CIRCUIT_BREAK_PAUSE)
            consec_fail = 0

        # 이미 해당 결산기 보유분이 충분한 종목 스킵 (--force 는 무시)
        try:
            if not args.force and get_existing_periods(conn, code, suffix) >= skip_threshold:
                state["done"].append(code)
                continue
        except Exception as e:
            log.debug("보유 행 확인 실패 %s: %s", code, e)

        corp_code = mapping[code]["corp_code"]

        # Rate limit: 간격 + 지터
        wait = max(0.0, last_request + MIN_INTERVAL + random.uniform(*JITTER_RANGE) - time.time())
        if wait > 0:
            time.sleep(wait)

        # 호출 + 재시도 (지수 백오프) — CFS 실패 시 OFS 폴백
        data = None
        for attempt, backoff in enumerate([0] + BACKOFF_STEPS):
            try:
                data, used = fetch_report(fc, code, corp_code, args.year, args.reprt_code)
                calls += used
                last_request = time.time()
                if data and data.get("status") == "000" and data.get("list"):
                    break
                # status != 000 — 재시도 없이 실패 처리 (API 응답 오류)
                log.warning("%s: status=%s", code, data.get("status") if data else None)
                data = None
                break
            except Exception as e:
                log.warning("%s: 요청 예외(%d차): %s — %d초 후 재시도", code, attempt + 1, e, backoff)
                if backoff:
                    time.sleep(backoff)
                calls += 1
                last_request = time.time()

        if not data or data.get("status") != "000" or not data.get("list"):
            consec_fail += 1
            fail += 1
            state["failed"].append(code)
            log.warning("%s: 데이터 없음 (연속실패 %d)", code, consec_fail)
        else:
            consec_fail = 0
            periods = extract_periods(data["list"], args.year, suffix=suffix, quarterly=quarterly)
            rows = build_rows(code, periods)
            if rows:
                try:
                    save_rows(conn, rows)
                    ok += 1
                    log.info("%s: %d개년 저장 (%s)", code, len(rows),
                             ", ".join(r["report_date"] for r in rows))
                except Exception as e:
                    log.error("%s: 저장 실패: %s", code, e)
                    fail += 1
                    state["failed"].append(code)
            else:
                log.warning("%s: 추출 행 0건 (매핑 확인 필요)", code)
                fail += 1
                state["failed"].append(code)

        state["done"].append(code)

        # 100건마다 체크포인트 저장
        if (ok + fail) % 100 == 0:
            save_checkpoint(state, state_path)
            log.info("중간 체크포인트: 성공 %d / 실패 %d / 호출 %d", ok, fail, calls)

    save_checkpoint(state, state_path)
    conn.close()
    log.info("완료: 성공 %d / 실패 %d / 호출 %d / 로그 %s", ok, fail, calls, log_path)


if __name__ == "__main__":
    main()

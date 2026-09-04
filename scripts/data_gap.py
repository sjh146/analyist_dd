#!/usr/bin/env python3
"""market_data 일봉 공실 감지 + KIS 백필 (자동 크론용).

- report  : 최근 N일 평일(휴장 제외) 중 market_data 적재가 임계 미만인 날 탐지
  → 의심 날짜는 KIS 1종목 프로브(limit=1)로 '실제 공실 vs 휴장' 판별.
    휴장(no_data)이면 KRX 휴장 파일(data/krx_holidays.json)에 기록.
- backfill: 공실 날짜 중 가장 오래된 1일을 전체 유니버스로 수집 (약 3.5~4h).
  실행 중복 방지(잠금 파일), 실행 중 수집기와 충돌 방지(pgrep 체크).

스케줄 의도: report = 매일 07:50 (전일 19:00 파이프라인 결과 기준),
backfill = 평일 04:15 (23:00 분봉 수집 종료 후, 19:00 파이프라인 전).
사용 LLM 없음(전부 로컬). 출력은 Discord 보고용 stdout.
"""
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta

PROJ = "/home/dduckbeagy/analyist_dd"
HOLIDAY_PATH = os.path.join(PROJ, "data", "krx_holidays.json")
LOCK_PATH = "/tmp/data_gap_backfill.lock"
GAP_THRESHOLD = 1000  # 정상 적재 ≈ 3,942종목; 이 미만이면 공실/부분 수집
LOOKBACK_DAYS = 10    # 점검 기간(달력일)
EXPECTED_FULL = 3900  # (참고용 로그)

_db_host = os.environ.get("POSTGRES_HOST", "127.0.0.1")
if _db_host in ("postgres", "db"):
    _db_host = "127.0.0.1"
_db_port = int(os.environ.get("POSTGRES_PORT", "5434") or 5434)
if _db_host in ("127.0.0.1", "localhost") and _db_port == 5432:
    _db_port = 5434  # .env 컨테이너 기본값 → 호스트 매핑 포트
DB = dict(
    host=_db_host,
    port=_db_port,
    user=str(os.environ.get("POSTGRES_USER", "stock_user")),
    password=str(os.environ.get("POSTGRES_PASSWORD", "")),
    dbname=str(os.environ.get("POSTGRES_DB", "stock_trading")),
)


def load_holidays():
    try:
        with open(HOLIDAY_PATH, encoding="utf-8") as f:
            return set(json.load(f))
    except (OSError, ValueError):
        return set()


def save_holidays(days):
    os.makedirs(os.path.dirname(HOLIDAY_PATH), exist_ok=True)
    tmp = HOLIDAY_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(sorted(days), f, ensure_ascii=False, indent=1)
    os.replace(tmp, HOLIDAY_PATH)


def pg_count(trade_date):
    """market_data 적재 종목 수 (또는 -1=DB 오류)."""
    import psycopg2

    conn = psycopg2.connect(**DB, connect_timeout=5)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT count(*) FROM market_data WHERE trade_date=%s", (trade_date,)
        )
        n = int(cur.fetchone()[0])
        cur.close()
    finally:
        conn.close()
    return n


def probe_kis(trade_date):
    """KIS 1종목 프로브: (존재: bool, no_data: bool). 휴장/미확정이면 no_data."""
    cmd = [
        "/usr/bin/python3", "-m", "kis_app.main",
        "--job", "daily", "--date", trade_date, "--limit", "1",
    ]
    env = dict(os.environ)
    env.update(
        POSTGRES_HOST="127.0.0.1", POSTGRES_PORT="5434",
        POSTGRES_USER=DB["user"], POSTGRES_PASSWORD=DB["password"],
        POSTGRES_DB=DB["dbname"],
    )
    out = subprocess.run(
        cmd, cwd=os.path.join(PROJ, "services/kis-collector"),
        env=env, capture_output=True, text=True, timeout=180,
    )
    text = out.stdout + out.stderr
    m_ok = re.search(r"\bok=(\d+)", text)
    m_nd = re.search(r"\bno_data=(\d+)", text)
    ok = int(m_ok.group(1)) if m_ok else 0
    nd = int(m_nd.group(1)) if m_nd else 0
    return ok > 0, nd > 0 and ok == 0


def expected_dates():
    """최근 LOOKBACK_DAYS 달력일 중 평일(월~금) 목록 (문자열 YYYY-MM-DD)."""
    out = []
    today = date.today()
    for i in range(1, LOOKBACK_DAYS + 1):
        d = today - timedelta(days=i)
        if d.weekday() < 5:
            out.append(d.isoformat())
    return out


def find_gaps(probe=True):
    """→ (gaps: [(date, count)], holidays: set) — 공실 후보만 probe."""
    holidays = load_holidays()
    gaps = []
    for d in expected_dates():
        if d in holidays:
            continue
        try:
            n = pg_count(d)
        except Exception as e:
            print("DB 조회 실패: {0}".format(e))
            sys.exit(1)
        if n >= GAP_THRESHOLD:
            continue
        if probe:
            exists, no_data = probe_kis(d.replace("-", ""))
            if no_data:
                holidays.add(d)
                save_holidays(holidays)
                print("휴장 기록: {0} (KIS no_data)".format(d))
                continue
            if not exists:
                # 데이터 자체가 아직 없음(예: 당일 장중) — 공실 아님
                continue
        gaps.append((d, n))
    gaps.sort()  # 오래된 날짜부터 백필
    return gaps, holidays


def cmd_report():
    gaps, _holidays = find_gaps(probe=True)
    print("=== market_data 일봉 공실 점검 ({0}) ===".format(datetime.now().strftime("%Y-%m-%d %H:%M")))
    print("점검 기간: 최근 {0} 평일 (정상 ≈ {1}종목/일, 임계 {2})".format(
        LOOKBACK_DAYS, EXPECTED_FULL, GAP_THRESHOLD))
    if not gaps:
        print("공실 없음 — 데이터 정상")
        return 0
    for d, n in gaps:
        print("공실: {0} (적재 {1}종목)".format(d, n))
    print("다음 백필: {0} (가장 오래된 날짜부터, 밤 04:15 크론)".format(gaps[0][0]))
    return 0 if os.path.exists(LOCK_PATH) else 1


def cmd_backfill():
    if os.path.exists(LOCK_PATH):
        print("백필 잠금 존재 — 다른 백필 진행 중, 종료")
        return 0
    gaps, _holidays = find_gaps(probe=False)  # 보고 크론이 이미 probe함
    if not gaps:
        print("백필할 공실 없음")
        return 0
    # 실행 중인 수집기/파이프라인 확인 (분봉 23:00, 저녁 19:00 등)
    busy = subprocess.run(
        "pgrep -af 'kis_app.main|evening_pipeline' || true", shell=True,
        capture_output=True, text=True,
    ).stdout.strip()
    if busy:
        print("수집기 실행 중 — 백필 보류:\n{0}".format(busy[:300]))
        return 0
    target = gaps[0][0].replace("-", "")
    open(LOCK_PATH, "w").write(target)
    try:
        print("백필 시작: {0} ({1} 공실 대기)".format(target, len(gaps)))
        cmd = [
            "/usr/bin/python3", "-m", "kis_app.main",
            "--job", "daily", "--date", target,
        ]
        env = dict(os.environ)
        env.update(
            POSTGRES_HOST="127.0.0.1", POSTGRES_PORT="5434",
            POSTGRES_USER=DB["user"], POSTGRES_PASSWORD=DB["password"],
            POSTGRES_DB=DB["dbname"],
        )
        r = subprocess.run(
            cmd, cwd=os.path.join(PROJ, "services/kis-collector"),
            env=env, capture_output=True, text=True, timeout=60 * 60 * 8,
        )
        tail = (r.stdout + r.stderr).strip().splitlines()[-6:]
        print("\n".join(tail))
        n = pg_count(target.replace("-", ""))
        print("백필 완료 {0}: 적재 {1}종목".format(target, n))
    finally:
        os.remove(LOCK_PATH)
    return 0


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "report"
    if mode == "report":
        sys.exit(cmd_report())
    elif mode == "backfill":
        sys.exit(cmd_backfill())
    else:
        print("usage: data_gap.py report|backfill")
        sys.exit(2)

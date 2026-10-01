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
from datetime import date, datetime, time, timedelta

try:  # 자기신고(R23) — 배선 실패가 수집을 깨지 않도록 방어적으로 import
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from dq_claim import claim_start, claim_finish  # noqa: E402
except Exception:  # noqa: BLE001
    claim_start = claim_finish = None

PROJ = "/home/dduckbeagy/analyist_dd"
HOLIDAY_PATH = os.path.join(PROJ, "data", "krx_holidays.json")
LOCK_PATH = "/tmp/data_gap_backfill.lock"
GAP_THRESHOLD = 1000  # 정상 적재 ≈ 3,942종목; 이 미만이면 공실/부분 수집
LOOKBACK_DAYS = 10    # 점검 기간(달력일)
# 휴장 확정 시각(당일): 장 마감 15:30 + 정산 여유. 이 시각 이전에는 **당일을 휴장으로 확정하지 않는다**.
# WHY(실측 2026-10-01 07:50): 장 개시 전에는 당일 일봉이 아직 없어 KIS 프로브가 no_data 를 돌려주고,
# 그 값이 그대로 휴장으로 기록됐다 → 캘린더에 거래일(10-01)이 휴장으로 들어가 수집·감시·실험 창이 꺼졌다.
HOLIDAY_CONFIRM_HHMM = (15, 40)
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


def probe_krx(trade_date):
    """KRX 2콜 프로브: (존재: bool, no_data: bool).

    KIS 자격증명이 없거나 토큰 발급이 실패하면 공실/휴장 판별이 무력해진다(KIS 프로브
    전용이던 시절 '공실 없음'으로 조용히 흘렸다). 그때는 KRX OpenAPI로 같은 판별을 한다 —
    날짜 1개에 2콜이면 되고, 휴장이면 krx_daily가 출력한 '휴장 기록' 문구로 판정한다.
    """
    cmd = [
        "/usr/bin/python3", os.path.join(PROJ, "scripts", "krx_daily.py"),
        "--from", trade_date, "--to", trade_date,
        "--ignore-run-gap", "--ignore-progress",
    ]
    env = dict(os.environ)
    env.update(
        POSTGRES_HOST="127.0.0.1", POSTGRES_PORT="5434",
        POSTGRES_USER=DB["user"], POSTGRES_PASSWORD=DB["password"],
        POSTGRES_DB=DB["dbname"],
    )
    try:
        out = subprocess.run(
            cmd, cwd=PROJ, env=env, capture_output=True, text=True, timeout=600,
        )
    except subprocess.TimeoutExpired:
        return False, False
    text = out.stdout + out.stderr
    if "휴장 기록" in text:
        return False, True
    m = re.search(r":\s*(\d+)종목 적재", text)
    if m and int(m.group(1)) > 0:
        return True, False
    return False, False

def holiday_confirmable(d):
    """당일 휴장을 확정해도 되는 시각인가 — 과거일은 항상 True, 당일은 장 마감 후에만 True.

    WHY: cron report(07:50)는 장 개시 전에 돌므로 당일 일봉 공실은 '아직 안 나온 것'이지 휴장이 아니다.
    """
    return d != date.today().isoformat() or datetime.now() >= datetime.combine(date.today(), time(*HOLIDAY_CONFIRM_HHMM))


def kis_is_trading_day(d):
    """KIS 국내휴장일조회(CTCA0903R): True=거래일 / False=휴장 / None=판별불가.

    판별불가일 때 **휴장으로 단정하지 않는다**(오탐이 실제 손해 — 거래일을 휴장으로 굳히면
    백필·실험·감시가 통째로 건너뛰어진다). 휴장이면 다음 회차의 과거일 경로가 정상 기록한다.
    """
    import urllib.request
    try:
        env = {}
        with open(os.path.join(PROJ, ".env"), encoding="utf-8") as f:
            for ln in f:
                if "=" in ln and not ln.strip().startswith("#"):
                    k, v = ln.strip().split("=", 1)
                    env[k] = v.strip().strip("'\"")
        with open(os.path.join(PROJ, "data", "kis", "token_cache.json"), encoding="utf-8") as f:
            tok = json.load(f)["access_token"]
        q = "BASS_DT=" + d.replace("-", "") + "&CTX_AREA_NK100=&CTX_AREA_FK100="
        req = urllib.request.Request(
            env["KIS_BASE_URL"].rstrip("/") + "/uapi/domestic-stock/v1/quotations/chk-holiday?" + q,
            headers={"authorization": "Bearer " + tok, "appkey": env["KIS_APP_KEY"],
                     "appsecret": env["KIS_APP_SECRET"], "tr_id": "CTCA0903R",
                     "custtype": "P", "content-type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            out = json.loads(r.read())["output"][0]
        return out.get("opnd_yn") == "Y" or out.get("tr_day_yn") == "Y"
    except Exception as exc:  # noqa: BLE001 - 판별 실패는 '휴장'이 아니다(오탐 방지)
        print("KIS 휴장일조회 판별 불가({0}): {1}".format(d, exc))
        return None



def expected_dates():
    """최근 LOOKBACK_DAYS+1 달력일 중 평일(월~금) 목록 (문자열 YYYY-MM-DD). **당일 포함.**

    ⚠ 종전에는 ``range(1, ...)`` 이라 **당일이 빠졌다**. 그래서 휴장일 D 의 휴장 사실이
    D+1 probe 에서야 캘린더에 기록됐고, D 당일 자율 루프 가드(market_hours)는 캘린더에 D 가
    없으니 '장중'으로 오판해 하루 종일 CPU 를 놀렸다(실측 2026-09-25 추석 연휴: 6시간 유휴,
    당시 수정은 캘린더에 09-25 를 손으로 넣는 임시방편이었다 — R7).
    당일을 포함하되 **공실로 세지 않는** 처리는 find_gaps 가 한다(당일은 장중일 수 있다).
    """
    out = []
    today = date.today()
    for i in range(0, LOOKBACK_DAYS + 1):
        d = today - timedelta(days=i)
        if d.weekday() < 5:
            out.append(d.isoformat())
    return out


def find_gaps(probe=True):
    """→ (gaps: [(date, count)], holidays: set) — 공실 후보만 probe.

    당일은 **휴장 판정만** 하고 gaps 에는 넣지 않는다: 장중이면 일봉이 아직 없어 count=0 이므로
    공실로 단정하면 매일 백필이 돈다(실측 위험: 장중 실행). 휴장이면 no_data 로 즉시 캘린더에 남는다.
    """
    holidays = load_holidays()
    today_s = date.today().isoformat()
    # 자가치유: 오탐으로 캘린더에 굳은 '오늘'을 KIS 교차확인으로 걷어낸다(1콜, 캘린더에 있을 때만).
    if today_s in holidays and kis_is_trading_day(today_s):
        holidays.discard(today_s)
        save_holidays(holidays)
        print("휴장 캘린더 정정: {0} 제거 (KIS 국내휴장일조회 = 거래일)".format(today_s))
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
            if not exists and not no_data:
                # KIS 키 없음/미인증/게이트웨이 오류 등 판별 불가 → KRX 2콜 프로브로 대체
                print("KIS 프로브 판별 불가 → KRX 프로브로 대체: {0}".format(d))
                exists, no_data = probe_krx(d)
            if no_data:
                if d == today_s and not (holiday_confirmable(d) and kis_is_trading_day(d) is False):
                    # 당일은 ① 장 마감 전이거나 ② KIS 휴장일조회가 거래일/판별불가면 기록하지 않는다.
                    # (R24 실측 2026-10-01: 장 개시 전 no_data 를 휴장으로 적어 거래일이 캘린더에 들어갔다.)
                    print("휴장 기록 보류: {0} (당일 — 마감 전 또는 KIS 교차확인 미통과)".format(d))
                    continue
                holidays.add(d)
                save_holidays(holidays)
                print("휴장 기록: {0} (KIS no_data)".format(d))
                continue
            if not exists:
                # 데이터 자체가 아직 없음(예: 당일 장중) — 공실 아님
                continue
        if d == today_s:
            # 당일은 **공실로 세지 않는다** (probe 유무와 무관).
            # 실측 2026-10-01 04:19: 백필 경로는 find_gaps(probe=False) 를 쓰는데 종전에는
            # 당일 제외가 probe 분기 안에만 있어, 04:15 크론이 **당일을 공실로 판정**했다
            # → KIS 일봉을 장 개시 전에 3,700종목 돌려 평탄·거래량 0 봉을 적재
            #   (market_data_freshness_days = -1, 수급 지연 판정 2 → 3 오탐 미달).
            # 당일 봉은 마감 후 공식 경로(18:55 daily_bars / 20:00 파이프라인)가 넣는다.
            continue
        gaps.append((d, n))
    gaps.sort()  # 오래된 날짜부터 백필
    return gaps, holidays


def db_state():
    """(전체 행수, 최신 거래일) — 빈 DB/장기 공백 조기 감지용."""
    import psycopg2

    conn = psycopg2.connect(**DB, connect_timeout=5)
    try:
        cur = conn.cursor()
        cur.execute("SELECT count(*), max(trade_date) FROM market_data")
        n, mx = cur.fetchone()
        cur.close()
        return int(n or 0), mx
    finally:
        conn.close()


def stale_warning():
    """빈 DB 또는 점검 창(LOOKBACK_DAYS) 밖의 공백 → (bool, 메시지).

    LOOKBACK_DAYS=10 만 보므로, 마지막 적재가 그보다 오래되면 '공실 없음'으로
    잘못 보고한다(실측 2026-09-22: 빈 DB에서 '데이터 정상' 출력). 구간 백필
    러너(scripts/kis_backfill_range.py)로 넘기기 위한 선행 점검.
    """
    try:
        n, mx = db_state()
    except Exception as e:  # noqa: BLE001
        return False, "DB 조회 실패: {0}".format(e)
    if n == 0:
        return True, ("market_data 가 비어 있음 — 일봉 전체 백필 필요 "
                      "(scripts/kis_backfill_range.py 로 구간 수집)")
    cutoff = date.today() - timedelta(days=LOOKBACK_DAYS)
    if mx is not None and mx < cutoff:
        days = (date.today() - mx).days
        return True, ("최신 적재 {0} (달력 {1}일 전) — 점검 창 {2}일 밖: "
                      "구간 백필 필요 (scripts/kis_backfill_range.py)"
                      .format(mx, days, LOOKBACK_DAYS))
    return False, "최신 적재 {0} / {1:,}행".format(mx, n)


def cmd_report():
    stale, note = stale_warning()
    print("=== market_data 일봉 공실 점검 ({0}) ===".format(datetime.now().strftime("%Y-%m-%d %H:%M")))
    print("상태: {0}".format(note))
    if stale:
        print("→ 최근 {0}일 점검만으로는 판단 불가 — 구간 백필을 먼저 실행하세요.".format(LOOKBACK_DAYS))
        return 2
    gaps, _holidays = find_gaps(probe=True)
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
    stale, note = stale_warning()
    if stale:
        print("상태: {0}".format(note))
        print("→ 1일/야간 백필로는 부족합니다. 구간 백필을 사용하세요:")
        print("   cd {0}/services/kis-collector && python3 ../../scripts/kis_backfill_range.py".format(PROJ))
        return 0
    gaps, _holidays = find_gaps(probe=False)  # 보고 크론이 이미 probe함
    if not gaps:
        print("백필할 공실 없음")
        return 0
    # 실행 중인 수집기/파이프라인 확인 (분봉 23:00, 저녁 19:00 등)
    # 자기매칭 주의: shell=True 로 pgrep 을 돌리면 **자기 명령줄**이 패턴에 걸려 항상
    # "수집기 실행 중" 으로 보인다 → 백필이 영구히 건너뛰어진다(2026-09-29 위생점검 실측:
    # 로그의 매칭 PID 가 `sh -c pgrep -af 'kis_app.main|evening_pipeline'` 자기 자신).
    # 브래킷 트릭으로 패턴이 자기 자신을 매칭하지 않게 만든다.
    busy = subprocess.run(
        "pgrep -af 'kis_app[.]main|evening_pipeline[.]sh|[k]is_supply_backfill"
        "|[k]is_minute|[k]is_short_program|[k]rx_offline|[d]aily_bars' || true",
        shell=True, capture_output=True, text=True,
    ).stdout.strip()
    if busy:
        print("수집기 실행 중 — 백필 보류:\n{0}".format(busy[:300]))
        return 0
    target = gaps[0][0].replace("-", "")
    open(LOCK_PATH, "w").write(target)
    if claim_start:
        # 자기신고(R23): 이 러너는 파서가 없다 — 자식(kis_app/krx_daily)이 자기신고를 남기고,
        # 여기서는 '위임 실행 1회'의 실적재 델타를 남긴다(persisted 는 헬퍼가 계산).
        claim_start("data_gap_backfill", "market_data",
                    note=f"date={target} gaps={len(gaps)}")
    try:
        print("백필 시작: {0} ({1} 공실 대기)".format(target, len(gaps)))
        env = dict(os.environ)
        env.update(
            POSTGRES_HOST="127.0.0.1", POSTGRES_PORT="5434",
            POSTGRES_USER=DB["user"], POSTGRES_PASSWORD=DB["password"],
            POSTGRES_DB=DB["dbname"],
        )
        # 수집 경로 선택: KIS 자격증명이 있으면 KIS, 없으면 KRX OpenAPI (BC250 수리 전 대체)
        has_kis = bool(env.get("KIS_APP_KEY") and env.get("KIS_APP_SECRET"))
        if has_kis:
            print("경로: KIS 일봉")
            cmd = [
                "/usr/bin/python3", "-m", "kis_app.main",
                "--job", "daily", "--date", target,
            ]
            cwd = os.path.join(PROJ, "services/kis-collector")
        else:
            print("경로: KRX OpenAPI (KIS 키 없음)")
            cmd = [
                "/usr/bin/python3", os.path.join(PROJ, "scripts", "krx_daily.py"),
                "--from", target, "--to", target, "--ignore-run-gap",
            ]
            cwd = PROJ
        r = subprocess.run(
            cmd, cwd=cwd,
            env=env, capture_output=True, text=True, timeout=60 * 60 * 8,
        )
        tail = (r.stdout + r.stderr).strip().splitlines()[-6:]
        print("\n".join(tail))
        n = pg_count(target)
        print("백필 완료 {0}: 적재 {1}종목 (exit={2})".format(target, n, r.returncode))
        if claim_finish:
            claim_finish("data_gap_backfill", source_rows=n, claimed_rows=n,
                         note=f"date={target} exit={r.returncode} child=kis_app|krx_daily")
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

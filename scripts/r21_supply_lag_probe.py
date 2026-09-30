#!/usr/bin/env python3
"""수급 최신일 지연 상시 감시 프로브 (읽기 전용 — KIS 호출 0회, DB SELECT 몇 회).

WHY (2026-09-29): R19 는 수급 러너(호출 ~700회 / 25분)를 다시 돌려 '지연 상한 ≤2거래일'을
확인했다. 그런데 목표가 충족되면(rc=0 + check 통과) 구동기가 항목을 `done` 으로 적어
**감시가 사라진다** — 회전을 멈춘 주체를 아무도 못 본다. 반대로 감시를 위해 매 틱 러너를
다시 돌리면 KIS 호출 예산을 모니터링이 태운다(일일 한도 미확인 = R20 승인 항목).
→ 감시는 **싼 읽기 전용 프로브**로 분리한다: 수집은 하루 1회 크론(kis_supply.sh, 16:20),
감시는 이 스크립트(2시간 틱, DB 쿼리 몇 회), 판정은 백로그 check(같은 스크립트).

판정 모집단 — 1차 수정(2026-09-29 04:0x): '시장 최신일'이 아니라 **종목별 자기 시세의 최신일**
대비로 센다. 거래정지 종목(예: 008290 — market_data·수급 모두 2026-09-18 정지)은 수급이
'마땅히 있어야 하는데 없는' 것이 아니므로 지연에 세지 않는다.

판정 모집단 — 2차 수정(2026-09-29 20:0x 실측): 종목별 기준으로 바꿔도 **모집단이 '테이블 전체'**
면 여전히 '고쳐도 통과 못 하는 check' 다. foreign_institutional 에는 일일 회전
(`kis_supply_backfill.liquidity_universe` = 유동성 상위 800)이 **애초에 담당하지 않는** 코드가
섞여 있고(실측 1,034종목 중 238), 그 코드들의 지연은 회전이 돌아도 영원히 자란다.
실측 같은 시각: 테이블 전체 최대 3거래일(회전 밖 11종목이 9/22 에 멈춤) vs **회전 유니버스
최대 1거래일**(785종목 0 / 15종목 1). 전체를 분모로 쓰면 회전이 완벽히 돌아도 매 틱 '미달'이 뜬다.
→ 판정은 **수집 경로가 책임지는 모집단**(회전 유니버스 = 유동성 상위 800)으로 한정하고,
모델 유니버스(R3 800)와 테이블 전체는 **정보 줄**로만 함께 찍는다(원인 분해용).
모델 유니버스 800 중 220종목은 회전 유니버스 밖이다 — 이 커버리지 구멍은 승인 항목 R20 에
올려 두었다(회전 확대 = KIS 일일 한도 확인 필요).

판정 기준 — 3차 수정(2026-09-30 21:1x 실측): 종전 판정은 기준일을 '시장 최신일'로 잡았는데,
그 기준일이 **수집 주기와 무관하게** 하루 앞으로 밀리면 **같은 DB 상태가 다른 판정**을 받았다:
  · 20:12 틱 — market_data 최신 09-29 / 수급 최신 09-29 → 최대 지연 **2 = 충족**
  · 21:13 틱 — 수급은 **그대로**(142,996행·최신 09-29)인데 저녁 파이프라인이 09-30 일봉을 적재해
    시장 최신일이 09-30 으로 밀림 → 최대 지연 **3 = 미달**
수집 러너는 16:20 에 돌고 그 시각 KIS 가 제공한 최신일까지만 담으므로(실측 09-30 16:20 실행의
as-of = 09-29), '오늘 바가 들어왔다'는 이유로 지연이 늘어나는 것은 **수집 결함이 아니라 시계 차이**다.
→ 판정을 두 항으로 분해한다:
  ① **상대 지연** = 회전 유니버스가 *실제로 도달한* 최신 수급일 대비 꼬리 종목의 지연
     (설계: 250종목/일 회전 → 2거래일). 회전이 느려지면 이 값이 자란다.
  ② **절대 정지** = 시장 최신일 − 수급 최신일(거래일). 수급 수집은 T+1 이 정상(1)이므로 2 이상이면
     수집이 통째로 멈춘 것이다 → +1 로 반영해 문턱(2)을 넘긴다.
판정값 = max(①, ②+1). 정상 상태에서 2 이고, 수집이 하루 더 밀리면 3 = 미달.
(기준일이 기준을 삼키는 것을 막으려고 ①의 기준을 '시장'이 아니라 '수집이 도달한 곳'으로 옮겼다 —
 멈춘 수집은 ①에서는 보이지 않으므로 ②가 그 구멍을 덮는다. 둘 중 하나라도 나빠지면 판정이 나빠진다.)

출력: 마지막 줄의 수치가 **판정값**(회전 유니버스 지연 판정 거래일)이다 — 구동기 check 규약.
사용:
  cd /home/jhshi/analyist_dd && POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434 \
    /usr/bin/python3 scripts/r21_supply_lag_probe.py
종료코드: 0 정상 조회 / 1 DB 오류(러너 환경 문제 — 조용히 넘기면 감시가 실명한다).
"""
import os
import sys

import psycopg2

PROJ = os.environ.get("PROJ_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_UNIVERSE_FILE = os.path.join(PROJ, "data", "kis", "supply_universe_800.txt")

PG = {
    "host": os.environ.get("POSTGRES_HOST", "127.0.0.1"),
    "port": int(os.environ.get("POSTGRES_PORT", "5434")),
    "user": os.environ.get("POSTGRES_USER", "stock_user"),
    "password": os.environ.get("POSTGRES_PASSWORD", ""),
    "dbname": os.environ.get("POSTGRES_DB", "stock_trading"),
}

# 회전 유니버스 = `scripts/kis_supply_backfill.py::liquidity_universe` 와 **같은 정의**여야 한다.
# 회전이 실제로 담당하는 집합이 곧 이 프로브의 판정 모집단이기 때문이다(정의가 어긋나면
# 감시가 엉뚱한 집합을 본다 — R19 의 옛 check 와 같은 함정).
ROTATION_SQL = """
SELECT md.stock_code
FROM market_data md
JOIN stocks s ON s.stock_code = md.stock_code
WHERE s.market IN ('KOSPI', 'KOSDAQ')
  AND s.instrument_type = 'STOCK'
  AND s.stock_name NOT LIKE '%%스팩%%'
  AND md.trade_date > (SELECT MAX(trade_date) - %s FROM market_data)
GROUP BY md.stock_code
HAVING COUNT(*) >= 10
ORDER BY AVG(COALESCE(md.trading_value, md.close_price * md.volume)) DESC NULLS LAST
LIMIT %s
"""

# 모델 유니버스(R3) — 파일이 없으면 `r3_universe_coverage.UNIVERSE_SQL` 과 같은 정의로 재생성.
MODEL_UNIVERSE_SQL = """
SELECT s.stock_code
FROM stocks s
JOIN market_data m ON m.stock_code = s.stock_code
WHERE s.instrument_type = 'STOCK'
GROUP BY s.stock_code
HAVING COUNT(*) >= 250
ORDER BY MAX(s.market_cap) DESC NULLS LAST
LIMIT 800
"""

# ① 상대 지연 — 기준(anchor)은 **회전 유니버스가 실제로 도달한 최신 수급일**이다.
LAG_SQL = """
WITH fi AS MATERIALIZED (
    SELECT stock_code, MAX(trade_date) AS d FROM foreign_institutional GROUP BY 1
)
SELECT COALESCE(MAX(l), 0) AS max_lag,
       COUNT(*) FILTER (WHERE l > 0) AS behind,
       COUNT(*) FILTER (WHERE d IS NULL) AS no_supply
FROM (
    SELECT u.code, fi.d, COUNT(m.trade_date) AS l
    FROM unnest(%s::text[]) AS u(code)
    LEFT JOIN fi ON fi.stock_code = u.code
    LEFT JOIN market_data m ON m.stock_code = u.code
         AND m.trade_date > fi.d AND m.trade_date <= %s
    GROUP BY 1, 2
) t
"""

# 회전 유니버스가 도달한 최신 수급일(anchor).
ANCHOR_SQL = "SELECT MAX(trade_date) FROM foreign_institutional WHERE stock_code = ANY(%s)"

# ② 절대 정지 신호 — 시장 최신일이 수급 최신일보다 몇 거래일 앞서 있는가(시장 전체 거래일 기준).
ABS_GAP_SQL = "SELECT COUNT(DISTINCT trade_date) FROM market_data WHERE trade_date > %s"

# 정보용: 테이블 전체(회전 담당 밖 코드 포함)를 '시장 최신일' 기준으로 센 값.
TABLE_LAG_SQL = """
WITH fi AS MATERIALIZED (
    SELECT stock_code, MAX(trade_date) AS d FROM foreign_institutional GROUP BY 1
)
SELECT COALESCE(MAX(l), 0), COUNT(*) FILTER (WHERE l > 0)
FROM (
    SELECT m.stock_code, COUNT(*) AS l
    FROM market_data m
    JOIN fi ON fi.stock_code = m.stock_code AND m.trade_date > fi.d
    GROUP BY 1
) t
"""

FAIL_CLOSED = 99  # 수급 최신일 자체가 없으면 판정 불가 → 위반으로 처리(실명 감추기 금지)


def judgment(rel_max, abs_gap):
    """판정값 = max(상대 지연, 절대 정지 신호 + 1).

    정상 상태(T+1 수집 · 250종목/일 회전)에서 두 항이 모두 2 가 되어 문턱(≤2)과 만난다.
    rel_max=None 은 '수급 최신일 자체가 없다' = 판정 불가 → FAIL_CLOSED.
    """
    if rel_max is None:
        return FAIL_CLOSED
    return max(int(rel_max), int(abs_gap) + 1)


def lag_stats(cur, codes, anchor):
    """코드 집합에 대해 (최대 지연, 뒤처진 종목수, 수급 행이 아예 없는 종목수) — anchor 기준."""
    if not codes or anchor is None:
        return None
    cur.execute(LAG_SQL, (list(codes), anchor))
    return cur.fetchone()


def model_universe(cur):
    try:
        with open(MODEL_UNIVERSE_FILE, encoding="utf-8") as f:
            codes = sorted({ln.strip() for ln in f if ln.strip()})
        if codes:
            return codes, f"파일 {os.path.relpath(MODEL_UNIVERSE_FILE, PROJ)}"
    except OSError:
        pass
    cur.execute(MODEL_UNIVERSE_SQL)
    return sorted({r[0] for r in cur.fetchall()}), "SQL 재생성(파일 없음)"


def main():
    try:
        conn = psycopg2.connect(**PG)
    except Exception as exc:  # noqa: BLE001 - 조회 실패는 실명이므로 rc=1 로 크게 남긴다
        print(f"DB 연결 실패: {type(exc).__name__}: {exc}", flush=True)
        return 1
    try:
        cur = conn.cursor()
        cur.execute("SELECT MAX(trade_date) FROM market_data")
        market_max = cur.fetchone()[0]
        cur.execute(ROTATION_SQL, (60, 800))
        rotation = sorted({r[0] for r in cur.fetchall()})
        if not rotation:
            print("회전 유니버스 0종목 — 정의 SQL 확인 필요", flush=True)
            return 1
        model, model_src = model_universe(cur)
        cur.execute(ANCHOR_SQL, (rotation,))
        anchor = cur.fetchone()[0]
        rot_stats = lag_stats(cur, rotation, anchor)
        model_stats = lag_stats(cur, model, anchor)
        if anchor is None:
            abs_gap = 0
        else:
            cur.execute(ABS_GAP_SQL, (anchor,))
            abs_gap = cur.fetchone()[0]
        cur.execute(TABLE_LAG_SQL)
        tbl_max, tbl_behind = cur.fetchone()
        cur.execute("SELECT COUNT(DISTINCT stock_code), COUNT(*) FROM foreign_institutional")
        stocks, rows = cur.fetchone()
        cur.close()
    finally:
        conn.close()

    rel_max, rot_behind, rot_nosupply = rot_stats or (None, 0, len(rotation))
    verdict = judgment(rel_max, abs_gap)
    rel_txt = "판정 불가(수급 최신일 없음)" if rel_max is None else f"{rel_max}거래일"
    print(f"수급 지연 프로브(읽기전용) — 시장 최신일 {market_max} / 수급 도달 최신일 {anchor}")
    print(f"· 판정 모집단 = 회전 유니버스(유동성 상위 {len(rotation)}종목): "
          f"상대 최대 지연 {rel_txt} / 뒤처진 {rot_behind}개"
          + (f" / 수급 행 없음 {rot_nosupply}개" if rot_nosupply else ""))
    print(f"· [정보] 시장 최신일 − 수급 최신일 = {abs_gap}거래일 (T+1 수집이면 1이 정상)"
          f" → 정지 신호 {abs_gap + 1}")
    if model_stats:
        m_max, m_behind, _ = model_stats
        outside = len(set(model) - set(rotation))
        print(f"· [정보] 모델 유니버스(R3 {len(model)}종목, {model_src}): "
              f"상대 최대 지연 {m_max}거래일 / 뒤처진 {m_behind}개 / 회전 밖 {outside}종목")
    print(f"· [정보] 테이블 전체 {stocks}종목 {rows:,}행(시장 최신일 기준): "
          f"최대 지연 {tbl_max}거래일 / 뒤처진 {tbl_behind}개 — 회전 담당 밖 코드 포함(판정 제외)")
    # stdout 마지막 수치 = 판정값(구동기 eval_check 규약).
    print(f"회전 유니버스 최대 지연 {verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""수급(supply) 수집기 — KIS OpenAPI 투자자별 매매동향 + 종목 현재가 → DB 저장.

채우는 테이블
  - ``foreign_institutional`` : 종목·일자별 외국인/기관/개인 순매수 (tr_id ``FHKST01010900``)
  - ``ownership``            : 종목별 외국인 지분율 스냅샷 (tr_id ``FHKST01010100``)

TR / 엔드포인트 (2026-09-24 실측 확정 — 상세는 ``kis_app.client.supply_client``)
  - ``FHKST01010900`` ``/uapi/domestic-stock/v1/quotations/inquire-investor``
      · 응답 배열 키는 **``output``** 이다(``output2`` 아님). **1콜 = 최대 30행**(최신순)이라
        60영업일은 2콜이 필요하다(``SupplyClient.get_investor_flow_all`` 이 페이지네이션).
      · ``*_tr_pbmn`` 단위는 **백만원** — 실측 검증: 005930 2026-09-23
        qty 4,513,767 × 종가 286,500원 = 1.293e12원 ≈ API값 1,283,306 × 1e6.
  - ``FHKST01010100`` ``/uapi/domestic-stock/v1/quotations/inquire-price``
      · ``hts_frgn_ehrt``(외국인 지분율 %) · ``frgn_hldn_qty`` · ``lstn_stcn``.
        실측 검증: 2,726,977,583 / 5,846,278,608 = 46.64% = ``hts_frgn_ehrt``.

단위 계약 (리더가 단위를 지정하지 않으므로 writer 가 고정한다)
  - ``*_net_buy``     = 순매수 **거래대금(원)**  ← KIS ``*_ntby_tr_pbmn``(백만원) × 1e6
  - ``*_net_buy_qty`` = 순매수 **수량(주)**      ← KIS ``*_ntby_qty``
  - 부호 유지: 양수 = 순매수, 음수 = 순매도

한계 (지분율)
  - KIS 는 **현재 스냅샷만** 준다(과거 시점 조회 불가) → ``ownership`` 행은 종목당 1건.
  - **기관 지분율 필드는 KIS 에 없다**(현재가 응답 필드 전수 확인) → ``institution_ownership_pct``
    는 억지로 0 을 채우지 않고 NULL 로 둔다. 그 결과 리더가 계산하는 ``retail_ownership_pct`` 는
    ``100 - 외국인지분율``(= 비외국인: 개인+기관) 이 된다.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

logger = logging.getLogger("kis_collector.supply")

# ``*_tr_pbmn`` 단위 = 백만원 → 원(KRW)
PBMN_TO_KRW = 1_000_000

FLOW_COLUMNS = ("stock_code", "trade_date", "foreign_net_buy", "institution_net_buy",
                "individual_net_buy", "foreign_net_buy_qty", "institution_net_buy_qty",
                "individual_net_buy_qty")

# 순매수 수량 컬럼 (지연 DB 대응 — 마이그레이션 SQL 과 동일 내용)
FLOW_DDL = """
ALTER TABLE foreign_institutional
    ADD COLUMN IF NOT EXISTS foreign_net_buy_qty BIGINT;
ALTER TABLE foreign_institutional
    ADD COLUMN IF NOT EXISTS institution_net_buy_qty BIGINT;
ALTER TABLE foreign_institutional
    ADD COLUMN IF NOT EXISTS individual_net_buy_qty BIGINT;
"""

OWNERSHIP_DDL = """
CREATE TABLE IF NOT EXISTS ownership (
    id SERIAL PRIMARY KEY,
    stock_code VARCHAR(10) NOT NULL REFERENCES stocks(stock_code),
    trade_date DATE NOT NULL,
    foreign_ownership_pct DECIMAL(8,2),
    institution_ownership_pct DECIMAL(8,2),
    foreign_held_qty BIGINT,
    listed_shares BIGINT,
    source VARCHAR(20) DEFAULT 'kis',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (stock_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_ownership_stock_date ON ownership(stock_code, trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_ownership_date ON ownership(trade_date);
"""

FLOW_UPSERT = """
INSERT INTO foreign_institutional
  (stock_code, trade_date, foreign_net_buy, institution_net_buy, individual_net_buy,
   foreign_net_buy_qty, institution_net_buy_qty, individual_net_buy_qty)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (stock_code, trade_date) DO UPDATE SET
  foreign_net_buy = EXCLUDED.foreign_net_buy,
  institution_net_buy = EXCLUDED.institution_net_buy,
  individual_net_buy = EXCLUDED.individual_net_buy,
  foreign_net_buy_qty = EXCLUDED.foreign_net_buy_qty,
  institution_net_buy_qty = EXCLUDED.institution_net_buy_qty,
  individual_net_buy_qty = EXCLUDED.individual_net_buy_qty
"""

OWNERSHIP_UPSERT = """
INSERT INTO ownership
  (stock_code, trade_date, foreign_ownership_pct, institution_ownership_pct,
   foreign_held_qty, listed_shares, source)
VALUES (%s, %s, %s, NULL, %s, %s, 'kis')
ON CONFLICT (stock_code, trade_date) DO UPDATE SET
  foreign_ownership_pct = EXCLUDED.foreign_ownership_pct,
  foreign_held_qty = EXCLUDED.foreign_held_qty,
  listed_shares = EXCLUDED.listed_shares,
  source = EXCLUDED.source
"""


def _to_int(value) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return None


def _to_float(value) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _iso(yyyymmdd) -> str:
    s = str(yyyymmdd)
    return "{0}-{1}-{2}".format(s[0:4], s[4:6], s[6:8])


def parse_investor_flow(resp) -> List[Dict]:
    """투자자별 매매동향 응답(``{"output": [원시행, ...]}``) → 정규화 행 리스트.

    반환 키: ``trade_date``(YYYY-MM-DD), ``foreign_net_buy``/``institution_net_buy``/
    ``individual_net_buy``(원), ``*_net_buy_qty``(주). 날짜 오름차순 정렬.

    ``stck_bsop_date`` 가 없는 행은 건너뛴다(스키마 변형에 안전). 이미 정규화된 행
    (``trade_date`` 보유)이 들어와도 그대로 통과시킨다.
    """
    rows: List[Dict] = []
    # 응답 배열 키가 TR 마다 다르다 — 둘 다 받아야 한다(실측 2026-09-24):
    #   · inquire-investor(FHKST01010900)          → ``output`` (리스트)
    #   · investor-trade-by-stock-daily(FHPTJ04160001) → ``output2`` (리스트),
    #     ``output1`` 은 현재가 요약 dict 라 쓰지 않는다.
    # ``output`` 만 읽으면 두 번째 TR 이 **조용히 0행**이 된다(확장 러너가 +0행으로 끝난 원인).
    for raw in ((resp or {}).get("output") or (resp or {}).get("output2") or []):
        if not isinstance(raw, dict):
            continue
        bsop = raw.get("stck_bsop_date")
        if not bsop:
            if raw.get("trade_date"):
                rows.append(raw)
            continue

        def _value(key):
            v = _to_float(raw.get(key))
            return None if v is None else v * PBMN_TO_KRW

        rows.append({
            "trade_date": _iso(bsop),
            "foreign_net_buy": _value("frgn_ntby_tr_pbmn"),
            "institution_net_buy": _value("orgn_ntby_tr_pbmn"),
            "individual_net_buy": _value("prsn_ntby_tr_pbmn"),
            "foreign_net_buy_qty": _to_int(raw.get("frgn_ntby_qty")),
            "institution_net_buy_qty": _to_int(raw.get("orgn_ntby_qty")),
            "individual_net_buy_qty": _to_int(raw.get("prsn_ntby_qty")),
        })
    rows.sort(key=lambda r: str(r.get("trade_date") or ""))
    return rows


def parse_price_ownership(resp) -> Dict:
    """현재가 응답 → 지분율 스냅샷.

    ``hts_frgn_ehrt`` 가 비어 있으면 ``frgn_hldn_qty / lstn_stcn × 100`` 으로 계산한다
    (실측으로 두 값이 일치함을 확인).
    """
    out = (resp or {}).get("output")
    if not isinstance(out, dict):
        return {"foreign_ownership_pct": None, "foreign_held_qty": None,
                "listed_shares": None}
    pct = _to_float(out.get("hts_frgn_ehrt"))
    held = _to_int(out.get("frgn_hldn_qty"))
    total = _to_int(out.get("lstn_stcn"))
    if pct is None and held is not None and total:
        pct = round(held / total * 100.0, 2)
    return {"foreign_ownership_pct": pct, "foreign_held_qty": held,
            "listed_shares": total}


class SupplyCollector:
    """수급·지분율 저장소. DDL 자가복구 + upsert(재실행 멱등)."""

    def __init__(self, client, pg_conn):
        self._client = client
        self._conn = pg_conn
        # _drop_untraded_dates 가 버린 행수 / 보존한 행수(가시화용).
        # WHY(2026-09-29 실측): 그 가드는 'market_data 에 그 날짜 행이 있어야 저장'이었는데,
        # 수급 크론(16:20)이 일봉 적재(18:55~)보다 먼저 돌면 **당일 수급이 통째로 떨어졌다**
        # — 로그에는 as-of 2026-09-29 가 찍히는데 DB 9/29 행 0건(최신 9/28), 실측 792/798종목
        # 뒤처짐. 종전엔 logger.info 라 크론 로그(tail stdout)에 남지 않아 완전 무음이었다.
        # 2026-09-29 22:0x 수리: 버리지 않고 **사유를 분리**한다 —
        #   · 시장 전체에 그 날짜 일봉이 없음(아직 미적재) → **보존**(kept_unloaded)
        #   · 시장은 거래했는데 이 종목 행이 없음(상장 전·거래정지) → 제거(dropped)
        self.last_dropped = 0
        self.dropped_total = 0
        self.last_kept_unloaded = 0
        self.kept_unloaded_total = 0
        self._market_max = None  # 시장 일봉 마지막 적재일 캐시(인스턴스당 1회 조회)

    # ── 스키마 ─────────────────────────────────────────────────────────
    def ensure_tables(self):
        """``ownership`` 테이블 생성 + ``foreign_institutional`` 수량 컬럼 보강.

        init-scripts 마이그레이션과 동일 내용을 지연 DB에서도 자기복구한다
        (저장소 계층 관례 — ``PostgresStorage._ensure_tables`` 와 같은 방식).
        """
        cur = self._conn.cursor()
        try:
            for ddl in (FLOW_DDL, OWNERSHIP_DDL):
                for stmt in [s.strip() for s in ddl.split(";") if s.strip()]:
                    cur.execute(stmt)
            self._conn.commit()
        except Exception as e:  # noqa: BLE001
            self._conn.rollback()
            logger.error("수급 테이블 보장 실패: %s", e)
            raise
        finally:
            cur.close()

    # ── 저장 ───────────────────────────────────────────────────────────
    def save_flows(self, stock_code: str, rows: List[Dict]) -> int:
        """``foreign_institutional`` upsert → 저장 행 수.

        **상장 전 구간 패딩 제거**: KIS 이력 조회는 그 종목의 상장(또는 거래개시) 이전 날짜도
        날짜만 채운 all-zero 행으로 돌려준다(실측: 스카이랩스 386380 — market_data 는
        2026-09-04 부터인데 수급 응답은 2026-05-15 부터 0 행이 옴). 그대로 적재하면
        커버리지가 부풀고 상장 직후 5일 평균이 0 으로 희석된다 → **시장이 거래한 날인데 이
        종목 행이 없는 경우만** 제거한다(``_drop_untraded_dates``). 시장 전체에도 그 날짜
        일봉이 아직 없으면(일봉 적재 전 수집) **버리지 않고 저장**한다.
        """
        payload = [
            (str(stock_code), r.get("trade_date"), r.get("foreign_net_buy"),
             r.get("institution_net_buy"), r.get("individual_net_buy"),
             r.get("foreign_net_buy_qty"), r.get("institution_net_buy_qty"),
             r.get("individual_net_buy_qty"))
            for r in (rows or []) if r.get("trade_date")
        ]
        if not payload:
            return 0
        payload = self._drop_untraded_dates(stock_code, payload)
        if not payload:
            return 0
        cur = self._conn.cursor()
        try:
            cur.executemany(FLOW_UPSERT, payload)
            self._conn.commit()
            return len(payload)
        except Exception as e:  # noqa: BLE001
            self._conn.rollback()
            logger.error("foreign_institutional 저장 실패 (%s): %s", stock_code, e)
            raise
        finally:
            cur.close()

    def _drop_untraded_dates(self, stock_code: str, payload: List[tuple]) -> List[tuple]:
        """저장하지 않을 날짜(= 그 종목이 상장/거래되지 않은 날) 행을 제거.

        판정은 **시장 일봉의 마지막 적재일**과 비교한다(2026-09-29 수리):
          · 날짜가 이 종목의 market_data 에 있음 → 저장
          · 그 밖에 마지막 적재일 이하 → 제거(상장 전·거래정지·휴장)
          · 마지막 적재일보다 **뒤** → **저장**(일봉 적재 전 수집 = 적재 순서)
            종전 규칙은 이 경우까지 버려서 수급 크론(16:20)의 **당일 수급 전량이 소실**됐다
            (실측 2026-09-29 16:20: as-of 9/29 로그 / DB 9/29 0행 → 792/798종목 1거래일 지연).
        """
        dates = [p[1] for p in payload]
        cur = self._conn.cursor()
        try:
            # %s::date[] 캐스팅 필수 — 파이썬 문자열 리스트는 text[] 로 어댑트되어
            # ``date = text`` 연산자 부재로 UndefinedFunction 이 난다(실측).
            cur.execute(
                "SELECT trade_date FROM market_data WHERE stock_code = %s "
                "AND trade_date = ANY(%s::date[])",
                (str(stock_code), dates),
            )
            # 날짜 비교는 **문자열로 통일**한다: psycopg2 는 date 컬럼을 datetime.date 로
            # 돌려주는데 payload 의 trade_date 는 'YYYY-MM-DD' 문자열이라
            # ``'2026-08-12' in {date(2026,8,12)}`` 가 항상 False → 전량이 버려진다(실측: saved=0).
            traded = {r[0].isoformat() for r in cur.fetchall()}
            market_max = self._market_max_date(cur)
        finally:
            cur.close()
        kept: List[tuple] = []
        kept_unloaded = 0
        for p in payload:
            d = p[1]
            if d in traded:
                kept.append(p)
            elif market_max and d <= market_max:
                continue  # 상장 전·거래정지·휴장(시장은 움직였는데 이 종목/그 날 일봉 없음)
            else:
                kept.append(p)  # 마지막 적재일 이후 = 일봉 미적재일 → 버리지 않는다
                kept_unloaded += 1
        dropped = len(payload) - len(kept)
        self.last_dropped = dropped
        self.dropped_total += dropped
        self.last_kept_unloaded = kept_unloaded
        self.kept_unloaded_total += kept_unloaded
        if dropped:
            logger.info("%s 상장 전/거래정지 %d행 제외 (거래일 %d행 저장)", stock_code, dropped, len(kept))
        if kept_unloaded:
            # 무음 금지: 이 수치가 >0 이면 '일봉 적재 전 수집'이다(= 당일 수급을 살렸다).
            logger.warning("%s 일봉 미적재일 %d행 보존(버리지 않음 — 적재 순서)", stock_code, kept_unloaded)
        return kept

    def _market_max_date(self, cur) -> str:
        """시장 전체 일봉의 **마지막 적재일**(문자열) — 인스턴스당 1회 캐시.

        판정 기준을 '그 날짜가 시장에 있는가'가 아니라 '**마지막 적재일보다 뒤인가**'로 잡는다.
        종전 판정(그 날짜가 market_data 에 있는가)만 쓰면 휴장일(9/24·25 처럼 시장 자체가 쉬어
        일봉이 아예 없는 날)도 '미적재'로 분류돼 **없는 날의 행이 저장**된다(실측 22:2x:
        008290 프로브에서 휴장일 2행이 '보존'으로 잡힘). 마지막 적재일 이후만 보존하면 적재
        순서 창(장 종료 후 수급 크론 ~ 일봉 적재)만 정확히 덮는다.
        캐시는 **보수적**으로 동작한다: 실행 중 일봉 적재가 진행돼도 값이 갱신되지 않으므로
        그 날짜는 '미적재'로 판정되어 **보존**된다(버리는 쪽이 아니다).
        """
        if self._market_max is None:
            cur.execute("SELECT MAX(trade_date) FROM market_data")
            row = cur.fetchone()
            self._market_max = row[0].isoformat() if row and row[0] else ""
        return self._market_max

    def collect_ownership(self, stock_code: str, trade_date, excd: str = "J") -> int:
        """현재가 1콜로 외국인 지분율 스냅샷을 받아 ``ownership`` 에 upsert.

        KIS 가 현재 스냅샷만 주므로 ``trade_date`` 는 '그 종목 수급의 최신 거래일'에
        귀속시킨다(리더가 ``trade_date <= 조회일`` 로 조회하므로 과거 조회일에도 잡힌다).
        지분율을 못 받으면 0 을 반환하고 행을 쓰지 않는다(0 을 지어내지 않는다).
        """
        resp = self._client.get_price(stock_code, excd)
        snap = parse_price_ownership(resp)
        pct = snap["foreign_ownership_pct"]
        if pct is None:
            logger.warning("%s 지분율 필드 없음 — 행 생략", stock_code)
            return 0
        cur = self._conn.cursor()
        try:
            cur.execute(OWNERSHIP_UPSERT, (
                str(stock_code), trade_date, pct,
                snap["foreign_held_qty"], snap["listed_shares"],
            ))
            self._conn.commit()
            logger.info("%s 지분율 %s%% (as-of %s)", stock_code, pct, trade_date)
            return 1
        except Exception as e:  # noqa: BLE001
            self._conn.rollback()
            logger.error("ownership 저장 실패 (%s): %s", stock_code, e)
            raise
        finally:
            cur.close()

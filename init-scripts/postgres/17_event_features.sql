-- 이벤트 공시 피처 테이블 (DART 공시 → 이벤트 유형별 롤링 5거래일 카운트)
--
-- WHY(2026-09-25 실측): feature_coverage 에서 event_*_5d 18개 + disclosure_count_5d 가 전부
-- nonzero_ratio=0 이었다. 원인은 데이터 부재가 아니라 ① 수집 범위가 정기공시(A)뿐이었고
-- ② 원천 → 피처 변환 코드가 없었기 때문이다. DART B(주요사항)·D(지분)·E(기타)·I(거래소)
-- 백필로 원천을 채우고, 이 테이블이 그 원천을 모델이 쓸 수 있는 형태로 만든다.
--
-- as-of 규율: 비거래일(주말·휴일) 접수 공시는 **다음 거래일**에 반영한다. 당일 접수분은 당일
-- 거래일에 포함한다(공시는 장중 공개). 미래 누수는 dq_asof_violation_rows 가 감시한다.
CREATE TABLE IF NOT EXISTS event_features (
    stock_code                 VARCHAR(10) NOT NULL,
    trade_date                 DATE        NOT NULL,
    event_capital_increase_5d  INTEGER     NOT NULL DEFAULT 0,   -- 유상증자
    event_cb_bw_5d             INTEGER     NOT NULL DEFAULT 0,   -- 전환사채/신주인수권부사채
    event_contract_5d          INTEGER     NOT NULL DEFAULT 0,   -- 공급계약/수주
    event_delisting_5d         INTEGER     NOT NULL DEFAULT 0,   -- 상장폐지/관리종목/거래정지
    event_disaster_5d          INTEGER     NOT NULL DEFAULT 0,   -- 재해/화재/조업중단
    event_exec_change_5d       INTEGER     NOT NULL DEFAULT 0,   -- 임원·대표이사 변경
    event_litigation_5d        INTEGER     NOT NULL DEFAULT 0,   -- 소송/가처분
    event_mna_5d               INTEGER     NOT NULL DEFAULT 0,   -- 합병/분할/최대주주변경
    event_new_product_5d       INTEGER     NOT NULL DEFAULT 0,   -- 신제품/상용화
    event_partnership_5d       INTEGER     NOT NULL DEFAULT 0,   -- 업무협약/MOU
    event_patent_5d            INTEGER     NOT NULL DEFAULT 0,   -- 특허/상표
    event_realized_5d          INTEGER     NOT NULL DEFAULT 0,   -- 실적/손익구조
    event_recall_5d            INTEGER     NOT NULL DEFAULT 0,   -- 리콜/회수
    event_regulation_5d        INTEGER     NOT NULL DEFAULT 0,   -- 규제/제재/과징금
    event_stake_change_5d      INTEGER     NOT NULL DEFAULT 0,   -- 지분/대량보유 변동
    event_treasury_5d          INTEGER     NOT NULL DEFAULT 0,   -- 자기주식
    disclosure_count_5d        INTEGER     NOT NULL DEFAULT 0,   -- 이벤트성 공시 총건수(정기공시 제외)
    computed_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (stock_code, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_event_features_date ON event_features (trade_date);
CREATE INDEX IF NOT EXISTS idx_event_features_stock ON event_features (stock_code);

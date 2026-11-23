# 백필 커버리지 감사 레시피 (일봉)

"모델 데이터 백필 상태 알려줘" 류 요청에 숫자로 답하기 위해 돌리는 실제 쿼리·프로브.
경로·컨테이너명은 analyist_dd 스택 기준(수집 러너가 다르면 해당 러너 호출로 바꿔 끼운다).

## 1. 기대 거래일 vs 적재일 (핵심 판정)

호스트에 psql 바이너리가 없으면 컨테이너로 넣는다(내부 trust 인증 → 비밀번호 불필요):
`docker exec -i stock_postgres psql -U stock_user -d stock_trading -f - < /tmp/q.sql`

```sql
WITH d AS (
  SELECT gs::date AS day FROM generate_series('2025-09-17'::date,'2026-09-22'::date,'1 day') AS gs
  WHERE EXTRACT(DOW FROM gs) NOT IN (0,6)
), hol AS (
  SELECT h::date AS day FROM unnest(ARRAY['2025-10-03','2026-02-16','2026-02-17','2026-02-18']) AS h
)
SELECT (SELECT count(*) FROM d) AS weekdays,
       (SELECT count(*) FROM d WHERE d.day NOT IN (SELECT day FROM hol)) AS expected_trading_days,
       (SELECT count(DISTINCT trade_date) FROM market_data) AS loaded_days,
       (SELECT count(*) FROM d LEFT JOIN market_data m ON m.trade_date = d.day
          WHERE m.trade_date IS NULL AND d.day NOT IN (SELECT day FROM hol)) AS real_gaps;
```

- 휴장 배열은 `data/krx_holidays.json` 내용을 그대로 붙인다(비었거나 오기록이면 기대일이 틀어진다).
- **`real_gaps = 0` 이고 `expected_trading_days = loaded_days`** 이면 백필 완료 — 이 두 수치를 보고 첫 줄에 쓴다.
- 부분 적재일: `SELECT trade_date, count(*) FROM market_data GROUP BY 1 HAVING count(*) < 2000 ORDER BY 1;`
  (유니버스 ~2,770종목일 때 일 적재 2,550~2,730이 정상.)
- 컬럼명: 종목은 `stock_code`(≠`symbol`), 일자는 `trade_date`(date). 시퀀스는 `id`.

## 2. 결측 평일 분류 — 휴장인가 진짜 공실인가

KIS 전용 프로브(`data_gap.py report`)는 KIS 키가 없으면 후보를 전부 '공실 아님'으로 흘린다.
KRX 2콜 러너로 직접 분류한다 — 휴장 판정·파일 기록까지 러너가 해준다:

```bash
cd /home/jhshi/analyist_dd && set -a; . ./.env; set +a
python3 scripts/krx_daily.py --from 20260717 --to 20260717
```

출력 해석: `시세 없음 → 휴장 기록` = 휴장(holidays 파일에 추가) / `N종목 적재` = 실제 공실이었고 이 실행이 메웠다.
하루치 = 시장 2콜. 셸 루프로 여러 날짜를 돌리면 실행 간 최소 간격이 날짜당 최대 60초를 더한다(9일 ≈ 10분) — 수동 감사는 `--ignore-run-gap`.
이 프로브가 abort(403/401/exit 3)되면 분류가 아니라 **키/승인 문제**로 보고된 뒤 멈춘다.

## 3. 적재 이력 추정 (id 시퀀스)

```sql
SELECT to_char(trade_date,'YYYY-MM') AS mon, min(id), max(id), count(*), count(DISTINCT trade_date)
FROM market_data GROUP BY 1 ORDER BY 1;
```

- 월별 `min(id)`가 1, 8, 26 … 처럼 작고 연속이면 **종목별로 전 날짜를 넣은 벌크 임포트**(한 종목 = 거래일 수만큼 연속 id).
- 날짜당 ~2,700 연속 id 덩어리면 일자별 러너(날짜당 전 종목 upsert)의 적재다.
- `max(id)`가 총 행수보다 크게 높으면 재실행 upsert로 시퀀스만 소모된 것 — 중복 행이 아니다.

종목별 벌크 임포트로 판명되면 **어느 수집기가 넣었는지 컨테이너 로그로 확인한다**:
`docker logs --tail 20 stock_yfinance_collector` → `Daily collection complete. Processed N stocks` + 종목당 `(244 rows)`면 Yahoo(yfinance) 유래다.
이 컨테이너는 **부팅 때마다 전 종목을 다시 돌리므로 장중 재기동 시 오늘의 부분봉까지 `market_data`에 들어간다**(20:00 파이프라인이 그 값을 학습 입력으로 읽는다).
공식 데이터로 통일하려면 KRX 2콜 러너로 기간 전체를 재수집한다(1년 = 247거래일 × 2콜 ≈ 494콜 ≈ 30분).

출처 혼재는 **OHLC 동결 종목**으로 드러난다(Yahoo 유래 행에서 관측). 최근 1개월 기준 직전일과 O/H/L/C가 완전히 같은 날 수를 종목별로 세어 5일 이상이면 소스 품질 플래그로 보고한다:

```sql
WITH w AS (
  SELECT stock_code, open_price, high_price, low_price, close_price,
         lag(open_price) OVER p po, lag(high_price) OVER p ph,
         lag(low_price) OVER p pl, lag(close_price) OVER p pc
  FROM market_data WHERE trade_date >= '<최근 1개월 시작일>' WINDOW p AS (PARTITION BY stock_code ORDER BY trade_date)
)
SELECT stock_code,
       count(*) FILTER (WHERE open_price=po AND high_price=ph AND low_price=pl AND close_price=pc) AS flat_days,
       count(*) AS days
FROM w WHERE pc IS NOT NULL GROUP BY 1 HAVING count(*) FILTER (WHERE open_price=po AND high_price=ph AND low_price=pl AND close_price=pc) >= 5 ORDER BY 2 DESC;
```

3~4종목이 수십 일 동결되는 정도가 Yahoo 유래 구간의 전형이다. 하루 100건 내외의 close 동일(보합)은 정상 범위이니 플래그로 세지 않는다.

## 4. "크론이 안 돌았는지 실패했는지"

래퍼 로그 파일이 **없으면 미실행**(실패면 파일에 에러가 남는다). WSL 호스트는 재부팅으로 크론이 죽는다:

```bash
/mnt/c/Windows/System32/wsl.exe -u root -- journalctl -u cron --since "2026-09-22" --no-pager | tail -20
```

`-- Boot <id> --` 뒤에 `Started cron.service`가 나오면 그 시각 이전의 스케줄은 아예 실행되지 않았다.
이 호스트의 크론 로그는 journald로 간다(`/var/log/syslog` 없음 → grep으로 찾지 말 것).

## 5. 보고 형식 (사용자 선호)

숫자 먼저(기간·기대 거래일/적재일·공실·행수·최신일) → 수집 경로 상태(어느 소스·키 유효성) → 미완 데이터(테이블별 0건이면 명시)
→ 리스크 별도 항목 → 말미에 **번호 매긴 다음 작업 후보 + 권장 1순위**로 승인 요청(열린 질문 하나로 끝내지 않는다).

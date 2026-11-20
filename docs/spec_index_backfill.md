# 스펙 — 지수 일봉 백필 (KOSPI/KOSDAQ) → 레짐 판정 가능하게

작성 2026-11-20. 필요 이유: `etf_premium_analysis.py` 의 `by_regime` 이 **null** 이고
`pairs_arb_research.py` 는 시장 프록시를 breadth(전 종목 수익률 중앙값)로 대신 쓰고 있다.
DB에 **지수 일봉 테이블이 없다**. "패닉일 조건부" 판정(사용자 요구: 하락장에서도 돈이 되는가)을
하려면 실제 지수가 필요하다.

## 1. 신규 `scripts/index_backfill.py`

```
python3 scripts/index_backfill.py [--from 2025-01-01] [--to 2026-11-20] [--codes KOSPI,KOSDAQ] [--dry-run]
```

- 원천: 네이버 `https://api.finance.naver.com/siseJson.naver?symbol=<KOSPI|KOSDAQ>&requestType=1&startTime=YYYYMMDD&endTime=YYYYMMDD&timeframe=day`
  - 응답은 `[['날짜','시가','고가','저가','종가','거래량','외국인소진율'], ...]` 형태의 JSON(작은따옴표 포함) → `ast.literal_eval` 로 파싱.
- 저장 테이블 **신규** `index_daily(index_code TEXT, trade_date DATE, open NUMERIC, high NUMERIC, low NUMERIC, close NUMERIC, volume BIGINT, PRIMARY KEY(index_code, trade_date))`
  - DDL 은 스크립트가 `CREATE TABLE IF NOT EXISTS` 로 수행(기존 테이블 무변경).
- 멱등 upsert(`ON CONFLICT DO UPDATE`), `--dry-run` 은 조회·파싱만 하고 출력.
- 자기신고 1줄: `[claim] index_backfill -> index_daily source=<n> claimed=<n> persisted=<n>`.
- `net_guard` 경유(`scripts/net_guard.py`) — 연속 호출 간 1초 이상, 하루 2종목이므로 부담 없음.

## 2. `etf_premium_analysis.py` 연결 (별도 커밋)

- `--index-code KOSPI` 옵션 추가. `index_daily` 에서 **전일 대비 수익률**을 읽어
  `panic = ret ≤ -1.5%`, `normal = 그 외` 로 episode 를 분류하고 `by_regime` 을 채운다.
- 지수가 없으면 지금처럼 `null` + 사유 문자열 유지(0 채움 금지).

## 3. 테스트 `tests/test_index_backfill.py` (3개)

① 네이버 응답 파싱(작은따옴표·헤더행 포함) ② upsert 멱등(2회 실행 시 행수 불변)
③ 결측 행(빈 값)은 건너뛰고 카운트에 기록

## 4. 검증

```
/usr/bin/python3 -m pytest tests/test_index_backfill.py -q
/usr/bin/python3 scripts/index_backfill.py --dry-run
/usr/bin/python3 scripts/index_backfill.py --from 2025-01-01 --to 2026-11-20
```
- 기대: KOSPI/KOSDAQ 약 450거래일 × 2 = 900행 내외, 2026-11-20 행 존재.

## 5. 금지

- 버전관리 금지. 기존 테이블 쓰기 금지(신규 `index_daily` 만). DB 접속정보는 프로세스 환경변수만.
- 비밀번호 파일 기록·출력 금지. 실주문·트레이더 코드 무접근.

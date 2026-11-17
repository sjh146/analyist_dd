# 스펙 v3 — 분봉 재생으로 '일봉 애매 95.9%' 를 실제 순서로 확정

작성 2026-11-18. **사용자 지시**: "내가 말한대로 트레이더 스케일업 해줘" (측정→배선 원칙).
선행 실측: `data/reports/capital_sim.json` — 자본 제약 시뮬에서 **애매(worst≠best) 95.9%**.
즉 1~3% 좁은 라인은 일봉으로 순서를 알 수 없어 worst −0.74%/건 ~ best +0.71%/건으로 갈린다.
결론을 내려면 **분 단위 실제 경로**가 필요하다.

## 실측으로 확인한 데이터 사실 (재조사 금지)

1. DB `minute_bars`: 2026-09-23~11-17, 300종목/일, **하루 30행(15:01~15:30)** 뿐이다.
2. 원인(코드 확인): `services/kis-collector/kis_app/collectors/minute_collector.py` 의 페이지 종료 조건
   `if len(page_bars) < int(fid_cnt) or oldest == MARKET_OPEN_TIME: break`.
   KIS 당일분봉은 `fid_cnt=100` 을 줘도 **페이지당 30행**만 주므로 이 조건이 **항상 참** → 1페이지만 받고 끝난다
   (KIS_MINUTE_MAX_PAGES=14 를 줘도 무효). 그 결과 40여 세션 동안 마감 30분만 쌓였다.
3. KIS API 는 **당일 데이터만** 제공 → 과거 복구 불가.
4. **Naver 분봉 API 는 과거 다일자를 준다** (2026-11-18 03:50 실측, 005930):
   `https://api.finance.naver.com/siseJson.naver?symbol=005930&requestType=1&startTime=20261101&endTime=20261117&timeframe=minute`
   → 6거래일(11-10~11-17) × **정규장 381분/일**, 총 3,808행, 응답 238KB.
   **한계: OHLC 가 null 이고 `종가`와 `누적거래량`만 온다** → 분봉 종가 경로로 순서를 판정하고,
   고가/저가 부재는 명시적 한계로 기록한다(1분 종가 교차 = 근사).
   요청 범위를 좁히면(예: 5일 창) 더 과거도 받을 수 있는지 코드에서 확인하고, 안 되면 받은 만큼만 쓴다.

## 1. 신규 `scripts/naver_minute_backfill.py`

```
python3 scripts/naver_minute_backfill.py --from 2026-09-23 --to 2026-11-17 \
    [--codes-file ...] [--codes 005930,000660] [--window-days 5] [--sleep 0.5] [--dry-run]
```
- 대상 종목 기본값: `--from`~`--to` 사이 신호 발생 종목(신호 정의는 `capital_sim` 과 동일) 중
  **최대 400종목**(자본 제약으로 실제 체결 가능한 종목 우선 아님 — 신호 전체).
- 요청: 5거래일 창으로 잘라 호출(응답 크기·한도 방어), 창마다 `--sleep`(기본 0.5초).
- 저장: 신규 테이블 `minute_close_bars(stock_code, trade_date, time, close_price, cum_volume,
  source, fetched_at)` — `PRIMARY KEY (stock_code, trade_date, time)`.
  **OHLC 는 저장하지 않는다**(없는 값을 만들지 않는다). `minute_bars` 는 건드리지 않는다.
- 파싱: 응답의 `["YYYYMMDDHHMM", 시가, 고가, 저가, 종가, 누적거래량, ...]` 중 **종가·누적거래량**만 사용,
  정규장(09:00~15:30)만 저장. 중복은 UPSERT(멱등). `--dry-run` 은 저장 없이 건수만 출력.
- 종목별 실패·빈 응답은 로그로 남기고 계속(부분 성공 허용). 마지막에 요약줄:
  `codes=.., days=.., rows_written=.., codes_empty=..`

## 2. 신규 `scripts/minute_replay.py`

```
python3 scripts/minute_replay.py --from 2026-09-23 --to 2026-11-17 [--rule fixed_1_3|fixed_15_45|trail_atr]
    [--equity 245435] [--per-stock-pct 0.3334] [--max-pos 3] [--daily-max 3] [--price-min 1000] [--price-max 50000]
```
- 신호·수량·한도·비용(왕복 0.21%)은 `scripts/capital_sim.py` 의 로직을 **import 해서 재사용**(복붙 금지).
- 경로: 신호일 t 의 다음 거래일(D) **09:00 분봉부터** 순차로 종가를 보며 손절/익절 도달을 판정.
  - 도달 판정: 분봉 `close` 가 임계(진입가×(1±pct))를 **처음 넘은 분**에서 체결(그 분 종가).
  - 같은 분에 둘 다 넘으면 **보수적으로 손절 우선**(그리고 그런 건수를 따로 센다).
  - 당일 15:30 까지 미도달이면 당일 종가 청산(`--same-day-close`, 기본 on) 또는 5세션 보유.
- 분봉 데이터가 없는 (종목,일) 조합은 **건너뛰고 센다**(`skipped_no_minute`), 일봉 대체 금지.
- 산출: 규칙별 `n, win_pct, avg_win_pct, avg_loss_pct, r_multiple, expectancy_pct, expectancy_krw,
  total_pnl_krw, maxdd_pct, ambiguous_same_minute, skipped_no_minute, avg_hold_minutes`.
  **일봉 worst/best 밴드와 나란히 표기**(같은 표에 `daily_worst_ev`, `daily_best_ev` 열).
- 워크포워드: `--from`~`--to` 를 절반으로 나눠 앞/뒤 각각 보고(후자는 재조정 없음).
- 산출물 `data/reports/minute_replay.json` / `.md`(실행 시각 미기록, 멱등).

## 3. 테스트 — `tests/test_minute_replay.py` (신규 6개) + `tests/test_naver_minute_backfill.py`(4개)

- backfill: ① 응답 파싱(종가·누적거래량, OHLC null 무시) ② 정규장 밖 행 제외 ③ UPSERT 멱등
  ④ 빈 응답 종목 카운트
- replay: ① 같은 분 양방향 → 손절 우선 ② 이른 분 익절 → 익절가 체결 ③ 미도달 → 당일 종가
  ④ 분봉 없음 → skipped_no_minute ⑤ 수량·비용 계산이 capital_sim 과 같은 값(경계 1건 대조)
  ⑥ 멱등(같은 입력 → 같은 바이트)

## 4. 검증 (실행 출력 원문 첨부)

```
cd /home/jhshi/analyist_dd
/usr/bin/python3 -m pytest tests/test_minute_replay.py tests/test_naver_minute_backfill.py -q
/usr/bin/python3 scripts/naver_minute_backfill.py --from 2026-11-10 --to 2026-11-17 --codes 005930,096530 --dry-run
/usr/bin/python3 scripts/naver_minute_backfill.py --from 2026-11-10 --to 2026-11-17 --codes 005930,096530
/usr/bin/python3 scripts/minute_replay.py --from 2026-11-10 --to 2026-11-17 --rule fixed_1_3
```
- 백필 요약줄에 실제 저장 행수, 재실행 시 증가 0(멱등) 확인.
- 재생 결과에서 `skipped_no_minute` 와 전체 n 을 비교해 **커버리지 %** 를 md 에 적는다.

## 5. 금지

- `git` 금지(커밋은 오케스트레이터). `.env` 값 출력 금지, 네트워크는 Naver 시세 API 외 금지.
- `minute_bars`·`market_data` **쓰기 금지**. 신규 테이블만 생성.
- 기존 스크립트 수정 금지(import 만). 실주문·브리지·트레이더 접근 금지.
- 데이터가 없는 값을 0/추정으로 메우지 말 것. 커버리지가 낮으면 낮다고 쓴다.
- 사후에 규칙·임계값을 바꾸지 말 것.

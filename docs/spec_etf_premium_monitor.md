# 스펙 — 레버리지 ETF 괴리율 계측기 (전략 ①, 무자본 리서치)

작성 2026-11-18. 사용자 지시: "쌍둥이/차익거래/레버리지 ETF 왜곡" 질문 → 검증 결과 **매매가 아니라 계측부터**.
목표: 레버리지·인버스 ETF의 **시장가 vs NAV(괴리율)** 를 장중 스냅샷으로 축적해
① 괴리 크기·지속시간 분포 ② 평균회귀 반감기 ③ "괴리 발생 후 N분 수익률"의 기대값을 실측한다.
**주문·브리지 무접근, DB 새 테이블 쓰기만.**

## 데이터 소스 (실측 확인 2026-11-18)

`GET https://finance.naver.com/api/sise/etfItemList.nhn` → **1콜로 전 종목 ETF** 를 준다:

```json
{"resultCode":"success","result":{"etfItemList":[
  {"itemcode":"360750","itemname":"TIGER 미국S&P500","nowVal":24305,"changeRate":-1.02,
   "nav":24243.0,"quant":23719509,"amonut":576256,"marketSum":203445}]}}
```
- `nowVal` = 시장가(원), `nav` = NAV(원) → **괴리율 = (nowVal/nav − 1)×100**.
- 응답 본문은 **EUC-KR**(종목명 깨짐) → `bytes.decode("euc-kr")` 후 `json.loads`. UTF-8 로 읽으면
  `UnicodeDecodeError` 또는 종목명 깨짐(실측).
- 키·인증 불필요, 스로틀 없음. 대신 **호출 정책은 `scripts/net_guard.py`** 를 통과시킨다(공유 상태파일).
- 유니버스: 위 목록에서 `itemname` 에 `레버리지` 또는 `인버스` 포함(현행 336종목 규모, DB `stocks` 와 무관하게 목록에서 직접 판정).

## 1. 신규 `scripts/etf_premium_collect.py`

```
python3 scripts/etf_premium_collect.py [--once] [--interval 60] [--window 09:00-15:30]
                                       [--min-premium-log 0.3] [--no-db] [--jsonl-out DIR]
```
- 1회 실행 = 스냅샷 1개. `--interval` 을 주면 `--window` 안에서 반복(기본 `--once`, 크론이 매분 호출).
- 시장 시간 밖이면 **아무 것도 하지 않고 exit 0** (`--window` 판정, 휴장일은 `data/krx_holidays.json`).
- 저장(2경로, 둘 다 멱등):
  1. DB `etf_premium_snapshots`(없으면 `CREATE TABLE IF NOT EXISTS`):
     `ts timestamptz, trade_date date, code text, name text, price numeric, nav numeric,
      premium_pct numeric, volume bigint, amount bigint, source text DEFAULT 'naver_etflist'`,
     `PRIMARY KEY (ts, code)` + `INSERT ... ON CONFLICT DO NOTHING`.
  2. `data/etf_premium/YYYY-MM-DD.jsonl` (한 줄 = 한 스냅샷; `--no-db` 면 이 경로만).
- `--min-premium-log 0.3`: 콘솔에는 |괴리|≥0.3% 인 종목만 요약 출력(로그 폭주 방지), 저장은 전량.
- 자기신고: `{"ts","n_etfs","n_saved","db":true/false,"jsonl":path,"elapsed_ms","net_guard":"ok|disabled"}` 1줄.
- 실패는 fail-open: 네트워크·파싱 실패 시 exit 0 + `error` 필드 기록(크론 중단 방지), 3회 연속 실패면 exit 1(알림 대상).

## 2. 신규 `scripts/etf_premium_analysis.py`

```
python3 scripts/etf_premium_analysis.py --date 2026-11-19 [--min-snapshots 60]
        [--premium-threshold 0.5] [--horizons 5,15,30,60]
        [--json-out data/reports/etf_premium.json] [--md-out data/reports/etf_premium.md]
```
- 입력: DB(없으면 jsonl). **당일 스냅샷만** 사용(장중).
- 산출:
  1. `by_code`: 종목별 `n, premium_mean, premium_p50, premium_p05, premium_p95, |premium|≥0.5% 체류비율,
     premium 시계열의 AR(1) 계수와 **반감기(분)**(= ln0.5/ln|φ|, φ≥1이면 null), volume`
  2. `episodes`: |괴리| ≥ threshold 가 **연속 유지된 구간**(시작·끝·최대괴리·지속분). 같은 종목이 3분 이내 재발하면 병합.
  3. `event_study`: 각 episode 시작 시점 기준 **+5/+15/+30/+60분 가격 수익률**(그 시점 스냅샷 가격 대비, 없으면 null)
     → 평균·중앙값·승률·표본수를 `horizons` 별로. **진입 규칙 후보 판정용**(예: 디스카운트 −1% 이하에서 매수 시 +30분 기대값).
  4. `by_regime`: 당일 KOSPI/KOSDAQ 지수 등락(있으면 `market_data` 의 지수 코드 또는 `macro` 테이블)로
     `panic(|지수 등락| ≥ 1.5%)` vs `normal` 분리해 위 3번을 다시 집계. 지수 데이터 없으면 `regime: null` + 사유.
- 출력 JSON 은 멱등(실행 시각 미기록, `date` 만 키). 금액 단위는 원.
- 하루 표본이 `--min-snapshots` 미만이면 **수치 대신 `insufficient_sample` + 사유**를 쓴다(0으로 메우지 않는다).

## 3. 크론 (평일 09:00–15:30 매분)

```
* 9-15 * * 1-5 cd /home/jhshi/analyist_dd && set -a; . ./.env; set +a; POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434 /usr/bin/python3 scripts/etf_premium_collect.py --once >> reports/cron_etf_premium.log 2>&1
30 16 * * 1-5 ... /usr/bin/python3 scripts/etf_premium_analysis.py --date $(date +\%F) >> reports/cron_etf_premium.log 2>&1
```
(크론 등록은 오케스트레이터가 한다 — 스크립트만 만들 것. 스크립트 자체가 `--window` 로 09:00–15:30 밖을 거른다.)

## 4. 테스트 — `tests/test_etf_premium.py` (신규 7개)

① EUC-KR 응답 파싱(종목명 한글 정상) ② 괴리율 계산(nowVal 100/nav 99 → +1.0101%) ③ 레버리지/인버스 필터
④ DB 적재 멱등(같은 ts·code 2회 → 1행) ⑤ episode 병합(3분 이내 재발 → 1건) ⑥ 반감기 계산(φ=0.9 → 약 6.6분)
⑦ 표본 부족 시 `insufficient_sample` 반환(수치 없음)

## 5. 검증 (실행 출력 원문 첨부)

```
/usr/bin/python3 -m pytest tests/test_etf_premium.py -q
/usr/bin/python3 scripts/etf_premium_collect.py --once --no-db --jsonl-out /tmp/etf_prem_test
wc -l /tmp/etf_prem_test/*.jsonl && head -2 /tmp/etf_prem_test/*.jsonl
/usr/bin/python3 scripts/etf_premium_analysis.py --date 2026-11-18 --json-out /tmp/ep.json --md-out /tmp/ep.md || true
```
- 1회 실행에서 **레버리지/인버스 ETF 수가 100건 이상**인지 확인(목록 실측 기준).
- 장 마감 후 실행이면 `--window` 로 인해 저장 0건 + exit 0 이 정상(그 사실을 자기신고에 남긴다).

## 6. 금지

- 주문·브리지·트레이더 코드 무접근. 기존 스크립트 수정 금지(import 만). 기준선 산출물 덮어쓰기 금지.
- DB 는 **새 테이블 `etf_premium_snapshots` 쓰기만** 허용(기존 테이블 쓰기·DDL 금지).
- 버전관리 명령 금지, 리포 밖 파일 수정 금지. 결측을 0으로 메우지 말 것(null + 사유).

모든 검증 통과. 구현·검증 완료 보고입니다.

---

# 구현 보고 — 레버리지 ETF 괴리율 계측기 (spec_etf_premium_monitor.md)

## 1. 생성 파일 (신규 4종 + 산출물)

| 파일 | 내용 |
|---|---|
| `scripts/etf_premium_collect.py` | §1 수집기. EUC-KR 파싱, 창/휴장일 판정, DB+jsonl 2경로 멱등 저장, fail-open(3연속 실패→exit 1), net_guard 통과, dq_claim 3분리 자기신고 |
| `scripts/etf_premium_analysis.py` | §2 분석기. by_code(평균·p05/p50/p95·체류비율·AR(1) 반감기)·episodes(3분 병합)·event_study(+5/15/30/60분)·by_regime |
| `tests/test_etf_premium.py` | §4 계약 7개(합성 입력·sqlite in-memory — 운영 DB 무오염, 형제 테스트 관례) |
| `data/reports/etf_premium.json` / `.md` | 산출물(오늘은 1스냅샷 → 정직한 `insufficient_sample`) |
| `data/etf_premium/2026-11-18.jsonl` | 수집기 기본 경로 산출(한 줄=한 스냅샷) |
| DB `etf_premium_snapshots` | 스펙 스키마 그대로 DDL + 102행(검증 실행분) |

기존 파일 수정 없음, git 명령 실행 안 함, 크론 등록 안 함, 주문·브리지·트레이더 코드 무접근.

## 2. 실측 근거 (docstring 에 조회 명령과 함께 기록)

- **API**: `curl 'https://finance.naver.com/api/sise/etfItemList.nhn'` → HTTP 200, 265,885B, `charset=EUC-KR` → `bytes.decode("euc-kr")` 필수. 전 ETF **1,190종목**, 레버리지/인버스 **102종목**(≥100 충족). 필드 결측 0건, `quant`는 당일 누적(→ by_code `volume_last_cum` 해석 근거).
- **지수 데이터 없음** → §2.4 `by_regime: null + 사유`: `market_data` 지수 코드(`KOSPI/KOSDAQ/1001/2001`) **0행**, `macro_indicators` 9종(금리·환율·물가)만 존재.
- **휴장일**: `data/krx_holidays.json` (2026-11-18 은 거래일, `market_data` max=2026-11-18 확인).
- **시각**: 호스트 로컬 KST(`date +%Z`).

## 3. 실행 중 발견·수정한 버그 (정직 공개)

1. **`.env` 소싱 시 DB 이름해석 실패** — 컨테이너 좌표(`POSTGRES_HOST=postgres`)가 호스트 프로세스에 물려 `could not translate host name "postgres"` 실측. `dq_claim._conn_target` 과 동일 규칙(컨테이너 밖이면 `127.0.0.1:POSTGRES_HOST_PORT`)을 두 스크립트 `connect()` 에 반영. 실패 자체는 fail-open 경로를 실증했다(jsonl 보존+error 필드+exit 0, 후속 성공 시 연속실패 카운터 리셋 — `data/state/etf_premium_collect_state.json` 현재 0).
2. **psycopg2 `numeric` → `Decimal` JSON 직렬화 실패** — 실측 후 로드 시점 float 정규화.
3. **DB 경로 타임스탬프 UTC 표기** — 로컬(KST) 정규화로 jsonl 경로와 표기 일치(입력원이 달라도 산출 동일).

## 4. 검증 출력 원문 (스펙 §5)

```
$ /usr/bin/python3 -m pytest tests/test_etf_premium.py -q
.......                                                                  [100%]
7 passed, 4 warnings in 0.41s
```
(경고 4건은 테스트④ in-memory sqlite 의 datetime 어댑터 DeprecationWarning — 테스트 하네스 한정, 코드 결함 아님)

```
$ /usr/bin/python3 scripts/etf_premium_collect.py --once --no-db --jsonl-out /tmp/etf_prem_test
{"ts": "2026-11-18T23:57:57+09:00", "n_etfs": 0, "n_saved": 0, "db": false, "jsonl": "/tmp/etf_prem_test/2026-11-18.jsonl", "elapsed_ms": 0, "net_guard": "ok", "reason": "market_closed"}
exit=0
```
장 마감(KST 23:57) → 창 밖 정상 경로: 저장 0건 + 자기신고에 `reason: market_closed` + exit 0 (스펙 §5 예고대로).

```
$ wc -l /tmp/etf_prem_test/*.jsonl; head -2 /tmp/etf_prem_test/*.jsonl
    2 /tmp/etf_prem_test/2026-11-18.jsonl
{"ts": "2026-11-18T23:55:25+09:00", "trade_date": "2026-11-18", "n": 102, "rows": [{"code": "122630", "name": "KODEX 레버리지", "price": 54400.0, "nav": 54606.0, "premium_pct":
...
```

```
$ /usr/bin/python3 scripts/etf_premium_analysis.py --date 2026-11-18 --json-out /tmp/ep.json --md-out /tmp/ep.md || true
[etf_premium] date=2026-11-18 insufficient_sample (n_snapshots=1)
exit=0
```
`/tmp/ep.json` = `{"date": "2026-11-18", "insufficient_sample": {"min_snapshots": 60, "n_snapshots": 1, "n_codes": 102, "reason": "당일 스냅샷 1개 < 60(--min-snapshots) — 수치 산출 보류(0으로 메우지 않음)"}}` — 실행 시각 미기록, date 만 키.

**장 마감이라 §5 만으로는 저장 경로가 안 보이므로** `--window 00:00-23:59` 확장 실행으로 저장 경로를 추가 실증했습니다:

```
{"ts": "...23:55:25+09:00", "n_etfs": 102, "n_saved": 102, "db": false, "jsonl": "...", "elapsed_ms": 250, "net_guard": "ok", "n_source": 1190}
```
DB 실행(테이블 DDL+적재+자기신고) 후 검증:
- `SELECT` → 102행, distinct_ts 1, trade_date 2026-11-18; 최대괴리 `TIGER 미국필라델피아반도체레버리지(합성) +3.7585%`
- **자기신고 3분리**: `dq_runner_claim` = `('etf_premium_collect','etf_premium_snapshots', claimed=102, persisted=102, source=1190)` (소스 수신/파서 생성/실제 저장)
- **PG 멱등 실증**: 같은 (ts, code) 102행 재적재 → 신규 **0행** (`ON CONFLICT DO NOTHING`)
- **jsonl 멱등 실증**: 같은 ts 재실행 → 2회차 append `False`, 줄 수 그대로
- 전체 산출 경로(`--min-snapshots 1`): by_code 102종목, episodes 35건, event_study `n=0`+null(미래 스냅샷 없음 — 0 채움 금지), `by_regime: null`+실측 사유, KST 표기 정상

## 5. 산출물 현재 내용 (정직한 오늘 상태)

`data/reports/etf_premium.md` = **insufficient_sample**(오늘 스냅샷 1개 < 60) — 내일부터 크론이 쌓이면 자동으로 수치로 전환됩니다.

참고: 규칙 ⑧(리포 밖 금지)과 관련해 `/tmp` 경로는 스펙 §5 검증 명령 자체가 지정한 곳이라 그대로 사용했고(사용자가 원문 실행을 명시 지시), 구현 산출물은 전부 리포 안입니다.

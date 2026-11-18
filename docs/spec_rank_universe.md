# 스펙 — 크레온 상승률 랭킹을 단타 유니버스로 압축 (rank_universe)

작성 2026-11-18. **사용자 선택 2번(2026-11-18 확정)**: 크레온 상승률 랭킹을 **① 추격 배제 필터**와
**② 대역 가점**으로만 쓰고, 매수 판단(칼만 추세·챔피언 모델·거래량·변동성·엣지 게이트)은
기존 단타스크리너·트레이더 규칙을 그대로 유지한다.

> 선택 1번(랭킹 ∩ 스코어 = 유니버스 압축)을 실제로 돌려본 결과 **교집합 0건**이었고
> 압축 후보 최고점 55.1 < 전체 65.5 로 "압축"이 아니라 "대체"가 됐다
> (`data/reports/rank_universe_overlap.json`). 원인: 스크리너 상위는 20일 칼만 추세+모델로
> 뽑혀 당일 등락률과 무관하다. 그래서 교집합 대신 배제+가점으로 바꿨다.

## 배경(실측)

- 크레온 `CpSvrNew7043`(상승률순위)은 이미 `bridge/signals.py::fetch_rank` 로 이식돼 있고
  `GET /rank` 로 조회된다(오늘 14:57 WSL 에서 호출 성공: `pct_from=1&pct_to=10&pages=2` → 60건).
- 그러나 라이브 루프는 `--feed .../screener_latest.json`(analyist_dd 피드)을 쓰므로
  이 랭킹은 **매매에 전혀 쓰이지 않았다**(`rankfeed.py` 경로는 `--feed rank` 전용).
- 단타스크리너는 KOSDAQ 전체(20거래일 이상)를 점수화하므로, 상승률 랭킹과 교집합을 취하면
  "오늘 실제로 움직이는 종목"으로 유니버스를 줄일 수 있다.

## 1. 신규 `scripts/rank_universe.py`

```
python3 scripts/rank_universe.py [--pct-from 2] [--pct-to 12] [--pages 7]
                                 [--direction up] [--market 0]
                                 [--bridge-url http://127.0.0.1:8100]
                                 [--out data/state/rank_universe.json] [--quiet]
```
- `GET {bridge}/rank?direction=up&period=day&market=0&pct_from=..&pct_to=..&pages=..` 호출
  (읽기 전용, 주문·잔고 미접근).
- 응답 `rank: [{code,name,diff,diff_sign,volume,rank}]` → 코드 정규화(6자리 숫자만, 중복 제거,
  `A` 접두 제거) 후 **랭킹 순서 유지**.
- 출력(원자적 쓰기: tmp + `os.replace`):
  ```json
  {"ok": true, "generated_ts": "2026-11-18T15:02:11+09:00", "params": {...},
   "codes": ["003780", ...], "n_codes": 60,
   "items": [{"code","name","diff","volume","rank"}],
   "claim": {"source_rows": 60, "claimed_rows": 60, "persisted_rows": 60}}
  ```
- **fail-open**: 브리지 미접속·타임아웃·`ok:false`·빈 랭킹이면 `ok:false` + `reason` + `codes: []`
  를 쓰고 **exit 0**(크론 체인을 끊지 않는다). `--strict` 를 주면 exit 1.
- 실행 시각이 결과 바이트를 바꾸지 않도록 `generated_ts` 외에는 비결정 요소를 넣지 않는다
  (멱등: 같은 입력 → 같은 `codes`/`items`).

## 2. 단타스크리너 대역 적용 (기존 계약 불변)

- `scripts/day_trading_engine/pipeline.py::run_screener(..., allow_codes: Optional[set] = None)`:
  유니버스 조회 직후 `allow_codes` 로 교집합. 로그:
  `유니버스 압축: 1,7xx → 43 종목 (rank_universe)`.
  `allow_codes` 가 None/빈 집합이면 **기존과 완전히 동일**(전체 유니버스).
- `scripts/daytrading_screener.py --rank-band-file PATH` (정본):
  · `excluded_codes`(+12~30%) → 유니버스에서 **제외**(추격 방지)
  · `codes`(2~12%) → **정렬 전용 가점**(`--rank-bonus`, 기본 +5.0). 공개 `score` 는 불변
  · 파일 없음/파손 → 대역 없이 실행(fail-open, 경고 로그)
- `--universe-file PATH` 는 (구)압축 모드로 남겨 두되 **크론에는 쓰지 않는다**.
- 점수·필터·출력 계약(OUTPUT_COLUMNS, score_kind="composite")은 변경 금지

## 3. 크론 `cron/daytrading_feed_intraday.sh` (평일 09:05–14:05 매시 5분, 등록 완료)

1. `rank_universe.py` 실행(실패해도 계속).
2. `daytrading_screener.py --top-n 20 --rank-band-file data/state/rank_universe.json --json-out reports/daytrading_latest.json`
3. 후보가 **0건**이면 한 번 더 제한 없이 재실행(폴백 로그), 그래도 0건이면 **발행 중단**(기존 피드 유지).
4. `feed_export.py --daytrading ...` 로 발행(08:35 크론과 동일 경로·동일 인자).
- 로그: `reports/cron_daytrading_intraday.log`.

## 4. 테스트 — `tests/test_rank_universe.py` (신규 6개)

① 정상 응답 파싱·코드 정규화(중복·`A`접두·비숫자 제거) ② 랭킹 순서 보존
③ 브리지 실패(예외) → `ok:false` + exit 0 ④ `ok:false` 응답 → `ok:false` + `codes:[]`
⑤ 멱등(같은 입력 2회 → `codes`/`items` 동일) ⑥ `--strict` 실패 시 exit 1

## 5. 검증 (출력 원문 첨부)

```
python3 -m pytest tests/test_rank_universe.py -q
python3 scripts/rank_universe.py --pct-from 2 --pct-to 12 --pages 7 && echo OK
python3 -c "import json;d=json.load(open('data/state/rank_universe.json'));print(d['ok'],d['n_codes'],d['codes'][:5])"
# 유니버스 제한 스크리너(dry: JSON 만 생성)
python3 scripts/daytrading_screener.py --top-n 20 --universe-file data/state/rank_universe.json --json-out /tmp/dt_restricted.json && python3 -c "import json;print(len(json.load(open('/tmp/dt_restricted.json'))['items']))"
```
기대: `ok true`, `n_codes>0`, 제한 스크리너 후보 수 ≤ 20, 전체 유니버스 실행 대비 **유니버스 축소 로그** 확인.

## 6. 금지

- `git` 금지(커밋은 오케스트레이터). 실주문·주문취소·잔고 API 호출 금지(`/rank` 읽기만).
- 트레이더 규칙·프로파일·엣지 문턱 변경 금지. `feed_export.py` 계약(키·의미) 변경 금지.
- 기존 테스트 기대값 수정 금지. DB 쓰기 금지(SELECT·파일 산출만).

## 7. 지연시간 최적화 (2026-11-18 실측, 출력 비트 동일 확인)

| 단계 | 이전 | 이후 | 방법 |
|---|---|---|---|
| 룩백 조회 | 3.68s | **0.37s** | `ROW_NUMBER()` 전체 322만행 정렬 → `CROSS JOIN LATERAL … LIMIT n`(종목별 인덱스) + 유니버스 코드만 |
| 칼만 계산 | 2.4s (1,858종목) | **0.9s** (669종목) | 유동성·가격 사전 필터(`prefilter_liquid`)로 대상 축소 — 사후 `filter_candidates` 유지 |
| 모델 예측 | 1,858회 단건 호출 | **1회 배치** | 특징행렬을 한 번 만들어 `ensemble.predict(X)`(실패 시 행별 폴백) |
| **전체 실행** | **8.17s** | **2.65s** | 위 3개 합 |

- 후보 20건의 코드·점수가 최적화 전후 **완전 동일**(`/tmp/dt_full_timed.json` vs `/tmp/dt_opt4.json`).
- 장중 재발행 체인(랭킹 2회 조회 + 스크리너 + 발행) 실측 **약 3.6초**(dry-run 15:13).

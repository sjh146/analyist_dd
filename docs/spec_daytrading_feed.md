구현 완료. 보고입니다.

# 구현 보고 — 피드 daytrading 경로 (spec_daytrading_feed.md)

## 변경 파일 (3개 — 스펙 그대로, 그 외 수정 없음)

| 파일 | 변경 |
|---|---|
| `scripts/feed_export.py` | ① `--daytrading` 인자(기본 `reports/daytrading_latest.json`) ② `sources`/발행 루프에 `daytrading` 추가 ③ `score_kind="composite"` + calibrated_prob 변환 금지 + `model_prob` 분리 보존(null 유지) ④ `slope_permille`·`volume_ratio` 전달 ⑤ 자기신고 추가 |
| `scripts/daytrading_screener.py` | `--json-out` 추가 + `build_json_payload`/`write_json_out`(원자적 tmp+replace) + 자기신고. CSV 동작 불변 |
| `tests/test_feed_daytrading.py` | 신규 — 6개 테스트 (① 키 포함 ② 필수 필드 ③ composite ④ close/swing 불변 ⑤ json-out 스키마 ⑥ 라이브 피드 검증) |

## 실측 근거 (추측 없음, 조회 명령 기록)

- **spec 4-5행**: 저널 실측 decisions = swing 2,483건 · daytrading 0건 — 피드에 daytrading 키가 없어 평가조차 못 함.
- **`head -5 data/reports/daytrading_candidates_20261007_125544.csv`**: 당일 실제 산출물 1~3행 (104200 NHN벅스 2705.0/64.7/3.835‰/4.6배, 053800 안랩 90200.0/64.7, 101330 모베이스 4130.0/64.5). **model_prob 전부 빈 값(모델미가용)** → null 보존 경로 필수 확인. 테스트 픽스처 값의 출처.
- **`data/feed/screener_latest.json` 조회**: candidates = {close, swing} 뿐, `reports/daytrading_latest.json` 부재 확인.
- **`data/krx_holidays.json` 조회**: 2026-10-07 휴장일 아님.

## 스펙 대비 판단 사항 (보고)

1. **`valid_until`**: daytrading은 close와 동일하게 **발행일 15:30** 창. 실측 산출물의 signal_date=10-06(직전 거래일 일봉 기준)인데 매수는 당일이므로 신호일 고정은 전 후보 즉시 만료가 됐을 것. swing의 +5일 창을 물려받으면 사흘 전 단타 신호가 신선해 보이는 문제도 차단.
2. **후보 0건 시에도 `--json-out`은 빈 items로 갱신**: 이전 산출물이 남아 feed_export 신선도 게이트(generated_at)를 통과해 "어제 후보"가 발행되는 경로 차단.
3. **라이브 피드 재발행은 하지 않음** — dry-run으로만 검증(20:30 크론이 발행할 것). `reports/daytrading_latest.json` 스모크 산출물은 삭제함.

## 자기신고 (3분리, dq_claim.record_claim 재사용)

- `feed_export` → `runner="feed_export"`, `table_name="screener_feed"`: source=소스 후보 합 / claimed=build_items 합(게이트 전) / persisted=최종 발행 합. DB 미연결 시 WARN만, 발행 불변(dq_claim 규약). 빈 실행·dry-run은 미기록.
- `daytrading_screener` → `runner="daytrading_screener"`, `table_name="daytrading_candidates"`: source=엔진 산출 / claimed=파서 생성 / persisted=실제 저장.

## 검증 출력 (실행 결과)

```
$ python3 -m pytest tests/test_feed_daytrading.py -q
.....s   [100%]
5 passed, 1 skipped in 0.69s
```
skip 1건 = 라이브 피드에 daytrading 미발행 시 skip하도록 설계한 라이브 검증(발행 크론 가동 후 자동 활성화).

```
$ python3 -m pytest tests/test_feed_export_contract_v2.py tests/test_feed_export_holiday_guard.py tests/test_daytrading_screener_engine.py -q
32 passed in 0.79s      # 기존 계약/휴장/엔진 테스트 전부 통과
```

**close/swing 비트 동일 실증** (dry-run vs 14:40 라이브 발행본, valid_until 제외 전체 항목 딥 비교 — DB 자격증명이 없어 종가는 라이브 발행이 DB에서 가져온 값으로 에뮬레이션):
```
close | dry vs live identical: True | dry: 20 | live: 20
swing | dry vs live identical: True | dry: 20 | live: 20
daytrading items: 5 | codes: ['104200','053800','101330','277880','101170']
score_kinds: {'composite'} | valid_until: 2026-10-07T15:30:00+09:00
```

## 메모

- git: 커밋/푸시 없음. 상태 확인용 `git status --short`(읽기 전용) 1회만 사용.
- 멱등성: 발행은 원자적 `os.replace`, json-out도 tmp+replace — 재실행 안전. 자기신고는 러너 공통 규약대로 1회 실행 1행 append.
- 스모크 중 실측: 코드 053800 → int 파싱 시 6자리 게이트가 올바르게 제외함(게이트 동작 확인, 산출물 자체는 문자열 코드라 무관).

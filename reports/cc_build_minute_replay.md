백필은 ~15/295종목 진행 중(약 40분 예상). 완료 알림이 오면 재생 스크립트를 다시 실행해 실제 커버리지 결과를 뽑겠습니다.

**지금까지 진행 상황:**

| 단계 | 상태 |
|---|---|
| 신규 스크립트 2개 (`naver_minute_backfill.py`·`minute_replay.py`) | ✅ 작성 완료 |
| 신규 테스트 2개 (10건) | ✅ **10 passed** |
| §4 검증: 백필 dry-run (005930·096530) | ✅ 6,593원천행 → 4,535 정규장 행 |
| §4 검증: 백필 실적재 | ✅ rows_written=4,535, claim source=6593/claimed=4535/persisted=4535 |
| §4 검증: 백필 재실행(멱등) | ✅ rows_written=0, persisted=0 |
| §4 검증: 재생 실행 (fixed_1_3) | ✅ 파이프라인 정상 — 다만 분봉 2종목뿐이라 커버리지 0.0% (n=0, skipped_no_minute=303 정직 집계) |
| 전체 신호 종목(295개 ≤ 400) 백필 | ⏳ 백그라운드 실행 중 (~40분) |
| 백필 완료 후 재생 재실행 → 실제 커버리지 산출 | 대기 중 |

산출물 `data/reports/minute_replay.json`/`.md` 구조(claim 3분리·일봉 worst/best 밴드 병기·전체/앞/뒤 3구간)는 이미 검증되었습니다. 기존 파일 수정 0건, git 명령 0회, `minute_bars`·`market_data` 쓰기 0회입니다.

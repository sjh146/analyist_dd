# analyist_dd 운영 사고 카탈로그

증상 → 원인 → 탐지 → 재발 방지 순서. 전부 실측(2026-11-21~23).

## 1. 같은 스크립트가 호스트는 차단, 컨테이너는 통과
- 증상: 11-23 09:10 파이프라인이 risk_off 인데도 단타 후보 20건 발행. 호스트 11:05 실행은 0건(`regime_off`).
- 원인: `scripts/`는 bind mount라 코드는 같은데, 게이트 입력 `data/state/market_regime.json` 이 컨테이너에 없어 `_load_regime()` 이 None → fail-open.
- 탐지: 같은 분에 호스트/컨테이너 실행 결과 비교. 컨테이너에 `test -f /app/data/state/market_regime.json`.
- 방지: compose 에 `./data/state:/app/data/state` 마운트 + 래퍼가 매 실행 가시성 확인 후 WARN.

## 2. 설정 tuple 이 문자열로 붕괴
- 증상: risk_off 기본 보수·상승장 확장·급등 게이트 목록이 전부 비활성. 로그 `state=risk_off` 인데 guard reason `risk_off 아님`.
- 원인: `Config.with_overrides` 가 `as_dict()` 를 거치며 tuple 필드를 `str()` 로 변환 → `"('risk_off',)"`. `state in states` 가 항상 False.
- 탐지: `RunnerConfig().engine_config()` 에서 `isinstance(cfg.risk_off_states, tuple)` 확인.
- 방지: `_coerce` 에 tuple 분기(`_as_strtuple`) + 회귀 3건. 설정 필드 타입을 테스트로 고정.

## 3. 테스트 러너의 거짓 통과
- 증상: root-tests 4건 실패인데 러너는 “4스위트 통과”로 보고.
- 원인: 테스트 개수 `skipped=4` 를 “스위트 건너뜀”으로 오독.
- 탐지: 저장된 JSON 의 `results[*].rc` 와 totals 불일치 확인.
- 방지: `suite_skipped` 로 키 분리, 회귀 테스트.

## 4. 환경 의존 테스트 4건
- 증상: 같은 코드가 4건/5건으로 다르게 실패.
- 원인: (a) 러너가 `.env` 미로딩 → DB 비밀번호 누락, (b) `today-29` 가 주말, (c) 픽스처 날짜 하드코딩으로 `today_closed` 불일치, (d) XR26 이후 “짧은 배치=종료” 규약 변경 미반영.
- 탐지: `.env` 유무/요일을 바꿔 재실행.
- 방지: 러너가 `.env` 자체 로드(+호스트 포트 강제), 픽스처는 거래일·상대 날짜, 규약 변경 시 테스트 이름에 근거 명시.

## 5. 시한부 청산이 이긴 포지션을 끊음
- 증상: 195940 을 90분 규칙으로 38,100(+0.16%)에 청산. 이후 고점 38,450(+1.4%).
- 원인: `max_hold_minutes=90` 이 수익 여부를 보지 않음.
- 탐지: 청산 사유별 사후 고점 대비 반납 측정.
- 방지: 수익 중 + 진입 시점 지수보다 지금 지수가 높으면 240분까지 연장(손익 0 이하로 회귀하면 즉시 청산), 단타 트레일링 +1%/−0.8%.

## 6. .env 의 DB 값은 컨테이너 기준
- 증상: 호스트 스크립트가 `could not translate host name "postgres"` 또는 `no password supplied`.
- 원인: `.env` 는 `POSTGRES_HOST=postgres`, `5432`. 호스트는 매핑 포트 `127.0.0.1:5434`.
- 탐지: 스크립트가 자체 기본값을 쓰면 connection 에러 문구에 호스트명이 찍힌다.
- 방지: 호스트 실행 래퍼는 항상 `POSTGRES_HOST/PORT` 를 덮어쓴다. 러너도 동일.

## 7. WSL interop 사망
- 증상: `<3>WSL ... UtilAcceptVsock:273: accept4 failed 110`. `cmd.exe`/`curl.exe`/`wsl.exe` 호출 전면 실패.
- 원인: WSL↔Windows 통신 브로커 이상(재기동 계열 사고 후).
- 탐지: `cmd.exe /c echo ok` 실패.
- 방지: 루프 정지는 파일 기반(kill_switch.txt)으로 하고, 복구는 Windows 에서 `wsl --shutdown`.

## 8. WSL 메모리 소진 종료
- 증상: 로그 단절, lifecycle 원장에 `exited UNCLEANLY (SIGKILL / OOM / VM death)`.
- 원인: `.wslconfig` 메모리/스왑 부족(당시 mem_available 159MB, swap 2GB 100%).
- 탐지: 부팅훅의 swap 추가 로그, watchdog exit 75.
- 방지: memory=12GB/swap=8GB, 컨테이너 수 상한, 장시간 작업 분할.

## 9. DART 백필 상태파일 공유
- 증상: 8월 반기에 완료된 1,702종목이 11월 3분기에서 “완료”로 스킵(대상 997로 축소).
- 원인: 보고서 코드 구분 없는 단일 `backfill_state.json`.
- 탐지: 보고서코드별 대상 수 vs DB 적재 수 비교.
- 방지: `backfill_state_<code>_<year>.json` 분리 + 제출창에만 도는 크론.

## 10. UTC 날짜가 하루 밀림
- 증상: 월요일 08:30 스윙 job 산출물 `signal_date=일요일`.
- 원인: 컨테이너 TZ=UTC 에서 `date.today()`.
- 탐지: 관문 G5(signal_date 거래일 검사).
- 방지: `_signal_date()` = DB `max(trade_date)`, 실패 시 KST 달력일.

## 11. 미러된 벤더 코드가 커밋 훅을 막음
- 증상: 스킬 미러 커밋이 C001/C003 위반으로 거부.
- 원인: `config/skills/**` 의 스킬 부속 스크립트는 리포 코드 규칙 대상이 아니다.
- 탐지: 훅 출력의 경로.
- 방지: 린트 `SKIP_DIR_PARTS` 에 `skills` 추가 + `--files` 경로에도 동일 필터.

## 12. 수집 단계 자기신고 미배선
- 증상: 저녁 파이프라인 컨테이너 phase 가 실패해도 다음 날까지 침묵.
- 원인: `docker exec` 경로는 러너 래퍼(dq_claim)를 쓸 수 없다.
- 탐지: `dq_runner_claim` 에 해당 runner 행이 없음.
- 방지: 호스트가 phase 전/후 테이블 count 를 재고 `phase_claim.py` 로 행 델타 자기신고(source_rows=NULL).

# analyist_dd 코드·테스트 게이트 (2026-11-23 사용자 지시로 강제)

코드를 고치기 전에 이 세 게이트를 알고 시작한다. 위반하면 커밋이 막히거나 야간 크론이 실패로 기록한다.

## 1. 주석 금지 + 린트 (래칫)

- `scripts/lint_agent_rules.py`: C001 주석 금지(기계 지시자만 허용: `#!`, `# -*- coding`, `# type:`, `# noqa`, `# pragma:`, `# fmt:`, `# SPDX-`, `# ruff:`, `# nosec`), C002 TODO/FIXME 는 티켓 ID 필수, C003 `except` 삼킴(pass/continue 만), C004 `except Exception` 후 로그 없이 기본값 반환, T001 검증 없는 테스트, T002 테스트 함수 없는 테스트 파일.
- `ruff.toml`: E/W/F/B/SIM/RET/PIE/RUF/BLE/S/T20/ERA/PLW/ARG. `uvx ruff@latest check --config ruff.toml` 로 실행(ruff 미설치, uvx 사용).
- 기존 부채는 `docs/lint_baseline.json` 에 파일별 건수로 고정. **기준선보다 늘어난 파일만 실패**, 신규 파일은 허용 0.
- pre-commit 훅이 `analyist_dd`·`trader-agent` 두 저장소에 설치됨(스테이징된 `.py` 만 검사).
- 주석을 지울 때는 `scripts/strip_comments.py`(tokenize 기반, 문자열·지시자 보존, 제거 후 구문 오류면 그 파일 미수정). `--top N` 은 벤더 예제(`creon_extracted_v1`)를 제외한다.

## 2. 테스트

- `scripts/run_test_suites.py`: 호스트 실행 가능 스위트(root-tests, api-gateway, trader-core/runner/bridge)만 돈다. `scripts/` 와 서비스 스위트는 pytest 스타일/의존성이 아니어서 제외(컨테이너 전용).
- 결과는 `data/reports/test_runs/latest.json` + `history.jsonl`. "이전 통과 → 지금 실패"를 회귀로 표시.
- 스위트 상태 키는 `suite_skipped`(테스트 개수 `skipped` 와 혼동 금지 — 이 혼동으로 실패 스위트가 통과로 보고된 사고가 있었다).
- 트레이더 테스트는 Windows python: `/mnt/c/Users/jhshi/Python312-64/python.exe -m pytest -q trader_core/tests` (400건대, 16초).

## 3. 인계 계약

- `docs/HANDOFF_CONTRACT.json` + `scripts/handoff_contract_check.py`: 백로그 상태별 필수 필드(가설·재현명령·수치합격기준·근거·차단사유·결과), 원장 근거, Neo4j writer/reader 관계 정합(HAS_THEME↔PART_OF_THEME 류).
- 승인된 항목이 `needs_setup` 으로 되돌아가는 문제는 `scripts/reconcile_approvals.py` 가 복원하되, 차단사유가 기술적 선행(선행/의존/미구현/커버리지 게이트/기록 0건)이면 **차단 유지**(루프 판단 존중).

## 야간 크론 (모두 deliver=local — Discord 자동 발송 금지)

| 시각 | 잡 | 하는 일 |
|---|---|---|
| 02:20 | feature-sources-nightly | supply/financial_ratio/macro 원천 적재 |
| 02:50 | test-suites-nightly | 테스트 스위트 + 회귀 감지 |
| 03:20 | handoff-contract-nightly | 승인 복원 → 계약 검사 |
| 03:40 | lint-agent-rules-nightly | 주석·삼킴 규칙 래칫 + ruff 추세 |

## 교훈

- "조용한 실패"가 이 저장소의 주 실패모드다(BLE001 blind-except 745건, 예외 삼킴 236건 실측). 예외는 구체 타입 + 로그 필수.
- 러너·검사기를 새로 만들면 **일부러 틀린 입력으로 실패하는지 먼저 확인**한다. 이번에도 테스트 러너가 키 이름 충돌로 실패 4건을 "통과"로 보고했다.

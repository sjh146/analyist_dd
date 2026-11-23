# 코드 규칙 (AI 에이전트 공통)

2026-11-23 사용자 지시: **주석 금지 + 린트로 신뢰성 확보.**

## 왜 주석을 금지하는가

이 저장소에서 실제로 발생한 실패 모드다. 주석은 코드가 바뀌어도 갱신되지 않고, 에이전트는
주석을 근거로 잘못된 결론을 낸다. 특히 "왜: ..." 형태의 설명 주석이 동작과 반대가 되어
디버깅을 반대 방향으로 이끈 사례가 반복됐다. 설명은 **이름·타입·테스트·docstring**이 담당한다.

## 강제되는 규칙 (`scripts/lint_agent_rules.py`)

| 규칙 | 내용 | 근거 사고 |
|---|---|---|
| C001 | 코드 주석 금지. 기계 지시자만 허용(`#!`, `# -*- coding`, `# type:`, `# noqa`, `# pragma:`, `# fmt:`, `# SPDX-`, `# ruff:`, `# nosec`) | 주석이 코드와 어긋나 오판 유도 |
| C002 | `TODO/FIXME/XXX/HACK` 는 티켓 ID 필수 (`TODO CG123: ...`) | 출처 없는 할일이 영구 잔류 |
| C003 | `except` 블록이 `pass`/`continue` 로만 끝나면 위반 | 예외 삼킴 → 조용한 0 |
| C004 | `except Exception/BaseException` 후 **로그 없이** 기본값(`None/0/[]`) 반환 금지 | 2026-09-24 graph reader 가 스키마 불일치를 삼켜 테마 피처가 항상 0 |
| C900 | 구문 오류 | — |

ruff 규칙은 `ruff.toml`: E/W/F/B/SIM/RET/PIE/RUF/BLE/S/T20/ERA/PLW/ARG
(S: 보안, BLE: 광범위 예외, T20: print 금지, ERA: 주석 처리된 코드)

## 래칫(기준선)

기존 부채는 `docs/lint_baseline.json` 에 파일별 건수로 고정되어 있다. **기준선보다 늘어난 파일만**
실패한다. 신규 파일은 허용치 0 이므로 완전히 깨끗해야 한다.

```bash
python scripts/lint_agent_rules.py                 # 래칫 검사(증가만 보고)
python scripts/lint_agent_rules.py --strict --files <파일>   # 신규/수정 파일 엄격 검사
python scripts/lint_agent_rules.py --update-baseline        # 부채를 의도적으로 줄였을 때 갱신
```

## 집행 지점

1. **pre-commit 훅** — 두 저장소(`analyist_dd`, `trader-agent`)에 설치됨. 스테이징된 `.py` 만 검사하며 위반 시 커밋이 막힌다.
2. **야간 크론** `lint-agent-rules-nightly` (03:40) — 래칫 + ruff 총계 추세를 로컬 기록.
3. **인계 계약** — 새 코드를 산출물로 내는 항목은 이 규칙을 통과해야 한다(`docs/HANDOFF_CONTRACT.json`).

## docstring

주석 대신 docstring 을 쓴다. 모듈·공개 함수·클래스에만 쓰고, 코드를 그대로 옮겨 적지 않는다.
"왜"가 필요하면 **테스트 이름**이나 **커밋 메시지**에 남긴다.

## 예외 처리 원칙

```python
except (OSError, json.JSONDecodeError) as e:
    log.warning("원장 파싱 실패 %s: %s", path, e)
    return None
```
구체 타입을 잡고, 로그를 남기고, 반환값의 의미를 호출부가 알 수 있게 한다.
"실패를 정상처럼 보이게" 만드는 코드는 C003/C004 로 차단된다.

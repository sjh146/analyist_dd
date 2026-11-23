# 코드 품질 게이트 — 주석 금지 · 린트 래칫 · 인계 계약

자율 루프가 코드를 쓰는 저장소에서 **AI 특유의 실패모드**를 기계로 막는 절차.
사용자 지시는 "주석 금지 + 린트로 신뢰성"이다. 핵심은 **기존 부채는 허용하고 증가분만 차단하는 래칫**이다 —
1000개 파일짜리 저장소에 새 규칙을 켜면 전부 위반이라 아무도 켜지 못한다.

## 1. 왜 주석을 금지하는가

주석은 코드가 바뀌어도 같이 바뀌지 않고, 에이전트는 주석을 근거로 잘못된 결론을 낸다. "왜: ..." 설명 주석이
동작과 반대가 되어 디버깅을 반대 방향으로 이끈 사례가 반복됐다. 설명은 **이름·타입·테스트·docstring**이 담당하고,
"왜"는 커밋 메시지에 남긴다. docstring 은 모듈·공개 함수에만 허용한다(코드를 그대로 옮겨 적지 않는다).

## 2. 규칙 집합 (커스텀, AST 기반)

정규식으로는 "삼킨 예외"를 못 잡는다. AST 로 판정한다. 최소 규칙 4개:

| 규칙 | 판정 | 이유 |
|---|---|---|
| C001 | `#` 주석인데 기계 지시자가 아님 | 주석 드리프트 |
| C002 | `TODO/FIXME/XXX/HACK` 에 티켓 ID 없음 | 출처 없는 할일이 영구 잔류 |
| C003 | `except` 본문이 `pass`/`continue` 뿐 | 조용한 실패 |
| C004 | `except Exception/BaseException` 뒤 로그 없이 `None/0/[]` 반환 | 피처가 항상 0이 되고 아무도 모른다 |

허용 지시자 정규식(앵커 필수): `^#\s*(!|-\*-|coding[:=]|type:|noqa|pragma:|fmt:|isort:|SPDX-|ruff:|mypy:|pyright:|nosec)`.
C004 판정은 "예외 타입이 광범위(bare/Exception/BaseException)" **AND** "반환값이 상수" **AND** "로그 호출 없음"
세 조건 동시일 때만 위반으로 낸다(하나라도 빠지면 정상 코드를 잡는다).

ruff 쪽은 표준 규칙으로 보강한다: `E,W,F,B,SIM,RET,PIE,RUF,BLE,S,T20,ERA,PLW,ARG`
(`BLE`=blind-except, `T20`=print, `ERA`=주석 처리된 코드, `S`=보안). `S101`(assert)·`E501`(길이)은 제외한다.

## 3. 래칫 — 기준선 파일

```bash
python scripts/lint_agent_rules.py --update-baseline    # 파일별 위반 건수를 docs/lint_baseline.json 에 고정
python scripts/lint_agent_rules.py                       # 기준선보다 늘어난 파일만 실패(exit 1)
python scripts/lint_agent_rules.py --strict --files a.py # 신규/수정 파일 엄격(허용 0)
```

- 기준선은 **파일별 건수**다(총계가 아니다). 총계만 쓰면 A 파일을 고치고 B 파일을 더럽혀도 통과한다.
- 신규 파일은 기준선에 없으므로 자동으로 허용 0 이다 — 이게 규칙이 실제로 자라는 방식이다.
- **임시 스크래치(`_*.py`)는 스캔에서 제외**한다. 아니면 루프가 남기는 일회용 스크립트마다 경보가 난다
  (`__init__.py` 는 `_` 로 시작하므로 `_` + non-`_` 조건으로 걸러야 한다).
- 부채를 의도적으로 줄였을 때만 `--update-baseline` 을 다시 돌린다(그 외 재생성 금지 — 래칫이 무의미해진다).

## 4. 집행 지점 3개 (하나만 두면 새 경로가 생긴다)

1. **pre-commit 훅** (easiest win): 스테이징된 `.py` 만 검사 → 위반 시 커밋 차단. 여러 저장소에 같은 훅을 설치한다.
   ```bash
   files=$(git diff --cached --name-only --diff-filter=ACM | grep -E '\.py$')
   [ -z "$files" ] && exit 0
   /usr/bin/python3 /abs/path/scripts/lint_agent_rules.py --root "$(git rev-parse --show-toplevel)" --files $files
   ```
   검증은 "위반 파일을 스테이징하고 커밋이 막히는지"로 한다. 훅 출력이 없으면 통과다(침묵 = 정상).
2. **야간 크론** (`no_agent`, deliver=local): 래칫 + ruff 통계 추세. 정상이면 침묵.
3. **인계 계약/역할 규약**: 쓰기 역할이 읽는 문서(예: 보드 최상단)에 규칙을 넣어야 다음 사이클부터 지킨다.
   스킬·규약에 없으면 에이전트는 그 규칙을 모른 채 코드를 쓴다.

## 5. 함정

- **`git add -A` 가 root 소유 런타임 파일에서 통째로 실패한다** (`error: open(...): Permission denied`).
  컨테이너가 root 로 쓴 상태 파일이 원인이다. 해결은 **커밋할 경로를 명시**하는 것(`git add <내 파일들>`)이고,
  파일을 지우면 다음 잡이 기대는 진행·상태가 사라진다. 소유권을 되돌리는 방법은 `chown` 이다.
- **린트 도구가 시스템에 없을 수 있다** — 이 호스트엔 `ruff` 가 없고 `uvx` 가 있다. `uvx ruff@latest check --config ruff.toml .`
  로 설치 없이 쓴다. 패키지 관리자가 PEP668 로 막혀 있으면 이 경로가 유일하다.
- **ruff 는 "주석 금지" 규칙이 없다**(`ERA001`=주석 처리된 코드만). 주석 정책은 반드시 커스텀 AST 검사로 만든다.
- **린트가 제 코드에서 먼저 위반을 잡는다** — 그게 정상이고 좋은 신호다. 새 규칙을 켜면 만든 사람의 코드부터
  고쳐서 "규칙이 실제로 작동한다"를 보여라(로그 추가 + 구체 예외 타입으로 수정).
- **스캔은 `ast.parse` 로 하면 SyntaxWarning 이 stdout 을 더럽힌다** — `warnings.catch_warnings()` +
  `simplefilter("ignore")` 로 감싸야 크론 로그가 깨끗하다.
- **래칫 검사는 빠르다**(1300파일 6초). 그래서 커밋 훅에 넣어도 작업 흐름을 막지 않는다 — "느릴 것"이라는
  가정으로 훅을 빼지 마라.

## 6. 인계 계약(handoff contract)과 같이 쓸 때

역할 간 인계를 "산문 규약"으로만 두면 기계가 검증할 수 없다. 별도 JSON 계약 + 검사기로 옮기면
① 상태별 필수 필드(pending=가설·재현명령·수치합격기준 / needs_setup=차단사유 / done=결과)
② 방향별 필수 증거 ③ 그래프 스키마(writer↔reader 관계 쌍)를 매일 검사할 수 있다.
**그래프는 writer 와 reader 가 다른 관계명을 쓰면 피처가 조용히 0 이 된다** — 쌍으로 검사해서 한쪽이 0 이면 FAIL.
검사기는 정상이면 침묵하고 WARN 은 exit 0, FAIL 만 비정상 종료로 둔다(크론이 오탐 경보를 내지 않게).

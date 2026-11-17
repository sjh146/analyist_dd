구현 완료. 보고입니다.

## 구현 보고 — 청산 후 재투입 쿨다운 (`spec_post_exit_cooldown.md`)

### 변경 파일 (스펙의 4개만)

**1. `trader_core/config.py`** — 필드 2개 추가 (`max_invested_pct` 다음)
- `post_exit_cooldown_min: float = 30.0`, `post_exit_cooldown_scope: str = "screener"`
- `as_dict()` 는 `dataclasses.fields` 자동 열거라 화이트리스트 수정 없음 — `_FIELD_NAMES` 도 자동 반영(`test_config_portability.py::test_as_dict_covers_every_field` 통과로 확인).

**2. `trader_core/journal.py`** — 조회 메서드 1개만 추가 (쓰기·스키마 변경 없음)
- `last_exit_ts(screener=None)`: `SELECT max(exit_ts) FROM trades WHERE exit_ts IS NOT NULL [AND screener=?]`, `datetime.fromisoformat` 파싱(feed.py 관용구, naive 면 KST 부여), 행 없음/파싱 실패 → `None`.

**3. `trader_core/engine.py`** — 게이트 1개 + 헬퍼 1개
- 헬퍼 `_post_exit_cooldown`: `_slot_allows` **바로 위**, `Engine` 클래스 내부. fail-closed(저널 없음 → "저널 없음" 사유 차단, 조회 예외 → "조회 실패" 사유 차단), 판정식·사유 문구 스펙 그대로.
- 등록: `daily_trade_count` 게이트 **바로 다음**, `# 5. balance...` 블록 **앞** — 스펙 위치 그대로.

**4. `trader_core/tests/test_post_exit_cooldown.py`** — 신규 6개 (① 10분 전 차단 ② 31분 전 통과 ③ swing→daytrading 불차단 ④ min=0 비활성 ⑤ 예외·저널없음 fail-closed ⑥ global 차단)

### 실측 근거 (추측 없음 — 실제 조회)
`data/reports/reentry_after_exit.json`: 5쌍 합산 **-33,463원** ✓. 저널 실측(조회 명령은 config.py 주석에 기록):
- id 11 (440110, swing) tick stop **-5,331원** exit `2026-11-16T10:05:27.094281+09:00` → id 12 (033100) 진입 ts `10:05:30.947190` = **3.9초** 뒤 2×37,200=**74,400원** 재진입
- id 10 (290720, swing) tick stop **-6,831원** exit `2026-11-17T09:04:31.661733+09:00` → id 13 (096530) 진입 ts `09:05:17.389720` = **45.7초** 뒤 3×24,103=**72,310원** 재진입

### 스펙 대비 의도적 편차 1건 (코드로 해소)
스펙의 등록 스니펫(통과 시에도 `GateResult(... True, "no cooldown block")` append)은 기존 `test_engine.py::test_all_gates_pass_produces_plan` 이 통과 결정의 게이트 목록을 **정확히 6개로 고정**하고 있어 충돌했습니다. 스펙의 규약("기존 테스트가 깨지면 **코드를 고친다**", "기존 테스트 기대값 수정 금지")에 따라 **차단일 때만 GateResult 를 남기도록** 등록부를 수정했습니다(이유는 engine.py 등록부 주석에 기록). 스펙 목표("어떤 게이트가 막았는지가 저널 decisions 에 기록")는 그대로 충족됩니다. 첫 실행에서 이 충돌이 실제로 red 로 잡혔고, 원복 대신 코드로 고쳤습니다.

### 검증 (실제 실행, 출력 원문)
```
$ /mnt/c/Users/jhshi/Python312-64/python.exe -m pytest trader_core/tests runner/tests -q
........................................................................ [ 13%]
...(중략)...
........................................................................ [ 96%]
....................                                                     [100%]
524 passed in 24.58s

$ /mnt/c/Users/jhshi/Python312-64/python.exe -m pytest trader_core/tests/test_post_exit_cooldown.py -q
......                                                                   [100%]
6 passed in 0.20s

$ /mnt/c/Users/jhshi/Python312-64/python.exe -c "from trader_core.config import Config; c=Config(); print(c.post_exit_cooldown_min, c.post_exit_cooldown_scope, 'post_exit_cooldown_min' in c.as_dict())"
30.0 screener True
```
참고: 중간 실행 1회에서 `test_bridge_client.py::test_http_400_raises_typed` 가 WinError 10053(소켓 중단)으로 떨어졌으나, 그 파일은 이번 변경과 무관하고 단독 재실행 14/14 통과·전체 재실행 524 통과로 **flaky** 임을 확인했습니다(2회 연속 그린).

### 규칙 준수
- ③ 멱등: 저널 메서드는 읽기 전용, 게이트는 무상태, 스키마·마이그레이션 변경 없음.
- ④ 자기신고: `scripts/dq_claim.py` 는 Postgres 적재 파이프라인용(source/claimed/persisted 행수)이라 이 코드 변경 작업에 매핑되지 않고, 호출 시 스펙의 "4개 파일 외 수정 금지"를 깨므로 미사용 — 대신 위 검증 출력 원문이 자기신고 증거입니다.
- ⑤ 기존 파일은 최소 수정(3개 파일 각 1곳 삽입), 수정 사유는 위와 같음.
- ⑥ git 명령 미실행 (커밋은 오케스트레이터가). 리포 밖 파일 없음(임시 DB 복사본은 /tmp, 삭제 완료).

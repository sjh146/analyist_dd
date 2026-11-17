# 스펙 — 청산 후 재투입 쿨다운 (post_exit_cooldown)

작성 2026-11-17. **사용자 승인 완료**("응 진행해줘") — 실주문 경로 변경이므로 이 문서가 승인 근거다.
근거 실측: `data/reports/reentry_after_exit.json` — 청산 직후 재투입 5쌍 합산 **-33,463원**,
11-16 스톱(-5,331원) **3.9초** 뒤 74,400원 재진입, 11-17 스톱(-6,831원) **45.7초** 뒤 72,310원 재진입.
출처: `data/state/ceo_priority.json`(day=2026-11-16) escalations 1번
"'손실 청산 후 당일 신규 진입 정지' 또는 최소한 '청산 후 30분 쿨다운'이 필요하다".

## 목표

청산이 발생한 뒤 일정 시간(기본 **30분**) 동안 **같은 경로(screener)의 신규 진입을 차단**하는
게이트를 실주문 경로에 추가한다. 기존 게이트 체인과 같은 형태(`GateResult`)로 남겨
어떤 게이트가 막았는지가 저널 `decisions` 에 그대로 기록되게 한다.

## 비목표 (명시)

- 청산 로직·스톱/TP·사이징·한도(`max_open_positions`·`max_invested_pct`·`max_position_pct`)를 바꾸지 않는다.
- 저널 **스키마를 바꾸지 않는다**(컬럼 추가·마이그레이션 금지). 조회 메서드만 추가한다.
- 다른 경로(daytrading)를 swing 청산으로 막지 않는다 — 기본 scope 는 `screener`(같은 경로만).
- 피드·analyist_dd 파이프라인을 건드리지 않는다.

## 변경 대상 (이 4개 파일만)

1. `trader-agent/trader_core/config.py` — 필드 2개 추가
   - `post_exit_cooldown_min: float = 30.0` (0 이면 비활성; 음수는 0 으로 취급)
   - `post_exit_cooldown_scope: str = "screener"` (`"screener"` | `"global"`)
   주석에 WHY(위 실측 수치·출처 날짜)를 남긴다. `Config.as_dict()` 는 dataclass 필드를
   자동 열거하므로 별도 수정이 필요 없음을 확인만 한다(화이트리스트 하드코딩 금지).
2. `trader-agent/trader_core/journal.py` — **조회 메서드 1개 추가** (쓰기·스키마 변경 없음)
   ```python
   def last_exit_ts(self, screener: Optional[str] = None) -> Optional[datetime]:
       """가장 최근 청산 시각(exit_ts 기준). screener 지정 시 그 경로만."""
   ```
   - `SELECT max(exit_ts) FROM trades WHERE exit_ts IS NOT NULL [AND screener = ?]`
   - ISO 문자열을 `datetime`(KST aware)으로 파싱해 돌려준다. `ts` 가 아니라 **`exit_ts`** 기준.
   - 행이 없거나 파싱 실패면 `None`.
   - `_iso` 와 동일한 형식 규약을 쓰고, 기존 `closed_trades`/`closed_on` 의 파싱 관용구를 재사용한다.
3. `trader-agent/trader_core/engine.py` — 게이트 1개 추가
   - **삽입 위치: `_evaluate_candidate` 의 게이트 체인에서 `daily_trade_count` 게이트 바로 다음,
     `# 5. balance / position / order-amount caps` 블록 **앞**.** (파일 첫 `def ` 근처나 다른 클래스에 넣지 말 것 — 실측 사고 이력 있음)
   - 새 헬퍼는 **`Engine` 클래스 내부**, `_slot_allows` **바로 위**에 둔다:
     ```python
     def _post_exit_cooldown(self, screener: str) -> Optional[str]:
         """청산 후 쿨다운 — 차단이면 사유 문자열, 허용이면 None.

         fail-closed: cooldown > 0 인데 저널이 없거나 조회가 실패하면 사유를 돌려 차단한다
         (얼마나 지났는지 셀 수 없는데 열어주면 쿨다운을 보증할 수 없다).
         """
     ```
   - 판정:
     - `cooldown = max(0.0, float(self.config.post_exit_cooldown_min or 0.0))`; `cooldown <= 0` → `None`(비활성)
     - scope `"global"` → `last_exit_ts()`(screener 인자 없이), 그 외(`"screener"`/미지정/알 수 없는 값) → `"screener"` 로 취급해 `last_exit_ts(screener=screener)`
     - `last = 저널 조회`; `None` → `None`(청산 이력 없음)
     - `elapsed_min = (now - last).total_seconds() / 60.0`; `elapsed < cooldown` → 차단 사유 문자열
       `"청산 후 쿨다운 {elapsed:.1f}분 < {cooldown:.0f}분 (마지막 청산 {last_iso}, scope {scope})"`
     - 그 외 → `None`
     - `self.journal is None` 이고 cooldown > 0 → 차단(사유에 `"저널 없음"` 명시)
     - `now` 는 `datetime.now(KST)` — 기존 모듈 관용구(`from .journal import KST` 또는 이미 import 된 것을 재사용)
   - 게이트 등록은 기존 관용구 그대로:
     ```python
     cooldown_reason = self._post_exit_cooldown(screener)
     if cooldown_reason is None:
         gates.append(GateResult("post_exit_cooldown", True, "no cooldown block"))
     else:
         gates.append(GateResult("post_exit_cooldown", False, cooldown_reason))
     ```
   - 차단되어도 **예외를 던지지 않는다**(게이트 체인 규약). 다른 게이트의 평가·기록은 그대로 진행된다.
4. `trader-agent/trader_core/tests/test_post_exit_cooldown.py` — 신규 테스트 6개
   - ① 청산 10분 전 → 진입 차단(`post_exit_cooldown` gate False, 사유에 "쿨다운")
   - ② 청산 31분 전 → 통과
   - ③ swing 청산이 daytrading 진입을 막지 않는다(scope=screener)
   - ④ `post_exit_cooldown_min=0` → 비활성(청산 직후여도 통과)
   - ⑤ 저널 조회가 예외를 던지면 fail-closed(차단)
   - ⑥ scope="global" 이면 다른 screener 도 차단
   - 기존 `trader_core/tests/test_slot_wiring.py` 의 저널 대역(`open_trades` 대역) 관용구를 그대로 참고하고,
     **기존 테스트 파일의 기대값을 바꾸지 않는다**. 기존 테스트가 깨지면 코드를 고친다(테스트를 고쳐 통과시키지 말 것).

## 검증 (반드시 실제 실행하고 출력 원문을 보고에 붙인다)

```
cd /mnt/c/Users/jhshi/analyist_dd/trader-agent
/mnt/c/Users/jhshi/Python312-64/python.exe -m pytest trader_core/tests runner/tests -q
/mnt/c/Users/jhshi/Python312-64/python.exe -m pytest trader_core/tests/test_post_exit_cooldown.py -q
/mnt/c/Users/jhshi/Python312-64/python.exe -c "from trader_core.config import Config; c=Config(); print(c.post_exit_cooldown_min, c.post_exit_cooldown_scope, 'post_exit_cooldown_min' in c.as_dict())"
```
- **두 스위트 전체 그린이 커밋 조건**이다. red 면 즉시 `git checkout -- <바꾼 파일>` 로 원복하고 원인을 보고한다.
- 위 `Config()` 출력이 `30.0 screener True` 여야 한다(as_dict 반영 확인).

## 금지

- `git` 명령 실행 금지(커밋·푸시·원복 판단은 오케스트레이터가 한다).
- 저널 스키마 변경·마이그레이션 금지. `.env`·`config/objective.json`·`kill_switch.txt`·analyist_dd 밖 파일 수정 금지.
- 위 4개 파일 외 수정 금지. 리포 밖 파일 생성 금지(임시파일은 /tmp).
- 스톱/TP/사이징/한도 값 변경 금지. 기존 게이트의 판정 순서·문구 변경 금지.
- 기존 테스트의 기대값 수정 금지(필요해 보이면 보고하고 멈춘다).

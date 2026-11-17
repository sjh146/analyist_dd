# 스펙 — 계기판 한도 축을 실행 설정에서 읽게 (single source of truth)

작성 2026-11-17. **사용자 승인**: "응 계속 진행해줘" (직전 보고의 남은 항목 1번: money_scoreboard 한도 정본).
근거 실측: `data/state/ceo_priority.json`(day=2026-11-16) escalations —
"money_scoreboard 는 종목 10%·총 30% 기준으로 '한도 초과 +74,836원'을 출력하지만 실행 설정은
1/3·100%(11-09 지시)다 … 어느 쪽이 정본인지 확정 전까지 노출 판정이 양방향으로 틀린다".

정책 값을 새로 정하는 것이 **아니다**. 지금 실제로 주문을 내는 주체는 `TraderEngine` 이고 그 값은
`trader_core/config.py` 의 `Config()` 다. 계기판이 그 값을 **직접 읽어** 표시하면 두 축이 어긋날 수 없고,
나중에 형님이 정본을 바꿀 때 `Config` 한 곳만 고치면 된다. 어긋남이 있으면 **숨기지 말고 출처를 표시**한다.

## 실측 확정값 (재조사 금지 — 2026-11-17 실행 확인)

`cd /mnt/c/Users/jhshi/analyist_dd/trader-agent && /usr/bin/python3 -c "import sys;sys.path.insert(0,'.');from trader_core.config import Config;c=Config();print(c.daily_max_trades,c.max_position_pct,c.max_open_positions,c.max_invested_pct)"`
→ `3 0.3333333333333333 3 1.0`

현재 계기판의 하드코딩(`scripts/money_scoreboard.py:63`):
`{"max_daily_trades": 3, "per_stock_pct": 0.10, "max_open_positions": 3, "total_exposure_pct": 0.30}`
→ 종목 10%·총 30% 가 실행(33%·100%)과 불일치. 이것이 매일 거짓 '한도 초과'를 만든다.

## 변경 대상 (이 4파일만)

1. **신규 `scripts/risk_limits.py`** — 한도 단일 진실원 헬퍼
   ```python
   FALLBACK = {"max_daily_trades": 3, "per_stock_pct": 0.10,
               "max_open_positions": 3, "total_exposure_pct": 0.30}

   def execution_limits(candidates=None) -> tuple[dict, dict]:
       """(limits, meta) — 실행 설정(Config)에서 한도 4값과 출처 메타를 돌려준다."""
   ```
   - `candidates`: trader-agent 사본 경로 목록. 기본 순서
     `[os.environ.get("TRADER_AGENT_DIR"), "/mnt/c/Users/jhshi/analyist_dd/trader-agent", <repo>/trader-agent]`
     (없는 경로는 건너뛴다 — **존재하는 첫 사본**을 쓴다. 라이브(Windows) 사본이 실행 주체이므로 먼저).
   - 각 후보에 대해 `sys.path.insert(0, 사본)` 후 `from trader_core.config import Config` 로 `Config()` 를 얻고,
     import 한 모듈·`sys.modules` 는 **검사 후 원상복구**한다(다른 후보·이후 import 오염 금지).
   - 매핑: `max_daily_trades←daily_max_trades`, `per_stock_pct←max_position_pct`,
     `max_open_positions←max_open_positions`, `total_exposure_pct←max_invested_pct`.
   - `meta` 필수 키: `source`(`"config:<절대경로>"` 또는 `"fallback"`), `loaded`(bool),
     `values`(매핑 원본 4값), `fallback`(FALLBACK 사본), `candidates_tried`(경로 목록).
   - 어떤 후보도 import 되지 않으면 `LIMITS=FALLBACK`, `meta["source"]="fallback"`, `loaded=False` — **예외를 던지지 않는다**.
   - 모듈 수준에서 `LIMITS, LIMITS_META = execution_limits()` 를 노출한다.
   - 파일 상단 docstring 에 WHY(위 ceo_priority 인용 + 실측 명령)를 남긴다.

2. **`scripts/money_scoreboard.py`**
   - `LIMITS = {...}` 하드코딩 제거 → `from risk_limits import LIMITS, LIMITS_META` 로 교체.
     (`scripts/` 가 import 경로에 없을 수 있으므로 `sys.path` 보정 관용구를 쓰거나 `importlib` 로 같은 디렉터리 모듈을 로드한다. **실행·`importlib` 양쪽에서 동작**해야 한다 — 기존 `tests/test_money_scoreboard.py` 는 `importlib.util.spec_from_file_location` 으로 이 파일만 로드한다.)
   - 모듈 docstring 26행의 `한도: 일 3건 · 종목 10% · 동시 3종목 · 총 30%` 문구를 **하드코딩 숫자 없이** 바꾼다
     (예: `한도: 실행 설정(trader_core/config.py)의 일 거래수·종목당·동시보유·총노출 — 값은 출력의 capital_use.limits 참조`).
   - `LIMITS_META` 를 계기판 출력(json)에 `limits_meta` 로 실어 **출처가 보이게** 한다. 넣는 위치는
     `capital_use` 를 만드는 쪽 옆(요약 json 최상위 `limits_meta`) — 스키마를 깨지 않도록 **추가 키만** 넣는다(기존 키 삭제·이름변경 금지).
   - `limits` 파라미터를 받는 기존 함수(`capital_use(...)` 등)의 시그니처·기본값 동작은 유지한다(테스트 호환).

3. **`tests/test_money_scoreboard.py`** — 기대값 1곳만 갱신 (183행 부근)
   - 현재: `assert cu["limits"] == {"max_daily_trades": 3, "per_stock_pct": 0.10, "max_open_positions": 3, "total_exposure_pct": 0.30}`
   - 변경: 하드코딩 대신 **실행 설정과의 일치**를 검사한다.
     ```python
     from risk_limits import LIMITS, LIMITS_META   # 또는 M.LIMITS 사용
     assert cu["limits"] == M.LIMITS
     if LIMITS_META["loaded"]:
         assert cu["limits"] == {"max_daily_trades": 3, "per_stock_pct": pytest.approx(1/3),
                                 "max_open_positions": 3, "total_exposure_pct": 1.0}
     ```
     (문자열 비교가 아니라 approx 로 — 1/3 은 부동소수다. `tests/` 가 `scripts/` 를 import 하는 기존 관용구가 없으면 `M.LIMITS` 만 쓴다.)
   - **그 외 테스트는 손대지 않는다.** 다른 테스트가 깨지면 코드를 고친다.

4. **`docs/QUANT_SCOREBOARD.md`** (있으면) — 한도 문구가 하드코딩돼 있으면 같은 취지로 1~3줄만 고친다. 없거나 이미 일반 문구면 건드리지 않는다.

## 검증 (실제 실행, 출력 원문을 보고에 붙인다)

```
cd /home/jhshi/analyist_dd
/usr/bin/python3 -c "import sys; sys.path.insert(0,'scripts'); import risk_limits as r; print(r.LIMITS, r.LIMITS_META['source'], r.LIMITS_META['loaded'])"
/usr/bin/python3 -m pytest tests/test_money_scoreboard.py -q
/usr/bin/python3 scripts/money_scoreboard.py --no-live --out-dir /tmp/msb_fixture
/usr/bin/python3 scripts/money_scoreboard.py --no-live --out-dir /tmp/msb_fixture2   # 멱등(2회차 바이트 동일)
```
- 첫 명령이 `{'max_daily_trades': 3, 'per_stock_pct': 0.333…, 'max_open_positions': 3, 'total_exposure_pct': 1.0} config:/mnt/c/... loaded=True` 를 출력해야 한다.
- pytest 는 **전부 통과**(실패 0).
- 계기판 산출 json 에 `limits_meta.source` 가 있고 `capital_use.limits` 가 위 값과 같아야 한다.
- 두 번 실행 산출물이 동일해야 한다(멱등).

## 금지

- `git` 명령 실행 금지(커밋은 오케스트레이터가).
- **정책 값을 새로 정하지 말 것** — `trader_core/config.py`·`config/objective.json`·프리셋·`.env` 수정 금지.
- `scripts/audit_safety.py` 는 이번 범위 밖이다(손대지 마라 — 정책 파일 축 비교라 별도 판단 필요).
- 위 4개 파일 외 수정 금지. 리포 밖 파일 생성 금지(임시 산출물은 /tmp).
- 실 브리지·실 DB 접속 금지(`--no-live` 픽스처 모드만 사용). 네트워크 호출 금지.
- 기존 테스트 기대값 수정은 위 183행 1곳만. 다른 기대값 수정 금지.

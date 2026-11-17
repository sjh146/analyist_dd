구현 완료. 보고합니다.

## WP2 초단타 고정 라인(1.5%/2.5%) 구현 보고

### 변경 파일 (스펙이 정한 4파일만)

**1. `trader-agent/trader_core/profiles.py`** (기존 파일, 최소 수정)
- `ScreenerProfile` 에 필드 4개 추가: `stop_mode: str = "vol"`, `stop_pct`/`tp_pct`/`max_stop_pct: Optional[float] = None` — 스펙의 서명·주석 그대로. 필드 docstring 에 WHY 와 실측 근거(그리드 11,300건 7%/7% −0.822% → 1%/2% −0.151%, 왕복비용 0.21%, ±1.5% 손익분기 승률 57.0% / ±1% 60.5%, 병목은 진입)와 근거 문서 경로를 남김.
- `DEFAULT_PROFILES["daytrading"]` 만 갱신: `entry_window "09:05-14:30"`, `close_exit_window "15:00-15:20"`, `hold_overnight False`(유지), `stop_mode "fixed"`, `stop_pct 0.015`, `tp_pct 0.025`, `max_stop_pct 0.02`. 기존 키(`exit_mode`, `kelly_fraction 0.15`, `max_entries_per_day 3`)는 그대로. `swing`/`close`/`rank_momentum` 은 **한 글자도 안 바꿈**.

**2. `trader-agent/trader_core/engine.py`** (기존 파일, 최소 수정)
- 상단 import 에 `from .profiles import profile_for` 추가(스펙 지시 관용구; profiles 는 `.config` 만 import 하므로 순환 없음).
- `_evaluate_candidate` 사이징 블록에서 변동성 `stop_pct`/`tp_pct` 계산 **직후**에 고정 라인 오버라이드 삽입 — `stop_mode=="fixed" and stop_pct and tp_pct` 이면 `max_stop_pct` 로 자른 고정값으로 대체. 이후의 사이징·동일위험 캡(`risk_capped_qty`)이 그대로 이 고정 stop_pct 를 쓰는 위치입니다.
- `algo_json` 의 `stop` dict 에 실제 `stop_pct`/`tp_pct` 와 `model` 라벨이 남음: daytrading 결정은 `"model": "fixed"`, 변동성 경로는 `"vol-range"` — **편차 1** 참조.

**3. `trader-agent/trader_core/tests/test_profiles_fixed_stop.py`** (신규, 5케이스)
① daytrading 프로파일 필드 스냅샷 ② swing/close/rank_momentum `stop_mode=="vol"`, `stop_pct/tp_pct is None` ③ `stop_pct=0.05, max_stop_pct=0.02` → 엔진이 0.02 로 자름 ④ daytrading 결정 `stop_pct≈0.015, tp_pct≈0.025`(변동성 24% 여도 덮이지 않음, model "fixed") ⑤ swing 결정 변동성 경로(`0.12 ≥ 0.07`, model "vol-range").

**4. `trader-agent/presets/daytrading_fixed.json`** (신규)
스펙의 `stop_mode_override_note` + `screener_profiles.daytrading` 그대로, 기존 프리셋 관례의 `_comment` 추가(1%/1% 전환 방법·롤백 명시).

### 추측 금지 — 조회한 실측값과 명령

- `Config().stop_loss_pct/take_profit_pct = 0.07/0.07` (2026-11-17 실행 확인): `python.exe -c "from trader_core.config import Config; ..."`
- 변경 전 daytrading `entry 09:00-14:50, close 15:00-15:25`, `stop_mode` 필드 부재 확인.
- `trader_core.Config` 에 `screener_profiles` 필드 없음 + `Config._coerce` 가 미지 키를 버림(실행 확인) → **편차 2**. `RunnerConfig` 는 dict 를 받음(`RunnerConfig.from_file('presets/daytrading_fixed.json')` 실행 확인).
- 그리드·손익분기 승률 수치는 지시서(`docs/spec_daytrading_shortstop_2026-11-17.md` 0-3/0-4)에서 인용(재측정 금지 규약) — 파일에서 해당 행 존재 확인 후 주석에 명령·출처 기재.

### 검증 (실제 실행 출력 원문)

```
$ /mnt/c/Users/jhshi/Python312-64/python.exe -m pytest trader_core/tests runner/tests -q
........................................................................ [ 13%]
........................................................................ [ 27%]
........................................................................ [ 40%]
........................................................................ [ 54%]
........................................................................ [ 68%]
........................................................................ [ 81%]
........................................................................ [ 95%]
.........................                                                [100%]
529 passed in 25.75s
```
```
$ /mnt/c/Users/jhshi/Python312-64/python.exe -m pytest trader_core/tests/test_profiles_fixed_stop.py -q
.....                                                                    [100%]
5 passed in 0.57s
```
```
$ /mnt/c/Users/jhshi/Python312-64/python.exe -c "from trader_core.profiles import profile_for as p; d=p('daytrading'); s=p('swing'); print(d.stop_mode,d.stop_pct,d.tp_pct,d.entry_window,d.close_exit_window,d.hold_overnight); print(s.stop_mode,s.stop_pct,s.tp_pct)"
fixed 0.015 0.025 09:05-14:30 15:00-15:20 False
vol None None
```
기대값과 정확히 일치. 추가 확인 — 실제 엔진 결정:
```
passed: True | plan.stop_pct= 0.015 plan.tp_pct= 0.025
algo.stop = {'model': 'fixed', 'stop_pct': 0.015, 'tp_pct': 0.025, 'floor_pct': 0.07, ...}
```

### 멱등성 / 자기신고
- DB 적재 없음(코드·정적 JSON 수정뿐) → 재실행해도 동일 결과. 신규 파일 재생성·기존 파일 재편집 모두 동일 내용으로 수렴.
- `scripts/dq_claim.py` 의 `record_claim` 은 postgres 행 적재(claimed/persisted/source_rows)용이라 이 작업(0행 적재)엔 부적합 → 사용하지 않음. 대신 이 리포의 코드 변경 관례대로 **파일 상단 docstring 에 WHY+실측 근거와 조회 명령**을 자기신고로 남겼고, 검증 출력을 본 보고에 첨부.

### 금지 준수
- `git` 미실행. `max_invested_pct`·`daily_max_trades`·`max_open_positions`·쿨다운 미변경. swing/close/rank_momentum 정책 미변경(검증 출력으로 보존 확인). 주문 시그니처·저널 스키마 미변경. WP1(`entry_filters.py` 등) 미착수. 4파일 외 수정 없음, 리포 밖 파일 생성 없음. 기존 테스트 기대값 수정 없음(기존 스위트 그린 유지).

### 편차 및 사유 (스펙 대비)
1. **`model` 라벨 `volatility` 대신 `vol-range` 유지**: 스펙은 `volatility`/`fixed` 구분을 요구하지만, 기존 테스트 `test_algo_attribution.py:195` 가 변동성 경로의 `stop["model"]=="vol-range"` 를 고정하고 있고 스펙 금지("기존 테스트 한 개도 수정하지 않는다, 깨지면 코드를 고친다")와 충돌합니다. 변동성 경로 라벨을 그대로 두고, 고정 라인은 `"fixed"` 로 기록해 **두 경로는 여전히 구분**됩니다(사후분석 근거 충족). engine.py 주석·테스트 ⑤ 주석에 명시.
2. **프리셋의 `screener_profiles` 는 엔진에 도달하지 못함 → 기본값 경로로**: 스펙의 조건부("`Config.screener_profiles` 가 dict 를 받는지 확인하고, 받지 않으면 프리셋 대신 config.py 기본값으로 두고 그 이유를 보고")에 해당합니다. `trader_core.Config` 에는 필드가 없고 `_coerce` 가 미지 키를 버리며(실행으로 확인), 루프는 엔진에 `engine_config()` 만 넘겨 `screener_profiles` 를 전달하지 않습니다(loop.py 는 4파일 밖이라 수정 불가). 따라서 엔진은 `getattr(self.config, "screener_profiles", None)` 폴백으로 **profiles.py DEFAULT_PROFILES**(= config.py 기본값 경로)를 읽습니다. 프리셋 파일은 스펙대로 생성하되 `_comment` 에 이 사실과 전환 방법을 적었습니다 — **1%/1% 로 바꾸려면 `profiles.py` 의 `DEFAULT_PROFILES["daytrading"]` `stop_pct`/`tp_pct` 두 값(및 프리셋 동기화)만 고치면 됩니다.** `RunnerConfig` 경로(loop/monitor)에서는 프리셋이 실제로 인식됨을 실행으로 확인했습니다.
3. **daytrading `kelly_fraction` 0.15 유지**: 상위 지시서 2.1 의 예시는 0.10 이지만, 본 스펙이 "기존 키는 유지, 추가·변경만"으로 열거 키를 한정해 기존 0.15 를 그대로 뒀습니다.

### 롤백
`DEFAULT_PROFILES["daytrading"]` 의 `stop_mode` 를 `"vol"` 로 되돌리면 기존 변동성 스톱·7% 익절 동작 그대로(스펙 계약 5번, 지시서 완료조건 5번).

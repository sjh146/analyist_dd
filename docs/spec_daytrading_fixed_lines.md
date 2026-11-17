# 스펙 — WP2: 초단기(1.5%/2.5%) 고정 라인 + 당일 청산 (실계좌 즉시 적용)

작성 2026-11-17. **사용자 지시**: "응 실계좌로 바로 진행해줘".
상위 문서: `docs/spec_daytrading_shortstop_2026-11-17.md` (사용자 확정 지시서) — **배포 방식 0.0**:
"모의계좌 트랙 없음, 실계좌로 계속 테스트", 순서는 ① 진입 필터 → ② 단타 프로파일. 이 스펙은 **②만** 구현한다
(WP1 진입 필터는 별도 위임 — 이 스펙에서 건드리지 말 것).

## 실측 근거 (재측정 금지, 지시서 0-3/0-4 인용)

- 현재 라이브는 스윙이다: `Config().stop_loss_pct = 0.07`, `take_profit_pct = 0.07` (2026-11-17 실행 확인).
- `ScreenerProfile` 에 **`stop_pct`/`tp_pct` 필드가 아예 없어서** daytrading 프로파일도 전역 7%/7%를 쓴다.
  → "1~2% 단타"로 바뀐 게 아니다(코드 확인 사실).
- 그리드 실측(신호 11,300건): 7%/7% 중립 −0.822% → 1%/1% −0.369% → **1%/2% −0.151%**(측정된 최고).
  손절만 좁히면 기대값이 +로 돌아서지 않는다 — 병목은 진입이다(무손절 기준선 −1.328%, 승률 36.0%).
  왕복비용 0.21% 때문에 ±1.5% 라인 손익분기 승률은 **57.0%**, ±1%는 60.5%.
- 이 지시서가 고른 값: **손절 1.5% / 익절 2.5%**(R:R 1.67), 상한 2%, 당일 청산.
  (형님이 "1%로"라고 말씀하셨으므로 1%/1% 로 바꾸고 싶으면 config 한 줄로 가능해야 한다 — 아래 계약 4번.)

## 변경 대상 (이 4파일만)

1. **`trader-agent/trader_core/profiles.py`**
   - `ScreenerProfile` 에 필드 4개 추가:
     ```python
     stop_mode: str = "vol"              # "vol"=기존 변동성 스톱 | "fixed"=아래 고정값
     stop_pct: Optional[float] = None    # stop_mode=="fixed" 일 때 손절 폭
     tp_pct: Optional[float] = None      # stop_mode=="fixed" 일 때 익절 폭(비율 스케일 금지)
     max_stop_pct: Optional[float] = None  # 고정 손절의 상한(넘으면 이 값으로 자름)
     ```
   - `DEFAULT_PROFILES["daytrading"]` 을 아래로 갱신(기존 키는 유지, 추가·변경만):
     ```python
     "entry_window": "09:05-14:30",     # 시가 추격 금지
     "close_exit_window": "15:00-15:20",# 당일 전량 청산 창
     "hold_overnight": False,           # 기존 False 유지
     "stop_mode": "fixed",
     "stop_pct": 0.015,
     "tp_pct": 0.025,
     "max_stop_pct": 0.02,
     ```
     주석에 손익분기 승률(±1.5% → 57.0%)과 근거 문서 경로를 남긴다.
   - **`swing`/`close`/`rank_momentum` 프로파일 값은 절대 바꾸지 않는다**(stop_mode 기본 "vol" = 기존 동작 그대로).
2. **`trader-agent/trader_core/engine.py`**
   - `_evaluate_candidate` 의 사이징 블록에서 `stop_pct`/`tp_pct` 를 계산한 **직후**
     (현재 코드: `stop_pct = risk.volatility_stop_pct(...)` … `tp_pct = stop_pct * (tp/stop)` 다음)에
     고정 라인 오버라이드를 넣는다:
     ```python
     prof = profile_for(screener, self.config.screener_profiles)
     if str(getattr(prof, "stop_mode", "vol")) == "fixed" and prof.stop_pct and prof.tp_pct:
         cap = prof.max_stop_pct
         stop_pct = min(float(prof.stop_pct), float(cap)) if cap else float(prof.stop_pct)
         tp_pct = float(prof.tp_pct)
     ```
     (`from .profiles import profile_for` 는 engine 상단 import 에 추가 — monitor.py/loop.py 와 같은 관용구.)
   - 사이징·수량 계산은 **그대로** 이 고정 stop_pct 를 쓰게 둔다(동일위험 캡 경로 포함).
   - `algo_json` 의 `stop` dict 에 `"model": "fixed"` 와 실제 `stop_pct`/`tp_pct` 가 남도록 기존 기록 경로를 확인하고,
     `model` 값이 `volatility`/`fixed` 로 구분되게 한다(사후 분석 근거).
3. **`trader-agent/trader_core/tests/test_profiles_fixed_stop.py`** (신규)
   - ① daytrading 프로파일: `stop_mode=="fixed"`, `stop_pct==0.015`, `tp_pct==0.025`, `hold_overnight is False`,
     `entry_window=="09:05-14:30"`, `close_exit_window=="15:00-15:20"`
   - ② swing/close/rank_momentum: `stop_mode=="vol"`, `stop_pct is None`, `tp_pct is None` (기존 동작 보존 스냅샷)
   - ③ `max_stop_pct` 상한: `stop_pct=0.05, max_stop_pct=0.02` → 엔진이 0.02 로 자른다
   - ④ 엔진 통합: daytrading 결정의 `stop_pct≈0.015`, `tp_pct≈0.025` (변동성이 커도 덮이지 않음)
   - ⑤ 엔진 통합: swing 결정의 `stop_pct` 는 변동성 경로(0.07 이상, `model=="volatility"`)
   - 기존 `trader_core/tests`·`runner/tests` 는 **한 개도 수정하지 않는다**. 깨지면 코드를 고친다.
4. **`trader-agent/presets/daytrading_fixed.json`** (신규, 롤백·전환용)
   ```json
   {"stop_mode_override_note": "profiles.py 의 daytrading 고정 라인을 쓴다(전역 stop_loss_pct 는 스윙용)",
    "screener_profiles": {"daytrading": {"stop_mode": "fixed", "stop_pct": 0.015, "tp_pct": 0.025, "max_stop_pct": 0.02}}}
   ```
   - 형님이 1%/1% 로 바꾸려면 **이 프리셋의 `stop_pct`/`tp_pct` 만** 고쳐 `TRADER_CONFIG` 로 지정하면 되게 한다
     (그 사실을 파일 주석·보고에 명시). `Config.screener_profiles` 가 dict 를 받는지 확인하고,
     받지 않으면 프리셋 대신 `config.py` 기본값으로 두고 그 이유를 보고한다(스키마 억지 변경 금지).

## 검증 (실제 실행, 출력 원문을 보고에 붙인다)

```
cd /mnt/c/Users/jhshi/analyist_dd/trader-agent
/mnt/c/Users/jhshi/Python312-64/python.exe -m pytest trader_core/tests runner/tests -q
/mnt/c/Users/jhshi/Python312-64/python.exe -m pytest trader_core/tests/test_profiles_fixed_stop.py -q
/mnt/c/Users/jhshi/Python312-64/python.exe -c "from trader_core.profiles import profile_for as p; d=p('daytrading'); s=p('swing'); print(d.stop_mode,d.stop_pct,d.tp_pct,d.entry_window,d.close_exit_window,d.hold_overnight); print(s.stop_mode,s.stop_pct,s.tp_pct)"
```
기대: 두 스위트 전부 그린(신규 5케이스 포함) · `fixed 0.015 0.025 09:05-14:30 15:00-15:20 False` · `vol None None`.

## 금지

- `git` 명령 실행 금지(커밋·동기화는 오케스트레이터).
- `max_invested_pct`·`daily_max_trades`·`max_open_positions`·`post_exit_cooldown_*` 변경 금지(사용자 승인 항목).
- swing/close/rank_momentum 의 손절·익절·보유 정책 변경 금지.
- 주문 함수 시그니처 변경 금지, 저널 스키마 변경 금지.
- WP1(진입 필터: `entry_filters.py`, `entry_block_*`) 건드리지 말 것 — 별도 위임이다.
- 위 4파일 외 수정 금지. 리포 밖 파일 생성 금지. 기존 테스트 기대값 수정 금지.

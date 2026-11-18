# 스펙 — 완전 단타 전환: 엣지 게이트 5 + 최대 30분 보유

작성 2026-11-18 11:15. **사용자 지시**: "1번으로 해줘 … 최대 30분만 보유하는 단타매매로 완전히 전환해줘".

근거 실측(오늘 `data/reports/minute_replay.md`, 분봉 실경로 n=12):
1%/3% 기대값 −1.21%/건 · 1.5%/4.5% −1.30% · ATR 트레일 −0.37% — 전부 음(−), 승률 16.7%.
그래서 (a) 진입 문턱을 올려 **품질 낮은 후보를 걷어내고**(edge_score ≥ 5), (b) 보유를 **30분으로 제한**한다
(스윙/오버나이트 금지 = 단타 전환). 값은 사용자 지시값이며 측정으로 재조정하지 않는다.

## 변경 대상 (3파일 + 테스트 1파일)

1. **`trader-agent/trader_core/config.py`**
   - `min_edge_score: int = 5` 로 기본값 변경 (현재 0). 주석에 WHY: 2026-11-18 사용자 지시 + 분봉 실측 음(−)
     → 저품질 후보 차단. `edge_gate_fail_open=True` 는 **유지**(구피드/이력부족은 통과+로그).
2. **`trader-agent/trader_core/profiles.py`**
   - `ScreenerProfile` 에 `max_hold_minutes: int = 0` 추가(0=비활성, 분 단위).
   - `DEFAULT_PROFILES["daytrading"]` 에 `"max_hold_minutes": 30` 추가(사용자 지시 "최대 30분 보유").
     기존 값(entry 09:05-14:30, same_day_close, fixed 1%/3%)은 **그대로** 둔다.
   - swing/close/rank_momentum 은 `max_hold_minutes=0`(비활성) 유지 — 정책 불변.
3. **`trader-agent/trader_core/monitor.py`**
   - `_add_time_exits` 안, `profile = profile_for(...)` 직후에 분 단위 보유 한도 검사를 **추가**한다
     (기존 `time_exit_due`/`time_rules_due` 판정 **앞**에 두어 30분 초과가 항상 우선):
     ```python
     hold_min = int(getattr(profile, "max_hold_minutes", 0) or 0)
     if hold_min:
         entered = _parse_ts(row.get("ts"))          # 기존 헬퍼 재사용(없으면 isoformat 파싱)
         if entered is not None:
             elapsed = (now - entered).total_seconds() / 60.0
             if elapsed >= hold_min:
                 due, reason = True, "max_hold_{0}m ({1:.1f}분 경과)".format(hold_min, elapsed)
     ```
     - 시간대(aware/naive) 방어: `now` 는 aware(KST)이므로 `entered` 가 naive 면 `replace(tzinfo=KST)`.
     - 판정 실패(파싱 불가)는 **차단하지 않는다**(기존 동작 유지, notes 에 한 줄).
   - 청산 신호는 기존 `ExitSignal` 경로 그대로(시장가 매도) — 주문 함수 시그니처 불변.
4. **`trader-agent/trader_core/tests/test_scalp_max_hold.py`** (신규 5케이스)
   ① daytrading `max_hold_minutes==30`, swing/close/rank `==0`
   ② 진입 31분 경과 → `max_hold_30m` 청산 신호 발생(익절/손절 미도달 상태)
   ③ 진입 29분 경과 → 청산 없음
   ④ 스윙 포지션 31분 경과 → 청산 없음(프로파일 0)
   ⑤ `Config().min_edge_score == 5` 이고 `as_dict()` 에 포함

## 검증 (실행 출력 원문 첨부)

```
cd /mnt/c/Users/jhshi/analyist_dd/trader-agent
/mnt/c/Users/jhshi/Python312-64/python.exe -m pytest trader_core/tests runner/tests -q
/mnt/c/Users/jhshi/Python312-64/python.exe -m pytest trader_core/tests/test_scalp_max_hold.py -q
/mnt/c/Users/jhshi/Python312-64/python.exe -c "
from trader_core.profiles import profile_for as p; from trader_core.config import Config
d=p('daytrading'); print(d.max_hold_minutes, d.stop_pct, d.tp_pct, d.entry_window, d.close_exit_window)
print(p('swing').max_hold_minutes, Config().min_edge_score)"
```
기대: 두 스위트 그린(신규 5 포함) · `30 0.01 0.03 09:05-14:30 15:00-15:20` · `0 5`

## 금지

- `git` 금지(커밋·사본 동기화·루프 재기동은 오케스트레이터).
- 자금 한도(`max_invested_pct`·`daily_max_trades`·`max_open_positions`)·쿨다운 변경 금지.
- swing/close/rank_momentum 정책 변경 금지. 주문 함수 시그니처·저널 스키마 변경 금지.
- 위 3파일 + 신규 테스트 외 수정 금지. 기존 테스트 기대값 수정 금지.
- 루프·브리지 프로세스를 **건드리지 말 것**(재기동은 사람/오케스트레이터 판단).

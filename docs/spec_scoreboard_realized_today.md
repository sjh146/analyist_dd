# 스펙 — 계기판에 당일 실현(realized.today) 추가 → 일 손실한도 판정 가능하게

작성 2026-11-17. **사용자 승인**: "응 계속해줘" (직전 보고의 후속 후보 1건).
선행 커밋 `4349274`(objective_state 의 누적↔일 범주 오류 제거)의 마지막 조각이다.
그 커밋 이후 상태는 정직하지만 **판정이 불가능**하다 — 계기판에 당일 실현이 없기 때문:

```
$ python3 -c "import sys;sys.path.insert(0,'scripts');import objective_state as s;print(s.build()['envelope']['notes'])"
['일 손실한도 판정 불가 — 계기판에 당일 실현(realized.today)이 없다. 누적 실현으로 대신 판정하지 않는다',
 '누적 실현손익 -33468.0원 = 자본의 -13.64% (참고 — 일 손실한도와 비교 대상 아님)']
```

## 목표

`scripts/money_scoreboard.py` 가 산출하는 계기판 json 에 **당일 실현 손익 구간 `realized.today`** 를 추가해,
`objective_state.envelope_check` 의 일 손실한도 판정이 실제로 돌게 한다.

## 계약

### 1. `realized.today` (추가 키만, 기존 키 삭제·이름변경 금지)

```json
"realized": {
  "cum": {...기존...}, "recent_30d": {...기존...}, "weekly": [...기존...],
  "today": {"day": "2026-11-17", "closed": 1, "pnl": -6831.0, "fees": 0.0,
            "wins": 0, "losses": 1, "flat": 0, "win_rate": 0.0}
}
```
- 정의: **청산 거래**(`exit_ts` 있음) 중 `exit_ts` 의 날짜(KST)가 오늘인 행.
- `pnl` = 그 행들의 `pnl` 합(= 비용 차감 후 순손익, 저널 규약), `fees` = `fees` 합.
- `closed` = 행 수. 승/패/flat 분류는 기존 `cum` 과 **같은 규칙**(pnl>0 승, pnl<0 패, 그 외 flat)을 재사용한다.
- 오늘 청산이 0건이면 `today` 는 위 스키마에 `closed: 0, pnl: 0.0, fees: 0.0, win_rate: null` 로 **존재해야 한다**(키 누락 금지, None 금지).
- `day` 는 `datetime.now(KST).strftime("%Y-%m-%d")` 가 아니라 **KST 오늘**을 쓴다(런타임 TZ 무관하게 KST 고정 — 기존 `_iso`/KST 관용구 재사용).

### 2. fixture 모드(`--no-live`)에서도 깨지지 않는다

- 픽스처 거래의 날짜는 과거(2026-09-xx)이므로 `today.closed == 0` 이 정상이다.
- 두 번 실행해 산출물이 **바이트 동일**해야 한다(멱등). `today` 계산에 실행 시각·임의값을 넣지 말 것(`day` 만 오늘 날짜).

### 3. `objective_state` 는 수정하지 않는다

- `4349274` 이후 `envelope_check` 는 `(real.get("today") or {}).get("pnl")` 를 이미 읽는다 → 계기판이 키만 주면 자동으로 판정이 돈다.
- **`scripts/objective_state.py` 수정 금지**(이번 범위 밖, 계약 확인용으로만 읽기).

## 변경 대상 (이 2파일만)

1. `scripts/money_scoreboard.py` — `realized` dict 생성부에 `today` 추가. 함수 하나(`_today_realized(trades, now=None)` 권장)로 분리하고, 기존 `_closed()`/승패 분류 헬퍼를 재사용한다. 상단 수식 docstring 에 `당일 실현 = 오늘(KST) exit_ts 를 가진 청산 거래의 pnl 합` 1줄 추가.
2. `tests/test_money_scoreboard.py` — `today` 스키마 회귀 테스트 **추가**(기존 테스트 기대값 수정 금지).
   - 픽스처 3건(청산 2건 + 미청산 1건)으로: `closed`, `pnl` 합, `fees` 합, `win_rate`, 0건일 때 `closed: 0 / pnl: 0.0` 확인.

## 검증 (실제 실행, 출력 원문을 보고에 붙인다)

```
cd /home/jhshi/analyist_dd
/usr/bin/python3 -m pytest tests/test_money_scoreboard.py tests/test_objective.py -q
/usr/bin/python3 scripts/money_scoreboard.py --out-dir /tmp/msb_today            # live 모드(저널 읽기)
/usr/bin/python3 -c "
import json;d=json.load(open('/tmp/msb_today/scoreboard_'+__import__('datetime').date.today().isoformat()+'.json'))
print('today:', d['realized']['today'])"
/usr/bin/python3 -c "import sys;sys.path.insert(0,'scripts');import objective_state as s;print(s.build()['envelope']['breaches'], s.build()['envelope']['notes'])"
/usr/bin/python3 scripts/money_scoreboard.py --no-live --out-dir /tmp/msb_fx1 && /usr/bin/python3 scripts/money_scoreboard.py --no-live --out-dir /tmp/msb_fx1 && md5sum /tmp/msb_fx1/*
```
기대:
- 2026-11-17 실측 기준 `realized.today = {"day": "2026-11-17", "closed": 1, "pnl": -6831.0, "fees": 0.0, "wins": 0, "losses": 1, "flat": 0, "win_rate": 0.0}`
  (저널 id=10 290720 청산 `2026-11-17T09:04:31`, pnl −6,831, fees 0)
- `objective_state.build()` 의 notes 에서 **'판정 불가'가 사라지고** 당일 실현이 한도 내(-6,831 = 자본의 약 -2.78% > -3% 한도)이므로 `breaches` 에 일 손실 항목이 **없어야** 한다. 누적 참고 줄은 유지.
- fixture 2회 실행 md5 동일, pytest 전부 통과.

## 금지

- `git` 명령 실행 금지(커밋은 오케스트레이터).
- `scripts/objective_state.py`·`scripts/risk_limits.py`·`trader-agent/**`·`config/**`·`.env` 수정 금지(읽기만).
- 정책 값(한도·프리셋) 변경 금지. 저널 스키마·쓰기 금지.
- 리포 밖 파일 생성 금지(임시 산출물은 /tmp). 기존 테스트 기대값 수정 금지(today 테스트는 **추가**만).

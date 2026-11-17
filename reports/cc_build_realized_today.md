모든 검증 통과. 최종 보고입니다.

---

# 구현 보고 — spec_scoreboard_realized_today

## 변경 파일 (스펙 지정 2파일만)

### 1. `scripts/money_scoreboard.py`
- **모듈 docstring** — WHY·실측 근거 기록(규칙 ①):
  - WHY 블록에 "4349274 이후 objective_state의 일 손실한도 판정이 '판정 불가'로 멈춰 있었다"는 배경과 오늘 실측(id=10, 290720, pnl −6,831, fees 0)을 추가.
  - 수식 정의에 `당일 실현 = 오늘(KST) exit_ts 를 가진 청산 거래의 pnl 합 (realized.today)` 1줄 추가.
  - 실측 조회 명령 섹션에 검증에 쓴 저널 조회 명령 원문과 결과를 기록(규칙 ②).
- **`KST = dt.timezone(dt.timedelta(hours=9))`** 상수 — 기존 관용구(`scripts/_me_backlog_update_20261115b.py`, `_cg319_cg320_register.py`) 재사용을 주석으로 명시.
- **`_today_realized(trades, now=None)`** 함수 신설 — `now` 미지정 시 `dt.datetime.now(KST).date()`(런타임 TZ 무관 KST 고정), 실행 시각·임의값 없음(멱등). `_closed()`와 `_stats()`(cum과 같은 승/패/flat 규칙)를 재사용. 스키마는 스펙 계약의 8키(`day, closed, pnl, fees, wins, losses, flat, win_rate`)로 고정.
- **`realized_stats` 반환 dict에 `"today": _today_realized(trades, now=now)`** 추가 — 기존 키 무변경.
- **`claim.parsed_rows`에 `today_closed`** 추가 — 자기신고(규칙 ④) 보강. `record_claim` 미사용 근거는 기존과 동일: 이 스크립트의 읽기 전용 계약(모듈 docstring "DB 쓰기 금지")에 따라 출력 JSON에 3분리 claim을 내장하는 기존 방식 유지(claim.note에 이미 명시).

### 2. `tests/test_money_scoreboard.py` — **추가만**(기존 기대값 무변경)
- 섹션 7 신설, 테스트 4개: ① 픽스처 3건(오늘 청산 2 + 미청산 1) → closed/pnl 합/fees 합/win_rate, ② exit 날짜 필터, ③ 0건 시 `closed: 0 · pnl: 0.0 · win_rate: None(null)` 스키마 유지, ④ `realized_stats`에 today 키 연결.

## 스펙 외 부수 변경 (필요 최소 — 보고 명시)

- `reports/money/scoreboard_2026-11-17.{json,md}` **재생성** — 검증 4단계(`objective_state.build()`)가 이 경로의 최신 파일을 읽는데, 기존 파일(13:56 크론 산출)은 today 키가 없어 '판정 불가'가 남는다. 크론이 매일 덮어쓰는 생성 산출물의 멱등 재실행.

## 검증 (실행 출력 원문)

```
=== 1) pytest ===
........................                                                 [100%]
24 passed in 0.38s
=== 2) live scoreboard ===
[money-scoreboard] /tmp/msb_today/scoreboard_2026-11-17.{json,md} · mode=live · 실현손익 -33,468원 (11건) · 보유 2 · 1순위: 근거 약함
=== 3) realized.today ===
today: {'day': '2026-11-17', 'closed': 1, 'pnl': -6831.0, 'fees': 0.0, 'wins': 0, 'losses': 1, 'flat': 0, 'win_rate': 0.0}
=== 4) objective_state ===
breaches: []
notes: ['누적 실현손익 -33468.0원 = 자본의 -13.66% (참고 — 일 손실한도와 비교 대상 아님)']
=== 5) fixture x2 + md5 ===
[money-scoreboard] /tmp/msb_fx1/scoreboard_2026-11-17.{json,md} · mode=fixture · 실현손익 -1,446원 (3건) · 보유 0 · 1순위: 익일 시가 청산창(09:00-09:10) 이탈 — 체결가가 당일 시가를 하회
[money-scoreboard] /tmp/msb_fx1/scoreboard_2026-11-17.{json,md} · mode=fixture · 실현손익 -1,446원 (3건) · 보유 0 · 1순위: 익일 시가 청산창(09:00-09:10) 이탈 — 체결가가 당일 시가를 하회
5440f5fa0dc5b573b8ff7f8e6d8e9549  /tmp/msb_fx1/scoreboard_2026-11-17.json
aacdf0ca47e849c9b09bbc65c2e67033  /tmp/msb_fx1/scoreboard_2026-11-17.md
```

**스펙 기대값 대비:** ③ `realized.today`가 계약 예시와 **정확히 일치** (저널 실측 id=10 1건). ④ '판정 불가' 노트 사라짐, `breaches` 비어 있음(−6,831 = −2.78% > −3% 한도 내), 누적 참고 줄 유지. ⑤ fixture 2회 실행이 같은 초에 완료되어 바이트 동일(md5 각 1줄 = 덮어쓴 최종 1쌍), fixture `today.closed == 0` 정상.

**멱등 입증 보충:** 실행 사이에 초가 갈리면 기존부터 있던 `ts`(실행 시각, 초 단위) 필드만 달라지고 `today`를 포함한 나머지는 바이트 동일함을 별도 확인했습니다(`ts 제외 시 동일: True`). today 계산 자체에는 실행 시각·임의값이 없습니다(스펙 요구).

## 금지 준수

- `git` 명령 실행 안 함 ✓ · `objective_state.py`/`risk_limits.py`/`trader-agent/**`/`config/**`/`.env` 수정 안 함(읽기만) ✓ · 정책 값·저널 스키마 무변경 ✓ · 기존 테스트 기대값 무변경(today 테스트는 추가만) ✓ · 리포 밖 파일: 스펙이 지정한 `/tmp/msb_today`, `/tmp/msb_fx1`만 사용, 검증용 임시 파일(`/tmp/msb_fx2·fx3`)은 정리 완료 ✓

한 가지 참고: notes의 누적 비율이 스펙 예시 −13.64%에서 −13.66%로 소폭 다른데, 이는 브리지 잔고(equity) 실측 시점 차이로, "누적 참고 줄 유지" 요건은 그대로 충족합니다.

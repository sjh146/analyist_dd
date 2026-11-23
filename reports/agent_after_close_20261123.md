# 2026-11-23 장 마감 후 점검 (자동 실행, 15:41 KST)

점검 방식: 로그·DB·브로커 read-only 조회만. 주문/설정 변경/프로세스 재기동 없음.

## 1) 트레이더 루프 마감 처리

- `runner.log` L47264 = 15:05:08 pid=6160 루프 재기동(`--loop --live --config config_live.json`), 마지막 사이클 15:40:11.
- 마감 로그(L48157~48160):
  - `cycle: ts=2026-11-23T15:40:11 phase=close market_open=False`
  - `CLOSE cancelled 0 outstanding order(s)`
  - `CLOSE summary pnl=-1229.0 entries=3`
  - `NOTIFY market close: pnl -1229.0 entries 3`
- `loop_state.json` daily: `entries=3`, `closed=true`, `start_equity=245,576` → `end_equity=244,347`, `closed_pnl=-1,229`, reconcile `issues=0` (unfilled/needs_price/bogus_exits 전부 빈 배열), `halt=false`.
- 15:00~15:40 구간 매도/청산 주문 0건, `exits=0` — 12:09 이후 보유 포지션 없음(`monitor: no open positions`). Traceback/exception 0건.
- 오류: 구간 내 `bridge request failed: timed out [connection_failed]` 34건(R4 balance fetch 20, monitor heartbeat 12, DECIDE aborted 1 등). `bridge_sup.log` 기준 14:40:20~15:29:36 "bridge not connected" — Creon 세션 끊김. 15:30경 자동 복구되어 15:40 close 잔고 조회는 성공(end_equity가 현재 브로커 equity와 일치).
- 브로커 read-only 실측(15:42): `/positions` = `[]`, `/orders` = `[]`, `/balance` equity 244,347 / positions_count 0, d2_deposit 244,347. → 잔여 포지션 없음 확인.
- 당일 체결 3건(journal, 전부 12:09 이전):
  | id | 종목 | 수량 | 진입 | 청산 | 사유 | journal pnl |
  |---|---|---|---|---|---|---|
  | 23 | 204620 글로벌텍스프리 | 13 | 6,100.85 | 6,100 | monitor stop | -11 |
  | 24 | 439090 마녀공장 | 4 | 17,100 | 16,920 | tick stop | -720 |
  | 25 | 195940 HK이노엔 | 2 | 37,905.5 | 38,100 | max_hold_90m | +389 |
  journal 합계 = **-342**.

### ⚠ 발견 1: journal 실현손익 ≠ 브로커 실현손익 (-342 vs -1,219, 차 877원)
- 브로커 `/daily_pnl` 평균체결가: 204620 매수6,100/매도6,067(-429), 439090 17,102/16,873(-916), 195940 37,905/37,968(+126) → 실현 **-1,219**.
- 에쿼티 기준 일일 -1,229와 브로커 실현 -1,219는 정합. journal의 exit_price(6,100/16,920/38,100)가 실제 체결 평균가보다 낙관적으로 기록됨. `fees` 컬럼도 전건 0.0.
- 204620은 09:11:06.81 진입 → 09:11:07.26 청산(0.4초) `monitor stop` — 즉시 손절 발생, 진입가 대비 -11원. 체결/기록 경로 점검 필요.

## 2) 발행 관문 (verify_daily_pipeline_gates.py)

```
OK G1 daytrading 후보 생성: items=20 generated_at=2026-11-23T14:40:14+09:00
OK G2 close 상한가 제외 메타: excluded_limit_up=4 max_day_change=0.25 date=2026-11-23
OK G3 피드 신선도(close/swing): {'close': 20, 'swing': 20} generated_at=2026-11-23T14:40:14+09:00
OK G5 signal_date 거래일: {'close': '2026-11-20', 'swing': '2026-11-20', 'daytrading': '2026-11-20'}
OK G4 당일 일봉 재시도 판정: 미도래(22:35 이전 실행 아님)
결과: 5/5 통과
```
G2(close 상한가 메타)는 14:40 close_job 이후 기준으로 통과.

## 3) 14:40 close_job

- `reports/cron_close_job.log`: `[2026-11-23 14:40:01] 종가 스크리너 잡 시작` → `14:40:13 피드 발행` → `14:40:14.581 발행 완료 (42,484 bytes, edge_scored=60/60)`.
- 후보 수: close 20건(min 58.7 / median 65.1 / max 82.8), swing 20건, daytrading 20건, 각 edge_scored 20/20.
- rc: `/etc/cron.d/analyist_dd` 23행이 출력만 리다이렉트하고 rc를 남기지 않음 → **rc 미기록**. 성공 판정은 "발행 완료" 로그 + feed `generated_at=2026-11-23T14:40:14+09:00` + G1/G3/G2 통과로 대체.
- ⚠ 발견 2: 로그 경고 `[close] signal_date 3~300일 전`. 스팩 3종의 signal_date가 2026-02-03(454640) / 2026-01-27(446150) / 2026-08-31(471050). 루프 신선도 게이트가 해당 후보를 차단(STALE skip 다수, 15:30 이후 valid_until 만료로 전 종목 skip은 정상).

## 결론

- 마감 처리·관문(5/5)·14:40 발행 모두 정상, 포지션 0, 오늘 신규 진입 3건 전부 12:09 이전 청산.
- 미결 ① journal exit_price가 브로커 체결가와 불일치(877원 과대), fees 미기록 — 계측 버그 후보(내일 우선 수정 권장).
- 미결 ② 14:40~15:29 Creon 브리지 세션 끊김으로 R4/모니터 공백 약 49분(포지션 없어 실손실 영향 없음).
- 미결 ③ close 후보에 스팩 3종(오래된 signal_date) 혼입 — 스크리너 signal_date 갱신 문제.

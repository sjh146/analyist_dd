# PHASE 4 DESIGN — Hermes 트레이더: 장중 운영 루프 (모의계좌)

> 작성: 2026-09-04 밤, BC-250 Hermes. 상태: 설계안 — 리뷰 후 구현(opencode P4a).
> 전제: Phase 2b/2c/3 완료, 월요일 장중 실검증(MARKET_DAY_CHECKLIST.md A~E)이 이 설계의
> 실동작 전제 조건.

## 0. 확정된 설계 방향 (사용자 리뷰 전 임시 기본값 — 각 항목 반대 의견 있으면 교체)

| 결정 | 기본값 (권장) | 비고 |
|---|---|---|
| D1. 실행 주체 | **노트북 로컬 자동화**: Windows 작업 스케줄러로 장중 루프 구동 | 브리지 127.0.0.1 전용, 포트 개방 불필요. paul(노트북 봇) 역할은 Q4로 계속 열림 |
| D2. '유리 판단' | **v1 규칙 기반** (LLM 없음, 결정적·비용 0) | 레짐/뉴스 데이터 소스 확정 후 LLM 단계 추가(Phase 5 전환점) |
| D3. 주문 방식 | **자동 주문** (모의 한정, 게이트+캘리 한도 내) | 파일럿 첫 주는 dry-run 리포트 병행 → 사용자 확인 후 실주문 전환 |

열린 결정(구현 전 확정 필요): 알림 채널(Discord 웹훅 URL), paul 역할(Q4), 레짐 데이터 소스(아래 §3).

## 1. 목표

Phase 3의 trader-core(64bit, 노트북)를 **장중 자율 루프**로 구동해 모의 매매를 수행하고,
결과를 저널에 쌓아 Phase 5(학습 루프)의 입력을 만든다. BC-250 Hermes는 원격 감시·지휘
(상태 조회, 킬스위치, 리포트) 역할.

```
[노트북, 64bit Python]                        [BC-250 Hermes (이 에이전트)]
run_market_loop.py  ──로컬 상태 파일/저널──▶   원격 조회: loop_state/journal/브리지 로그
  ├ trader_core.engine (게이트+캘리)             원격 지휘: kill_switch 파일 생성/해제 (ssh)
  ├ trader_core.monitor (스탑/익절)              Discord 리포트: 아침 브리핑·이벤트 알림
  └ 브리지 localhost:8100                        (알림 채널 확정 필요 — D-열림)
```

## 2. 장중 루프 명세 (run_market_loop.py, 신규)

상태 전이: `IDLE → CHECK → DECIDE → ACT → MONITOR → (장마감) CLOSE` — 매 사이클 5분
(장중 09:00~15:25), 설정 `TRADER_CORE_*`/config.json으로 주기·시간 조정.

1. **CHECK (상태 확인)** — 하나라도 실패하면 이번 사이클 SKIP(사유 기록):
   - 브리지 `/health` connected + `/balance`(cash·equity) 수신
   - 피드 신선도: `ScreenerFeedClient.fetch()` + `is_fresh` (feed_max_age 24h)
   - 킬스위치 파일 부재, 오늘 거래 수 < daily_max(저널 `trade_count_today`)
   - 루프 상태 파일(`loop_state.json`)에 HALT 플래그 없음 (자체/원격 중단 반영)
2. **DECIDE (후보 평가)** — `TraderEngine.evaluate()` 재사용, 규칙 추가:
   - 전일 export 후보(close/swing/daytrading)를 6게이트 통과분만 추림
   - **신규 규칙 R1(레짐 대용)**: 후보군 분포 점검 — top-N 평균 점수 < 임계(예: close 88)
     또는 유효 후보 0건이면 오늘 전체 SKIP (추세 악화 대용, D2 규칙 기반)
   - **신규 규칙 R2(진입 제한)**: 당일 이미 N건(기본 3) 진입했으면 신규 진입 보류
     (daily_max_trades=10은 상한, R2는 실질 보수 운용)
3. **ACT (주문)** — 플랜 1건씩 `engine.execute()` (브리지 게이트가 주문 슬롯 페이싱),
   성공/실패 모두 저널 `record_decision`/`record_trade`. 지정가(전일 종가) 기본 — 장중
   괴리 크면(전일 종가 ±3% 초과) 해당 후보 SKIP(가격 이탈, 규칙 R3).
4. **MONITOR (포지션 감시)** — `PositionMonitor.heartbeat()` 5분마다:
   - 스탑(7%)/익절(15%) ExitSignal → 시장가 청산(`engine` 경유, CWE-20 참조가격 게이트)
   - **신규 규칙 R4(일일 손실 한도)**: 실현+평가 손실 합이 equity 3% 초과 시 →
     전 포지션 시장가 청산 + `kill_switch.txt` 생성(사유 "daily_loss_limit") + HALT
   - **신규 규칙 R5(뉴스 게이트, 9/4 추가)**: ACT 전에 후보 종목별 BC-250 뉴스 게이트
     (`http://100.127.186.48:8082/newsgate`, trader-feed systemd unit) 조회 —
     부정 이벤트(소송/규제/리콜/유상증자·감자/CB·BW/부도 등) 또는 감성 ≤ -0.5 뉴스가
     있으면 해당 종목 SKIP. 게이트 장애 시 fail-closed(신규 진입 차단, 설정으로 완화 가능).
     SNS(종토방) 감성은 sns_post_features 스키마 확정 후 게이트에 확장 예정.
5. **CLOSE (장마감 15:25~)** — 미체결 주문 취소(브리지 `/orders`→`/cancel`), 상태 파일에
   일일 요약(진입/청산/보유/손익) 기록 → BC-250이 Discord 브리핑(저널 `summary()`)

**중단 조건(사이클 시작 시 재확인)**: kill-switch 파일 / 원격 HALT(loop_state.json) /
일일 손실 한도(R4) / 피드 단절(fresh 실패 N회 연속 → HALT) / 주문 연속 실패(rate_limited 등).

## 3. v1 게이트 데이터 소스 한계 (레짐/승률)

- 피드 `scoring_summary.screener_stats`가 현재 **빈 값** → 캘리 prior는 config 기본값
  (0.55/1.0/1.0) 사용, 스크리너별 통계가 채워지면 자동 전환(Phase 3 `_prior_stats`).
- 시장레짐 필드는 export에 없음(reason 문자열 "시장레짐+"만 존재) → v1은 R1(후보 분포)
  + 개별 후보 `reason` 부정 패턴(예: "하락", "악화", "시장레짐-") SKIP 규칙으로 대체.
  **확정 필요**: (a) 스크리너/채점 export에 레짐 필드 추가(BC-250 파이프라인, 권장) 또는
  (b) 지수 시세 소스(브리지에 지수 종목 등록) — 월요일 검증 후 결정, 그 전까지 R1/R2/R3.

## 4. 산출물

**P4a — 노트북 루프 (opencode 구현, repo `runner/`)**
- `runner/loop.py` — 상태 머신 + 5분 사이클, 설정·중단 처리, 상태 파일(`loop_state.json`)
- `runner/notify.py` — 알림 스텁: 로컬 로그 + (URL 설정 시) Discord 웹훅 POST
- `runner/schedule.bat` + 등록 안내 — Windows 작업 스케줄러(평일 08:58 시작, 15:35 종료)
- `runner/tests/` — unittest: 상태 전이(킬스위치/HALT/손실한도/장마감), R1~R4, notify 스텁,
  전체 루프 fake 브리지+fixture 피드 e2e — **Linux에서 전부 통과**(기존 원칙)
- 실행: `python run_market_loop.py --once|--loop --dry-run|--live`

**P4b — BC-250 원격 ops (Hermes 도구/스킬, 내가 작성)**
- `~/AUTONOMY/tools/trader_remote.sh` — ssh 상태 조회(loop_state/journal 요약/브리지 로그),
  킬스위치 원격 생성·해제(사유 기입), 루프 시작/중지 신호
- 스킬 `creon-trader-ops` (또는 creon-bridge-ops 확장) — 장중 점검·리포트 절차
- 크론: 평일 08:55 아침 브리핑(전일 저널 summary → Discord), 15:40 장마감 리포트
  (알림 채널 확정 후 등록)

**P4c — 파일럿 일정**
- 월요일 MARKET_DAY_CHECKLIST A~E 통과 → P4a 배포 → **1주 dry-run 리포트 모드**
  (실주문 대신 플랜 로그) → 사용자 확인 후 자동 주문 전환(D3)

## 5. 안전 (모의 전용 강화)

1. 실전 주문 경로 없음 — 코드에 실계좌 주문 불가(모의 계좌 7770****** 전용, goods_flag 검증)
2. 주문 전 6게이트 + R1~R3 전부 통과, 주문 슬롯은 브리지 게이트가 페이싱
3. 중단 최우선: kill-switch 파일(수동·원격·자동 R4) 존재 시 신규 주문 차단 + 전량 청산
4. 일일 한도: daily_max_trades(10) 상한 + R2(3건) + R4(equity 3% 손실 self-halt)
5. dry-run 기본값 유지 — `--live` 명시 + 상태 파일 확인 시에만 실주문

## 6. 구현 로드맵

- [x] 설계 리뷰 (사용자 승인 9/4 — D1~D3 기본값으로 진행, 응답 없어 권장안 적용)
- [x] P4a 구현 완료 (9/4 밤, opencode): `runner/` 장중 운영 루프 (커밋 2527b37)
  - 상태머신 run_once/run_forever + R1~R4 (rules.py 순수함수) + StateStore(원자적 상태 파일,
    HALT/resume) + Notifier(Discord 웹훅 옵션, 기본 로그) + CLI `run_market_loop.py`
    (status/run --once|--loop --dry-run|--live/halt/resume, dry-run 기본+live 가드)
  - e2e: MockBridge+fixture 피드+주입 now — 월요일 장중 plan 생성, 킬스위치/HALT 차단,
    R4 손실 시 전량청산+킬스위치 기록, 장마감 CLOSE(미체결 취소+요약), 일 경계 리셋
  - 테스트 58건 Linux 통과 + trader_core 82건 회귀 OK
  - 리뷰 중 보강: 실브리지 주문 응답({order_no} 형태, status 키 없음)도 fill로 인식해
    R2 일일 진입 한도가 라이브에서 우회되지 않게 패치 (test_live_bridge_order_shape 추가)
- [x] P4b 원격 ops 구축 (9/4 밤): `~/AUTONOMY/tools/trader_remote.sh` (status/loop-state/journal/
      bridge/kill/unkill/halt/resume/logs — ssh python-stdin 방식, 한글 사유 UTF-8 안전) +
      creon-trader-ops 스킬 v2 갱신(시스템 맵·중단 3계층·진단). 실기기 라운드트립 검증 완료
  - [ ] 아침/마감 리포트 크론 — 알림 채널(Discord 웹훅 URL) 확정 후 등록
- [x] 모니터링 연동 (9/4 밤): news-analyzer·redis 재기동(30분 주기 재개), screener_stats
      채점이력 누적화, **R5 뉴스 게이트** (trader-feed 8082 + runner R5, 테스트 63건·노트북 교차 OK)
      — 남음: SNS(종토방) 수집 재개 확인·크론, sns 감성 게이트 확장(스키마 확정 후)
- [ ] 월요일 장중 검증(A~E) → P4a 배포 → dry-run 파일럿 1주 → 자동 전환 검토

*Phase 4는 모의계좌 운영이 목표. 실전 전환은 별도 사람 승인(설계 원칙 12장).*

# trader-agent — Creon 트레이더 에이전트 (Windows 노트북 실행)

> **이 디렉터리는 `analyist_dd` 저장소의 일부다**(2026-11-13 통합). 별도 `trader-agent.git` 은
> 이력 보존용으로만 남아 있고, 새 커밋·푸시는 `analyist_dd` 한 곳으로 한다.
> 실행 경로는 그대로다(Windows 노트북의 `C:\Users\<user>\analyist_dd\trader-agent`).

BC-250 `analyist_dd` 데이터를 받아 Windows 노트북에서 Creon(대신 CybosPlus) **모의 매매**를
수행하는 시스템. 설계의 단일 기준점: `~/AUTONOMY/CREON_TRADER_PLAN.md` (BC-250).

## 구조 원칙
- Creon COM = Windows + **Python 32bit 전용** → `creon-bridge`(32bit) 가 모든 COM 담당
- 나머지(트레이더 코어·위험관리·에이전트) = **64bit** — localhost API로 브리지 호출
- 위험관리: 스탑로스 + Kelly(분수 캘리, f≤0.25) — 기존 `~/analyist_dd/services/` 코드 재사용
- 매매 결정·결과는 저널(SQLite)에 기록 → 학습 루프로 발전

## 현재 상태
- [x] Phase 0: 기본알고리즘 원본 수집 (`creon_src/`, 노트북 C:\Users\jhshi\크레온 기본알고리즘)
- [x] Phase 1: `#######[파이썬]` 마커 기준 37개 모듈 추출 + PyQt5 UI 제거 (`creon_extracted_v1/`)
      추출기: `~/AUTONOMY/tools/creon_code_extractor.py` (제거 조각은 removed/ 보존, REVIEW 마커)
- [x] Phase 2: creon-bridge v1 (32bit) — 로그인/주문/잔고/실시간 (`bridge/`, opencode 구현,
      테스트 29건 통과, api_notes.md에 COM 인덱스 표) — 노트북 배포·32bit selftest 완료
- [x] Phase 2b: 모의 CybosPlus 실연결 확인 (계좌 7770******, health/잔고/포지션/미체결/구독 OK)
      ⚠ 남음(월요일 장중): 실시간 틱 수신 + 모의 주문 1회 라운드트립 → MARKET_DAY_CHECKLIST.md
- [x] Phase 2c: /balance에 현금 필드 추가 (CpTdNew5331A 현금주문가능금액·예수금·가능예수금 +
      CpTd6033 공식 헤더 equity/D+2 — 레거시 인덱스 오류 확인, 공식 헬퍼 표 기준) + 주문 요청제한
      게이트(CpCybos.GetLimitRemainCount(0) 슬롯 대기, rate_limited) — 테스트 35건
      ⚠ 모의서버 5331A 지원 여부는 월요일 A항목에서 확인 (미지원 시 d2_deposit 폴백 자동)
- [x] Phase 3: trader-core (64bit) — 시그널 소싱 + 리스크 + 저널 (`trader_core/`, opencode 구현,
      테스트 82건 통과, 순수 stdlib, Linux·노트북 64bit 모두 통과) — PHASE3_BRIEF.md에 명세
      (feed_max_age 기본 24h — 전일 19:30 export를 다음날 장중까지 유효로)
- [ ] Phase 4~6: Hermes 트레이더(운영 루프·킬스위치), 학습 루프, 실모의 운영

## trader-core CLI (BC-250 dev / 노트북 64bit)
```
python3 run_trader_core.py status --mock            # 브리지+피드+계좌 상태 (mock=내장 모의 브리지)
python3 run_trader_core.py scan --dry-run --mock    # 게이트 평가 리포트 (기본 dry-run, exit 3=차단)
python3 run_trader_core.py close-all --reason ...   # 전량 청산 계획 (확인 후 실행)
```
- 게이트 순서: feed_fresh → kill_switch → market_hours → daily_trade_count →
  balance_position_amount → signal_sane (전부 통과 시에만 주문 계획)
- 리스크: 캘리 f*=p−q/b (cap 25%, quarter-Kelly 기본), SL 7%/TP 15%, 시장가 CWE-20 참조가격 가드

## 실행 (노트북, 중요)
- CybosPlus API는 **관리자 권한 필수**: `bridge_run_admin.bat` 우클릭 → 관리자 권한으로 실행 (UAC 확인)
  - 일반 권한으로 실행하면 `/health`가 `connected:false` — CpStart(관리자)와 권한이 달라 연결이 안 보임
- 전제: 크레온 HTS(C:\CREON\HTS\CBP.exe) 로그인 — CYBOSPLUS 선택 + **모의투자 토글 ON(초록)** + HTS 창 유지

## 배포
- 노트북 실행 경로: `C:\Users\jhshi\analyist_dd\trader-agent` (SSH jhshi@100.104.139.79, 키 ~/.ssh/bc250_win_ctl)
- 노트북 파이썬: `C:\Users\jhshi\Python311-32`(32bit 브리지) / `Python312-64`(64bit 코어)

## 샘플 코드 규약 (추출에서 확인, 브리지 구현 시 준수)
- 주문 객체 `CpTrade.CpTd0311`, 종목코드 `A` 접두어(예: A005930), 호가구분 `SetInputValue(8, "01")`
- 에러 처리: `BlockRequest()` 반환값 + `GetDibStatus()/GetDibMsg1()`

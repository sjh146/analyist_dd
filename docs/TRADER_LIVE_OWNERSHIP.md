# 트레이더 라이브 경로 소유권

- 소유: **터미널 Hermes 세션** (사용자 직접 지시로 가동)
- 게이트웨이 Hermes(디스코드)는 이 경로를 **읽기 전용**으로만 다룬다:
  - `C:\Users\jhshi\trader-agent\config_live.json` (라이브 설정)
  - `trader-agent/trader_core/**` (주문·청산·사이징 로직)
  - 루프 프로세스의 기동·종료
- 게이트웨이 담당: `analyist_dd` 백로그·문서·크론·리포트, 그리고 위 경로의 **상태 보고**
- 사고 이력 2026-11-26: 게이트웨이가 `min_edge_score_by_screener.daytrading` 을 4→5 로 덮어써
  터미널 세션의 결정(ffb523f "사용자 선택 2번: 5→4")을 뒤집었다. 원복 완료.

# 스펙 — ETF 괴리 분석 오염 제거 + event_study 버그 수리

작성 2026-11-20 16:55. 실측 근거 2건:
1. **event_study 전 지평 n=0** — episodes 시작가 조회가 스냅샷 키와 타입이 달라 전부 skip.
2. **가짜 15% 괴리** — 09:02 `0193T0 KODEX SK하이닉스단일종목레버리지` price 4,255(전일 종가, 체결 0)
   vs NAV 3,683 → premium **+15.53%**. 09:04 price 3,730 → −0.11%. 즉 **개장 직후 미체결 가격**이 만든 아티팩트.

## 변경 대상 (이 파일만)

`scripts/etf_premium_analysis.py` + `tests/test_etf_premium_analysis.py`

### 1. event_study 타입 정규화

- 스냅샷 ts·episode start 를 **동일 키 타입**으로 정규화(둘 다 `datetime` 또는 둘 다 ISO 문자열).
  `price.get((code, ep["start"]))` 가 실제로 값을 찾도록.
- 검증: 오늘 데이터에서 `+5min` n>0 이어야 한다(현재 0).

### 2. 유효 스냅샷 필터 (오염 제거)

- 신규 CLI 옵션: `--min-volume`(기본 **1**), `--skip-open-min`(기본 **5**)
- 규칙: ① `nav` 가 null 인 스냅샷 제외 ② `volume < min-volume` 제외
  ③ 개장 후 `skip-open-min` 분 이내(09:00~09:05) 스냅샷 제외
- 필터 적용 결과를 리포트에 `filtered` 블록으로 남긴다: `{dropped_no_nav, dropped_no_volume, dropped_open_window}`.
- **리포트 상단에 필터 사유를 명시**하고, 필터 전/후 에피소드 수를 함께 표기.

### 3. 테스트 3개 추가

① 같은 ts 타입에서 event_study n>0 ② volume=0 스냅샷이 괴리에서 제외 ③ 개장 5분 이내 스냅샷 제외

## 실행 검증

```
/usr/bin/python3 -m pytest tests/test_etf_premium_analysis.py -q
/usr/bin/python3 scripts/etf_premium_analysis.py --date 2026-11-20
```
- 기대: `event_study["+5min"]["n"] > 0`, 에피소드 수가 1,092 → 감소, 15% 짜리 가짜 에피소드 사라짐.

## 금지

- 버전관리 금지. 수집기(`etf_premium_collect.py`) 수정 금지. DB 쓰기 금지. 기존 테스트 기대값 수정 금지.

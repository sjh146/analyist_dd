# 스펙(후속) — ETF 괴리 '정지 가격' 제거

작성 2026-11-20 17:55. 근거: 오염 제거(volume>0 · 개장 5분 제외) 후에도
`0194R0 KIWOOM SK하이닉스선물단일종목레버리지 +15.76%(09:05~09:17)` 가 남았다.
09:05:01 은 개장 5분 창 밖이고 volume≥1 이지만, **가격이 전일 종가에 멈춰 있었다**
(09:00 4,255 → 09:04 3,730 사례와 같은 계열).

## 변경 대상

`scripts/etf_premium_analysis.py` + `tests/test_etf_premium_analysis.py` (이 둘만)

### 1. 정지 가격 규칙

- 신규 옵션 `--stale-minutes`(기본 **3**)
- 정의: 종목별로 **직전 `stale-minutes` 분 동안 price 가 한 번도 변하지 않은** 스냅샷을
  `stale` 로 표시하고 괴리 계산·에피소드 생성에서 제외한다.
- 에피소드 시작 스냅샷이 stale 이면 그 에피소드 전체를 버린다.
- `filtered` 블록에 `dropped_stale` 카운트를 추가하고, 리포트 상단 사유 문자열에도 포함.

### 2. 테스트 3개 추가

① 가격이 3분 정지한 스냅샷이 제외된다 ② 가격이 매분 변하면 제외되지 않는다
③ stale 시작 에피소드가 통째로 빠진다

## 검증

```
/usr/bin/python3 -m pytest tests/test_etf_premium_analysis.py -q
/usr/bin/python3 scripts/etf_premium_analysis.py --date 2026-11-20 --index-code KOSPI
```
- 기대: `dropped_stale > 0`, 최대괴리 1위에서 **+15%대 에피소드가 사라짐**, event_study n 은 유지(>1,000).

## 금지

버전관리 금지 · 수집기 수정 금지 · DB 쓰기 금지 · 기존 테스트 기대값 수정 금지.

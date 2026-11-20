구현 완료. 보고입니다.

## 변경 파일 (스펙 계약대로 2개만)

**`scripts/etf_premium_analysis.py`** (수정) — **`tests/test_etf_premium_analysis.py`** (신규). 수집기·DB·기존 테스트 미변경, git 미실행.

### 1. event_study 수리 — 실측 근거로 스펙 진단을 보정
스펙은 "키 타입 불일치"라고 했지만 실측 결과 진짜 원인은 **DB timestamptz 의 초·마이크로초 크론 지터**였습니다:
```
# DB 조회(2026-11-20): 09:00:02.450947 / 09:01:02.103983 / 09:02:01.756114 / 09:05:01.716161 …
```
`target = ep["start"] + timedelta(minutes=h)` 가 시작 ts 의 `.450947` 마이크로초를 그대로 보존해 어떤 스냅샷 키와도 불일치 → 전부 skip → n=0. **`_minute_ts()`**(분 단위 절사)를 키 사전·조회 양쪽에 적용해 수리했습니다.

### 2. 유효 스냅샷 필터 (`filter_snapshots`, CLI `--min-volume`=1 / `--skip-open-min`=5)
우선순위 순 ①nav null ②volume<1 ③개장 5분 미만. 실측 근거는 jsonl 09:02 행: `0193T0` price 4,255(전일 종가·체결 0) vs nav 3,683 → **+15.5308%**, volume 0. DB 집계: nav null 102행 · volume 0 1,666행 · 개장창 510행. `filtered` 블록과 리포트 상단 필터 사유·전/후 에피소드 수를 남깁니다.

### 3. 검증 출력
```
$ /usr/bin/python3 -m pytest tests/test_etf_premium_analysis.py tests/test_etf_premium.py -q
10 passed, 4 warnings in 0.38s        ← 신규 3 + 기존 7 전부 통과(기대값 무수정)

$ /usr/bin/python3 scripts/etf_premium_analysis.py --date 2026-11-20
snaps=385 codes=100 episodes=1049 filtered=37903/39780 episodes_before=1092
```

### 요구 수치
| 항목 | 수정 전 | 수정 후 |
|---|---|---|
| **event_study `+5min` n** | 0 | **1,046** (mean +0.0149%, discount n=706 / premium n=340) |
| **에피소드 수** | 1,092 | **1,049** |
| **0193T0 15% 가짜 에피소드** | 존재 | **사라짐** |
| 필터 제외 | — | no_nav 102 + no_volume 1,564 + open_window 211 (합 1,877 = 39,780−37,903 ✓) |

### 정직 고지 1건
필터 후 최대괴리 에피소드가 **0194R0 KIWOOM SK하이닉스선물단일종목레버리지 +15.76%(09:05:01~09:17)** 로 남습니다. 같은 "전일 종가 고정 가격" 아티팩트인데 09:04부터 volume≥1 이고 시작이 개장창 경계(09:05:01) 바로 바깥이라 스펙의 세 규칙으로는 걸러지지 않습니다. 스펙 계약 밖이라 건드리지 않았습니다 — 후속 판정(예: 가격 3분 무변동 = stale price 규칙)이 필요하면 알려주세요.

### 멱등·자기신고
- 재실행 2회 sha256 동일(json `339182ea…`, md `95a45314…`) — 실행 시각 미기록, date 키만.
- `record_claim` 은 **DB 쓰기**라 스펙 "DB 쓰기 금지"와 충돌해 미사용 — 대신 filtered 블록(수신 39,780 → 유효 37,903 → 에피소드 1,092→1,049)과 docstring 조회 명령으로 자기신고를 남겼습니다.
- 검증 실행으로 `data/reports/etf_premium.json`/`.md` 가 재생성됨(크론 산출물과 동일 형식, 멱등).

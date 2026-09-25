# /review — scripts/build_screener_stats.py

검토 범위: 스크립트 본체 + 기록자(`scripts/pnl_backtest.py`, `scripts/record_strategy_run.py`) + 소비 경로(`scripts/feed_export.py`, `services/strategy-agents/app/risk_management/position_sizer.py`) + 계약 문서(`docs/TRADER_INTEGRATION.md`, DB 스키마). 파일 수정 없음.

> ⚠️ 참고: 요청하신 trader-agent 실코드 `/mnt/c/Users/jhshi/analyist_dd/trader-agent/trader_core/risk.py` 읽기는 세션 권한(작업 디렉터리 `/home/jhshi/analyist_dd` 제한)으로 **차단**됐습니다. 대신 repo 안에 있는 소비자 코드(`position_sizer.py`의 `calculate_kelly_fraction` — repo 유일의 kelly 구현)와 피드 계약 문서를 근거로 판단했고, 해당 부분은 확신도를 낮춰 표시했습니다.

---

## 요청하신 4개 확인사항

### 1) `strategy_runs.meta` 키 일치 — **일치함 (워크포워드 분기 한정)**
- 기록: `scripts/pnl_backtest.py:273-275` — `"win_rate": m['win_rate'], "n_trades": m['n_trades'], "avg_win_pct": …, "avg_loss_pct": …` (walk-forward 분기만 기록)
- 읽기: `scripts/build_screener_stats.py:96-99` — `win_rate`, `avg_win_pct`, `avg_loss_pct`, `n_trades` — 이름 정확히 일치.
- 단, **단일 백테스트 분기**(`pnl_backtest.py:339-346`)와 **시나리오 분기**(`:304-312`)는 avg 키를 기록하지 않음 → 이 행들은 `build_screener_stats.py:100`에서 항상 스킵됨(설계 의도, docstring 14행). 스탯의 실질 소스는 워크포워드 행뿐.
- `meta`는 JSONB(`init-scripts/postgres/04_strategy_runs.sql:13`)이고 psycopg2가 dict로 반환하므로 `isinstance(meta, dict)`(`build_screener_stats.py:94`)는 정상. 키 문제 없음. **확신도: 높음**

### 2) `shrink()` 경계 처리 — **b≤0에서 f\*≤0 판정이 성립하지 않음 (현재는 도달 불가)**
- `build_screener_stats.py:63-65`: `b=0` → `bs = 1+(0-1)*0.5 = 0.5` → **클램프로 0.8에 부활**. `ps=0.62`면 `f = 0.62 − 0.38/0.8 = +0.145 > 0` → `(0.62, 0.8, 0.145)`라는 **유효한 튜플이 반환**됨. b≤0(음수 포함)은 전부 0.8로 클램프되므로 "f\*≤0 → None" 판정은 `ps > 0.4444`에서 깨짐.
- `p=0` → `ps=0.25` → 클램프 0.45로 마스킹, `bs=2.0`이면 `f=+0.175>0` 통과 — 측정 승률 0%인데 45%로 발행됨.
- `p=None`/`b=None` → 산술 TypeError 크래시.
- **완화 요인**: `main()` 경로에서는 `build_screener_stats.py:100`(`not aw`/`not al` 필터) + `:136` 덕에 `p∈(0,1)`, `b>0`이 보장되어 이 입력들은 현재 도달 불가. 함수 자체의 경계 판정은 틀리지만, 호출부가 막고 있는 상태. **확신도: 판정 결함 높음 / 운영 영향 낮음**

### 3) '10분 배치 필터' — **단일 배치에는 맞지만, 두 가지 운영 패턴에서 오작동**
- 단일 배치 span은 문제없음: 워크포워드 3창은 tight loop에서 창마다 `record_run` 호출(`pnl_backtest.py:255-277`), `simulate`는 소형 패널(50종목×90일)에서 초 단위 → 3행이 몇 초 안에 기록됨. 600초로 한 배치는 충분히 잡힘.
- **오작동 1 — 혼합**: 10분 안에 다른 `backtest_pnl` 실행이 있으면 무조건 섞임. 컬렉터는 `meta`의 `mode/k/hold/threshold/window`를 전혀 보지 않고(`build_screener_stats.py:90-99`) 전부 하나의 하드코딩된 `"close"` 버킷에 합산(`:102`). 승격일(2026-09-25)에 챔피언/챌린저 워크포워드를 연달아 돌리면 — 주석(`:75-76`)이 막으려던 바로 그 시나리오 — **두 모델의 통계가 섞임**. `--short`/롱, 파라미터 그리드도 같은 tool.
- **오작동 2 — 전체 탈락**: 앵커(`:88` `newest = rows[0][2]`)는 avg 키 없는 행(시나리오/단일 백테스트)도 됨. 그 행이 워크포워드보다 10분 초과 새 것이면 배치 전체 탈락 → `per={}` → exit 3(`:126-130`). → 아래 발견 4로 연결.
- **확신도: 높음** (혼합 시나리오는 승격일 실행 패턴과 정면 충돌)

### 4) avg_win/avg_loss % 단위 vs `kelly_fraction` — **b 비율은 무관, win_rate 단위가 불일치 (확인 차단된 부분 있음)**
- `b = avg_win/avg_loss`는 비율이라 **% 단위는 상쇄**됨 — avg 손익을 %로 계산하는 것 자체는 문제 없음.
- 문제는 **win_rate 단위**:
  - 출력: `build_screener_stats.py:143` — `round(ps * 100.0, 2)` → **퍼센트(예: 53.3)**
  - 계약 예시: `docs/TRADER_INTEGRATION.md:48` — `"win_rate": 0.61` → **분수**
  - repo 유일 kelly 엔진: `services/strategy-agents/app/risk_management/position_sizer.py:55-62` — `win_rate >= 1.0 → return 0.0` (분수 전제, 테스트 `test_position_sizer_ext.py:24-26`이 고정). 53.3이 들어오면 **kelly=0 → 모든 진입이 조용히 차단** (docstring 25-27행이 막으려던 침묵 실패의 정반대). 정규화 가드가 없는 다른 엔진이면 kelly≈53.8 → 클램프 0.25로 최대 사이징.
  - docstring `:21`의 "엔진이 >1이면 /100으로 정규화" 주장은 repo 어디에도 근거가 없음(해당 문장이 유일한 출처). trader-agent 실제 `risk.py` 확인이 필요 — 권한 차단으로 미확인. **확신도: 불일치 자체는 높음 / 폭발 여부는 중간(실코드 확인 필요)**

---

## 발견 목록 (심각도순)

### 발견 1 — 축소된 b'가 페이로드에서 b·b'로 증폭되어 전달됨 — **높음**
`build_screener_stats.py:144-145`
```python
"avg_win_pct": round((a["aw_sum"] / n) * bs, 3),   # ← avg_win에만 bs 곱함
"avg_loss_pct": round(a["al_sum"] / n, 3),          # ← avg_loss는 원본
```
소비자가 `b = avg_win_pct / avg_loss_pct`로 재계산하면(`position_sizer.py:60`이 정확히 이 방식) 전달되는 비율은 `b_측정 × bs`가 됨. 축소 공식(주석 24행: `b' = 1+(b-1)·0.5`)은 b를 1로 당기려는 것인데, `b·bs`는 **1에서 멀어지는 방향으로 증폭**: b=1.2 → 1.32 (의도 1.1), b=0.9 → 0.855 (의도 0.95). 클램프 상한 2.0도 우회됨(b=1.8 → 2.52). 올바른 스케일은 `bs/b`(= `al·bs`).
**폭발 시나리오**: 스크립트의 f\* 게이트(`:66-68`)는 `bs` 기준으로 통과시키는데 소비자는 `b·bs` 기준으로 계산 → **스크립트는 통과, 소비자에서 f\*≤0 → 전량 차단**. 예: p=0.55, b=0.85 → 스크립트 f=+0.064(통과), 소비자 f=−0.022(차단). 노트(`:147-148`)는 "b'=bs"라고 주장하므로 진단도 어긋남.

### 발견 2 — '기록 없음'이 fail-closed가 아니라 fail-STALE — **높음**
`build_screener_stats.py:126-130` (exit 3 시 기존 파일 **삭제하지 않음**) + `feed_export.py:170-181` (`load_stats`는 파일 `generated_at` 신선도 검사 없이 존재하면 그대로 발행).
**폭발 시나리오**: 시나리오/단일 백테스트 행(avg 키 없음, `pnl_backtest.py:304-312, 339-346`)이 워크포워드보다 10분 이상 늦게 기록됨 → 배치 전체 탈락 → exit 3 → 파일은 안 쓰는데 **이전 챔피언의 낡은 통계 파일이 남아 무기한 재발행**됨. 승격 직후 새 챔피언이 옛 모델의 kelly 사전확률로 사이징됨 — 스크립트의 존재 이유(6-9행)가 정확히 무너지는 경로.

### 발견 3 — 10분 배치 필터가 배치 분리를 보장하지 못함 (혼합) — **높음**
`build_screener_stats.py:88-102` — 앵커는 `newest` 하나, 포함 판정은 600초 하나뿐. `meta`의 모델·파라미터 식별자(`mode/k/hold/threshold/window`, `pnl_backtest.py:270-276`에 기록돼 있음)를 무시하고 전부 `"close"` 하나로 합산.
**폭발 시나리오**: 승격일 챔피언/챌린저 워크포워드를 10분 안에 연달아 실행 → 두 모델 통계 혼합 (주석 `:75-76`의 전제가 깨지는 정확한 패턴). 추가로 계약(TRADER_INTEGRATION.md:52 "전략별 키를 섞지 않는다") 위반 — 향후 swing 백테스트 행이 생기면 close 통계에 오염됨. `pnl_backtest`는 스크리너별 유니버스도 없이 고정 유니버스(seed 42, `pnl_backtest.py:217`)라 "스크리너별 통계"라는 명목과도 어긋남.

### 발견 4 — win_rate 퍼센트 출력 vs 소비자 분수 전제 (발견 4의 4번 확인사항) — **높음(불일치)/중간(폭발)**
`build_screener_stats.py:143` — 계약 예시(`TRADER_INTEGRATION.md:48`: 0.61)와 repo 엔진(`position_sizer.py:58`: ≥1.0이면 kelly=0)이 모두 분수인데 출력은 퍼센트. docstring의 정규화 주장은 자기 언급 외 근거 없음. trader-agent 실코드(`/mnt/c/.../risk.py`) 확인이 권한 차단으로 미완 — **이 파일 하나만 확인하면 높음으로 확정/기각 가능**.

### 발견 5 — 전승/전패 창을 통째로 버리는 필터가 측정 p를 편향 — **중간**
`build_screener_stats.py:100` — `not aw or not al`: `avg_win_pct==0`(전패 창) 또는 `avg_loss_pct==0`(전승 창)인 창은 n-가중 평균에서 제외됨. 주석(`:14`)은 "옛 기록"만 버리겠다고 했지만 이 조건은 **최신 기록도** 버림. 워크포워드 창의 n은 작고(90일/3창, k=5, hold=5 → 창당 거래 수개), 창 하나만 전승/전패여도 그 증거 전체가 소실됨. b를 알 수 없다는 이유(주석 `:14`)는 al=0·aw=0에도 성립하지 않는 과잉 필터.

### 발견 6 — shrink()의 b≤0 부활 + p=0 마스킹 — **중간(함수)/낮음(현재 도달성)**
`build_screener_stats.py:63-68` — 확인사항 2의 내용. b≤0이 클램프로 0.8에 부활해 `ps>0.4444`에서 f\*>0 판정이 성립하지 않음; p=0도 0.45로 마스킹됨. 현재 main() 경로에서는 도달 불가하나, 향후 행 필터(`:100`)가 바뀌면 곧바로 노출되는 지뢰.

### 발견 7 — 페이로드 비원자적 쓰기 + 이중 경로 — **낮음**
`build_screener_stats.py:166-169` — 두 경로에 순차 open/write(원자 교체 아님). `feed_export.py:170-181`이 쓰기 도중 읽으면 JSON 파싱 실패 → 경고 후 stats 생략 → 기본값 회귀(발견 2와 동일 증상의 저확률 경로).

---

## 요약
- **키 일치(1번)** 는 깨끗함. **shrink 경계(2번)** 는 결함이 있으나 호출부가 가림.
- 실제 위험은 **데이터 경로의 두 계약 불일치**: (a) b' 축소가 페이로드에서 `b·bs`로 왜곡(발견 1), (b) win_rate 퍼센트 vs 분수(발견 4), (c) 10분 필터의 혼합/탈락(발견 3) → (d) 탈락 시 낡은 파일 무기한 발행(발견 2).
- 즉 CI(단위 테스트 없음, 스타일)는 통과하지만 **승격일 연속 실행 + 시나리오 실행 뒤 크론**이라는 실제 운영 패턴에서, "켈리 사전확률이 측정값이 아니라 옛 모델 통계 또는 전량 차단"으로 조용히 떨어짐. 발견 1·2·3이 한 시나리오로 연결되는 점이 가장 위험합니다.
- 미확인 1건: trader-agent 실코드의 win_rate 정규화 유무(`/mnt/c/.../trader_core/risk.py` 읽기 권한 필요) — 발견 4의 확정/기각용.

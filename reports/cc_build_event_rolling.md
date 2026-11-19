모든 검증이 끝났습니다. 최종 보고입니다.

---

# 구현 보고 — docs/spec_event_rolling.md 계약 실행

## 1. 변경 파일 (스펙이 지정한 3개만)

| 파일 | 상태 | 내용 |
|---|---|---|
| `scripts/build_event_rolling.py` | **신규** | event_features → `event_rolling_features`(8컬럼) 빌더 |
| `scripts/patch_panel_events.py` | **교체** (아래 §3) | 패널에 8컬럼 as-of append |
| `tests/test_event_rolling.py` | **신규** | 스펙 §3 계약 5개 |

그 외 산출물: `data/panels/panel_prod200_v5.npz`(신규), `data/reports/event_rolling.json`, `data/reports/feature_survival_v5.{json,md}`. **git 명령은 실행하지 않았습니다.** 리포 밖 파일 생성 없음.

## 2. 실측 근거 (docstring에 조회 명령까지 기록 — 규칙 ②)

- `event_features`: **223,204행 · 2025-06-17~2026-11-18 · 2,710종목 · 고유거래일 324** — 스펙 원천과 일치. **all-zero 행 0건**(비영 행만 저장), 종목당 행수 중앙 76 → "직전 20행"은 이벤트-시간 창.
- 패널 v4: **X (61640, 213) float32**, feature_names `<U33`, 314일·200종목 — 스펙 "213→221" 일치. 패널 종목 중 **192/200 커버** — 스펙 일치.
- 판정을 뒤집을 수 있는 해석 2건을 docstring에 명시: ① "총 이벤트" = **16개 event_*_5d 합**(disclosure_count_5d 제외 — 포함 시 any_60d가 비영행-저장 원천에서 상수화), ② as-of는 **trade_date < D 엄격**(당일 행 제외).

## 3. 기존 파일 수정 내역 (규칙 ⑤)

- **`scripts/patch_panel_events.py` 교체**: 기존(2026-09-25)은 옛 `event_*_5d` 18개를 **같은날 값**으로 붙이고 결측을 **0으로 채우던** 일회성 실험 도구(컨테이너 전용 `--in/--out`). 새 스펙 §2가 같은 경로를 명시하고, 옛 도구는 스펙 §5가 금지하는 두 가지(당일 조회·0 채움)를 그대로 하고 있어 대체. 참조처는 일회성 `_cg29_*.py` 등뿐이며 옛 코드는 git 히스토리에 남고, 새 docstring에 대체 사실을 명시.
- 그 외 기존 파일·테이블·패널(v4 mtime `Nov 19 17:56` 그대로) **무수정**.

## 4. 검증 실행 결과 (규칙 ⑦)

**테스트** — `/usr/bin/python3 -m pytest tests/test_event_rolling.py -q` → **5 passed** (① 직전20행·당일제외 ② 추세=5/20(+1e-9 분모만) ③ any_60d 이진화 ④ 창부족→null·0행 미방출 ⑤ 멱등 2회→행수 불변).

**dry-run → 본 적재 ×2 (멱등 실증)**
```
소스 223204행 → 출력 780091행 · 2710종목 · 2025-08-01~2026-11-18
1회차: 0 -> 780091 (persisted=780091)    2회차: 780091 -> 780091 (persisted=0)
```
테이블 실측: `event_rolling_features` **780,091행 / 2025-08-01~2026-11-18 / 2,710종목 / PK(stock_code, trade_date)**. 범위 로직 스모크(`--from 2026-11-01 --to 2026-11-10`) → 8,121행 정상.

**자기신고 3분리** — `dq_claim.record_claim` 재사용: `source_rows=223204 / claimed_rows=780091 / persisted_rows=780091→0` (dq_runner_claim에 2건 기록 확인. "기존 테이블 쓰기 금지"는 피처 데이터 테이블 대상으로 해석 — 신고용 claim 테이블은 지시 ④가 명시 재사용 요청).

**패널 패치** — v4→v5, 매칭 59,109/61,640(95.9%), 저장 후 재독해 검증 통과: **행수 동일 · 기존 213컬럼 바이트 동일 · dates/codes/price 동일** (독립 비교로도 재확인: `X4.tobytes() == X5[:,:213].tobytes()` True). 가드: `--out`==`--panel` → **exit 2**, 중복 컬럼 → **exit 3** 확인. 원본 v4는 압축 관례에 맞춰 v5도 `savez_compressed`(25.4MB).

**생존 판정(스펙 §4)** — v5 전체: alive 127 / dead 67 / conditional 27. **신규 8개 중 7개 ALIVE**, risk_level_20d만 DEAD_SPARSE(4.56%, 임계 직하) → 스펙 기대 "5개 이상 nnz ≥ 5%" **충족**:
```
event_level 71.7% · trend 53.9% · any_60d 95.6% · funding 30.3% · governance 60.0%
business 47.1% · disclosure 72.4% · risk 4.6%
```

## 5. 과정 중 잡은 자체 버그 1건

패치 스크립트의 출력 통계에서 NaN이 `!=0` 비교에 True로 잡혀 nnz가 부풀려 표시되는 **표시 버그**(패널 값 자체는 정상 — 생존 리포트 수치와 대조로 발견). 마스크 집계로 수정 후 패치 재실행 → 두 보고 수치 완전 일치. 수정 사유를 코드 주석에 기록.

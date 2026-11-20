모든 검증이 완료됐습니다. 최종 보고입니다.

---

# 구현 보고 — 시장 레짐 필터 (docs/spec_regime_filter.md)

## 변경 파일 (3개 — 스펙 계약 그대로, 그 외 수정 없음)

| 파일 | 변경 |
|---|---|
| `scripts/market_regime.py` | **신규** — index_daily 읽기(읽기 전용) → `{asof, index, close, ma20, ret5, state, reason}` JSON을 `data/state/market_regime.json`에 원자적 저장. 멱등(tmp+os.replace), 자기신고 3분리 |
| `scripts/daytrading_screener.py` | 게이트 옵션 `--regime-file`(기본 `data/state/market_regime.json`)·`--regime-block on|off`(기본 on) + 차단 시 빈 결과+사유 기록 + fail-open 경고 1줄. 상세는 아래 |
| `tests/test_market_regime.py` | **신규** — 스펙 §3 계약 4개 |

## 실측 근거 (추측 없음 — 조회 명령을 소스 docstring에 기록)

- **DB 조회 2026-11-20**: `SELECT ... FROM index_daily GROUP BY index_code` → KOSPI/KOSDAQ 각 459행(2025-01-02~2026-11-20, 백필 완료). `WHERE index_code='KOSDAQ' AND trade_date <= '2026-11-20' ORDER BY trade_date DESC LIMIT 21` → close=645.68 → **ma20=695.659, ret5=−7.715%** → `close<ma20` AND `ret5≤−3%` → **risk_off** (스펙 §4 기대와 일치).
- WHY는 `docs/EXP_rule_expectancy_verdict.md` 인용: KOSDAQ −31.7%, 기준선 −0.691%/일, 추격 규칙 R3/R4/R5 전부 음 → 레짐 불량 시 롱 진입 자체가 손실 주원인.

## 스크리너 최소 수정 내역 (무엇을 왜)

1. docstring에 게이트 WHY/동작 단락 추가.
2. helper 4개 신규: `_load_regime`(없음·파손→경고+None), `_prev_trading_day`(krx_holidays.json 주말·휴장 제외), `_regime_gate`(risk_off+asof=오늘/직전 거래일→차단), `_default_csv_path`(기존 인라인 4줄 추출 — 동작 동일).
3. 차단 시: **후보를 만들지 않고** 빈 CSV(헤더만) + (--json-out 시) 빈 items JSON에 top-level `reason` 추가 + 콘솔/자기신고에 `regime_off: KOSDAQ close<ma20,ret5<=-3%` 기록. 조용한 0건과 구분됨(스펙 §2 ※).
4. 기존 쓰기 경로(CSV/JSON/자기신고)는 `not args.dry_run` 가드로만 감쌈 — `--dry-run` 없이 실행하면 기존 동작 100% 동일.
5. **판단 사항(보고)**: 스펙 §4 검증 명령에 `--dry-run`이 있는데 기존 스크리너에 없어 **최소 구현을 추가**함(콘솔만 출력, 산출물·자기신고 미기록). 스펙 §2에 없는 유일한 추가 변경.

## 자기신고 (3분리 — dq_claim.record_claim 재사용, 규칙 ④)

- `runner="market_regime"`, `table_name="market_regime_state"`: **source**=DB가 준 행수(26) / **claimed**=파서 생성 레코드(1) / **persisted**=JSON 실제 저장(1). DB 실측 확인(위 출력). **유일한 DB 쓰기는 이 자기신고 행** — index_daily 등 데이터 테이블은 읽기 전용.

## 검증 출력 (실제 실행)

```
$ /usr/bin/python3 -m pytest tests/test_market_regime.py -q
....                                                                     [100%]
4 passed in 0.05s

$ /usr/bin/python3 scripts/market_regime.py --asof 2026-11-20
[regime] KOSDAQ asof=2026-11-20 close=645.68 ma20=695.659 ret5=-7.715 -> risk_off (close<ma20,ret5<=-3%)
[claim] market_regime -> market_regime_state source=26 claimed=1 persisted=1
→ data/state/market_regime.json: {"asof":"2026-11-20","index":"KOSDAQ","close":645.68,"ma20":695.659,"ret5":-7.715,"state":"risk_off","reason":"close<ma20,ret5<=-3%"}
→ 재실행 시 md5 동일(멱등 확인: 63ba80a5... = 63ba80a5...)

$ /usr/bin/python3 scripts/daytrading_screener.py --top-n 20 --dry-run
WARNING 레짐 게이트 차단: regime_off: KOSDAQ close<ma20,ret5<=-3%
후보 없음 — regime_off: KOSDAQ close<ma20,ret5<=-3% (dry-run: 산출물·자기신고 미기록)   ← 후보 0건 + 사유 ✓

fail-open 확인:
  --regime-block off            → 후보 5건 정상 출력
  --regime-file 없음            → WARNING 1줄 + 후보 5건
  asof 오래됨(11-18)/unknown    → WARNING 1줄 + (False, "") 개방
  _prev_trading_day(11-20)      → 2026-11-19 ✓

$ /usr/bin/python3 -m pytest tests/ -q
959 passed, 4 skipped, 8 deselected, 1 failed  (72.77s)
```

## 기존 테스트 1건 실패 — 내 변경과 무관함을 확인함 (미수정, 규칙 ⑥·"기대값 수정 금지")

`test_trader_cycle_sample_definition.py::test_unfilled_marker_excluded_from_closed_sample`: 픽스처 저널 날짜가 2026-11-19인데 `trader_cycle.py:202`의 `today_closed`는 **실행일** 기준 집계 — 테스트 작성일(11-19)에만 통과하는 날짜 의존 테스트. 해당 테스트는 `scripts/trader_cycle.py`만 import하며 제 변경 3개 파일을 전혀 참조하지 않음. 기대값 수정 금지 규칙에 따라 손대지 않고 보고만 함.

## 준수 확인

- 크론 등록·라이브 적용·git 명령 **미실행**. 수집기·실주문·트레이더 코드 무접근.
- 지수 결측 0 채움·추정 없음 — 행 부족/없음은 `unknown`+사유로 처리(테스트 ④ 포함).
- 리포 밖 파일 생성 없음. 검증 산출물은 스펙이 지정한 `data/state/market_regime.json`뿐.

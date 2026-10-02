# 핸드오프 소비 계획 (큐 심사 H) — 2026-10-02

역할: quant-protocol-lock §5.2 게이트 ⑥. 근거: `scripts/trader_cycle.py --handoffs` 실측
(총 20건 / 미채택 17건 — 그중 4건은 이미 판정이 끝났는데 `command` 가 없어 카운터가 과대집계된다).
이 문서는 **남은 12건에 소유 역할과 '무엇을 채우면 착수 가능한가'를 배정**한다.

## 0. 이번 큐 심사에서 종결한 항목

| id | 제목 | 처리 | 근거 |
|---|---|---|---|
| MT116 | 챔피언 교체 후 swing 신호 소멸 | **done(수리 완료)** | 롤백(0.554776→0.551318) + 승격 게이트 `--min-improvement 0.02` + `tools/release_precheck.py --with-probe`(후보 신호 0건이면 BLOCK). 같은 유니버스 A/B: 0건 vs 9건 |
| MT117 | 챔피언 교체/롤백 미기록 | **done(수리 완료)** | `scripts/log_champion_swap.py` + `full_pipeline_dd.sh` 배선(멱등 append). 실측 1줄 기록 확인 |
| XR16 / XR7 / R22 / R24 | (판정 종료) | 카운터 과대 — 별도 조치 없음 | `status=done` 인데 command 가 없어 미채택으로 세어짐 → 감사 스크립트에서 큐 위생으로만 보고 |

## 1. 남은 12건 — 소유·소비 조건

표기: **소비 조건** = 이 필드를 채우면 구동기가 실행할 수 있게 되는가.

| id | prio | 제목 | 소유 | 소비 조건(다음 행동) |
|---|---|---|---|---|
| XR13 | 8 | 죽은 피처 35개 분해(원천부재/커버리지/시장레벨/부활가능) | 리서처 | 산출물 `data/reports/r13_dead_feature_audit.json` 를 `command`+`check` 로 연결(이미 산출물 존재) |
| XR2 | 8 | `institution_ownership_pct` 소스 발굴(100% 결측) | 리서처 | 소스 후보 3곳 도달 프로브 1콜 + 커버리지 실측치를 `success`(≥X%)로 등록 |
| XR3 | 8 | 수급 이력 확장 343 → 800종목 | 리서처 | 백필 창·콜 수·예상 소요(est_minutes)와 커버리지 check 채우기 |
| XR10 | 8 | 수급·시장·모멘텀 피처 19개 부활 | 리서처 | **비영 커버리지 먼저 실측**(교훈: '19개 부활'은 값 부활이 아니었다) → 컬럼별 비영 비율을 check 로 |
| XR12 | 8 | 재무 비율 피처 19개 부활(기간유형 구분 선행) | 리서처 | 기간유형 매핑표 + 비영 비율 check |
| XR24 | 8 | `data_gap` '당일 휴장' 오탐 — 거래일이 휴장으로 기록 | 리서처 | 수리 커밋 + `krx_holidays.json` **연도별 항목 수** 확인 check |
| XR25 | 8 | DART 공시 정기 러너 — 신선도 상시 감시 | 리서처 | 크론 등록은 완료(10-01) → 남은 것: 신선도 check(max(rcept_dt) 경과일) 를 `command` 로 |
| XR9 | 4 | 이벤트 공시 피처 17종 연결 | 엔지니어 | 선별(top30) 진입 여부를 check 로(교훈: 진입 0개면 '효과 없음'이 아니다) |
| XR1 | 5 | 실제 공시 접수일(rcept_dt)로 재무 as-of 정확화 | 엔지니어 | as-of getter 수리(CG63 과 같은 경로)와 같은 문법으로 패널 재측정 command |
| MT49 | 8 | 검증 성적표에 폴드 통계·purge·승격 dry-run 포함 | 엔지니어 | `tools/release_precheck.py` 를 성적표에 연결(중복 구현 금지) |
| MT70 | 4 | swing 확률 피드가 소비자 R1 문턱 아래 — 경로 차단 | 엔지니어 | `scripts/_swing_ensemble_weight_probe.py`(PROBE_MODEL_DIRS) 로 재현 → 문턱 판정을 check 로 |
| T17 | 8 | 스크리너별 실현 성과(어느 데이터가 기여했나) | 리서처/트레이더 | `data/reports/trader_fill_stats.json` 의 by_screener 집계를 표본 확대 check 로 |

## 2. 소비 규율 (다음 큐 심사에서 다시 세지 않기 위해)

1. **`command` 없는 항목은 '미착수'가 아니라 '판정 불가'다** — 구동기는 실행할 수 없고 카운터만 올린다.
2. 종결된 항목은 `status=done` + `result` 를 채워 미채택 카운터에서 빠지게 한다(MT116/MT117 이 그 예).
3. 새 환류 항목은 **재현 명령을 함께** 넣는다(트레이더 `handoff_to_model(research)` 의 note 규칙).

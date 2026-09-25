# 모델엔지니어 야간 사이클 — 2026-09-25 (22:00 틱)

## 결론 3줄
1. **정렬 버그 발견·수정**: 패널 피처명 중복(14개) 때문에 `df[names]` 선택이 210→238열로 부풀며 순서가
   바뀌어, 선별 인덱스가 기록된 이름과 다른 열을 학습에 넣고 있었다 → 이름 1:1 화(dedupe) + 하드 가드로 수정.
2. **어제 올린 누수 위반 주장 철회**: 정렬 수정 후 5폴드 전부에서 top30 안의 시장레벨 피처 = 0개(게이트 통과).
   어제의 "시장레벨 피처가 횡단면 선별에 들어간다"는 정렬 버그가 만든 오판이었다.
3. **남은 실질 리스크는 종목상수 피처**: top30 의 37~53%가 종목상수(모집단 0.3293 대비 1.2~1.6배 과대표집)
   → 재기준선 + 피처풀 비교(RB1)를 다음 틱에 실행하도록 백로그 priority 0 으로 등록.

## 실측 근거
- 패널: `panel_420_asofpatch.npz` 13,609행 × 210 피처, 49종목, 2025-07-31~2026-09-23.
- 버그 증명: `df[base_names].shape=(13609,238)` vs `X.shape=(13609,210)`, 210개 위치 중 192개 어긋남.
  예) pos16 label=cycle_down 인데 실제 값은 X열15(cross_trend). 중복 라벨: cross_trend·price_volume·
  target_ma_5/10/20·volume_price_trend·volatility_volume 등 14개.
- 수정 검증(스모크 folds=2 seeds=1): 이름 `price_volume__dup1` 생성, 가드 통과, 제외군이
  cpi_yoy·credit_spread·fx_usd_krw·fx_change_1m/3m·oil_change_1m/3m·oil_wti·ppi_yoy·yield_spread·
  krx_advance_decline_ratio·krx_total_trading_value·interest_rate_change_3m·event_* 로 교체됨
  (수정 전에는 op_margin·price_volume·rank_volatility_20d 같은 비시장레벨 컬럼이 제외되고 정작
   program_trading_ratio 는 남는 완전한 오작동).
- 게이트 실측(정렬 수정 후, 5폴드): ① 단일 피처 최대 분리도 0.5637 (<0.75, 통과)
  ② as-of 위반 0 / 상장 전 패딩 0 (통과) ③ 시장레벨 in top30 = 0/30 × 5폴드 (통과)
  ④ 종목상수 in top30 = 12·12·16·12·11 / 30 (40~53%) — 주의.
- 정직 지표: pooled AUC 0.5395 vs 날짜별 AUC 평균 0.5426 (Δ+0.003, 92일) → pooled 판정 편향 미관측.
- MK 축(시장레벨 제외) 무효: 0.5395 vs 0.5395, 선별 목록 동일.

## 산출물
- `services/xgboost-ml/reports/overnight/engineer_leak_audit_20260925.json` (컨테이너 경로 `/app/reports/overnight/`)
- `data/reports/overnight_leak_select_20260925_fixed.json` (정렬 수정 후 게이트 실측)
- 코드: `scripts/wf_wave.py`(dedupe_names + 불일치 즉시 실패), `scripts/wf_label_sweep.py`(하드 가드,
  시장레벨 마스크 위치기반화, daily_auc_mean, --out/--summary-out), `scripts/wf_leak_select_check.py`(신규 감사 도구)

## 진행 중 / 다음
- U1(49→150종목): 21:29 시작, 22:21 기준 4,000/41,893(9.5%) @1.28 pair/s, ETA 09-26 06:35.
  정렬 수정 이전 코드 → 유니버스 효과 비교는 유효, 피처 귀속은 무효.
- RB1(priority 0, pending): 정렬 수정 후 재기준선 + PO_timevary/PO_const/MK 4실험 (약 25분, U1 종료 후).
- 승격: 새 챌린저 없음(챔피언 0.5513 유지). 저녁 파이프라인 `retrain_champion` 진행 중 → `champion_cand`
  생성되면 다음 틱에서 `champion_promote --dry-run` 판정.

## 위임(Claude Code) 상태
- `./scripts/ask_claude.sh review reports/claude_review_wf_label_sweep_20260925.md` 실행 →
  1차 백엔드 실패: `[claude-code:unrecognized_model] {"model":"deepseek-v4-pro[1m]"}`.
  산출물 미생성 → **미채택**(본업은 계속). 백엔드 모델명 설정 복구가 필요(보고 ② 항목).

## 위임 리뷰 채택/기각 (파일:라인 재검증 후)
- 산출물: `reports/claude_review_wf_label_sweep_20260925.md` (backend=claude, 12,091B, 1차 unrecognized_model 후 재시도 성공)
- **채택 ①** F1 누수(중간-높음): purge 는 달력 h거래일, 라벨은 종목별 h번째 **행** → 갭 종목 학습 라벨이
  테스트 구간 가격 참조. **내 실측으로 확정**: 폴드4 8행 · 폴드5 14행(학습행의 0.09~0.13%), 예 008290
  (2026-04-13 → 참조 2026-05-12, 테스트 시작 2026-05-06), 002680(2026-06-15 → 참조 2026-07-13).
  → `wf_label_sweep.py`·`wf_wave.py` 에 라벨 참조일 기준 purge 추가. 수정 후 스모크(folds=5,seeds=1):
  fold4 `n_label_ref_purged=8`, fold5 `=14` — 호스트 예측과 일치, AUC 0.5385(pooled)/0.5403(daily).
- **채택 ②** F7 운영: `--summary-out` 디렉터리 미생성 → 전 실험 완료 후 크래시. 시작 시점에 디렉터리 생성
  (+`--out x.jsonl` 처럼 dirname 이 빈 경우 `makedirs("")` 크래시도 차단).
- **기각/보류**: F3(캐시 재사용 code_sig 검증 부재)·F4(code_sig 커버리지)·F6(`feature_names.index` 중복명 붕괴)·
  F8·F9·F10·F11 → 파일:라인은 확인했으나 오늘 밤 U1 과 자원이 겹치고 프로토콜 변경은 RB1 재기준선과 함께
  다뤄야 하므로 **백로그 RB2(backlog)** 로 이관. 리뷰가 '추측/미검증'으로 표시한 항목(갭 종목 빈도,
  중복 쌍 동일값 여부)은 채택하지 않았다.

모든 검증 통과. 최종 보고입니다.

## 구현 보고 — `docs/spec_horizon_expectancy.md` 계약 구현

### 변경 파일 (신규 2개, 기존 수정 0개)
| 파일 | 내용 |
|---|---|
| `scripts/horizon_expectancy.py` | 지평별 기대값 하네스 (신규) |
| `tests/test_horizon_expectancy.py` | 스펙 §2 의 4개 테스트 (신규) |

기존 파일 수정 없음. git 명령 실행 안 함(규칙 ⑥). 실제 컨테이너 실행 안 함(오케스트레이터 몫).

### 실측 근거 (규칙 ② — 조회 명령을 주석에 그대로 남김)
- **프로브 폴드 AUC**: `services/xgboost-ml/reports/overnight/wf_horizon_probe.json` 조회 → H1_top60 fold_mean 0.5568, folds [0.5545, 0.5518, 0.5529, 0.5678]
- **패널 규모**: `docker exec stock_xgboost_ml python3 -c "np.load('/app/app/models/wf/p_v5recent.npz')..."` → X (37805, 221) · 200종목 · 2026-02-02~2026-11-17 · price 137.0~1,547,000
- **학습 경로 실측**: `overnight_ml_loop.train_seed`(L312-344) = oversample_balance(seed rng) + split_train_val(0.67) + `EnsembleModel`(xgb+lightgbm+catboost, cat 미설치 시 자동 스킵) + `apply_hyperparams`(lr 0.03/d 4/est 1500/seed)

### wf_wave 재사용 (규칙이 달라지면 비교 무효 → 전부 재사용)
- **라벨**: `wf_wave.make_labels(df, "quantile", h, 0.3)` import 재사용
- **피처 선택**: `wf_wave.subset(names, "top60", Xtr, ytr)` import 재사용
- **학습 하이퍼파라미터**: `wf_wave.BASE["recipe"]` + `ml.train_seed` 호출을 wf_wave L683-690 과 동일하게 재사용
- **폴드 분할**: wf_wave 의 분할은 `main()` 인라인(L648-671)이라 import 불가 + 스펙 §4 가 기존 파일 수정을 금지 → `fold_split()` 으로 **동일 규칙 재구현**(purge 경계 h일, `_ref` 라벨 참조일 제거 포함, 주석에 1:1 대응 명시). 스펙의 "xgb 1+lgb 1" 기술과 달리 실제 `train_seed` 는 `EnsembleModel`(3모델, val-AUC 가중) — wf_wave 와의 비교 타당성을 위해 실제 경로를 재사용했고 이 차이를 docstring 에 명시함

### 스펙 계약 구현
- 확장창 4폴드, 진입=신호일 **다음 거래일 종가**(시가 근사 — 산출 JSON `entry_note` 로 명시), 청산=진입+h거래일 종가, 비용=(cost_bps+2×slip_bps) bp 차감
- 산출 JSON: horizon 별 `{n, mean_net_pct, win_rate, std, t, max_dd_pct, by_fold:[...], picks_sample}` + 사전 고정 판정(mean>+0.10% & n≥300 & 승률≥손익분기 승률) `verdict`
- **멱등**: 산출은 JSON 1파일뿐 — tmp 쓰기→`os.replace` 원자적 덮어쓰기(재실행 안전). mkstemp(0600) 대신 open("w") 사용 — 컨테이너 루트가 쓴 파일을 호스트가 읽지 못하던 실측 교훈 반영
- **자기신고**: 스펙 §4 DB 쓰기 금지로 `dq_claim.record_claim`(DB) 재사용 불가 → 산출 JSON `self_report` 블록이 3분리 대신(source_rows(패널 수신) / label·picks(파서 생성) / persisted_rows(JSON 저장)) — 미사용 사유를 note 에 명시
- **look-ahead 금지**: 진입은 신호일 다음 행(k+1), 피처 선택은 학습구간 전용, 라벨 참조일 purge — 전부 준수
- **테스트 가능 구조**: 무거운 import(wf_wave 등)는 `run()` 내부로 지연 — 호스트 테스트는 `evaluate()` 에 가짜 모델을 주입해 전체 파이프라인 검증 (호스트 python3 에 sklearn/xgboost 없음 실측 확인)

### 검증 출력 (규칙 ⑦ — 실제 실행)
```
$ /usr/bin/python3 -m py_compile scripts/horizon_expectancy.py
syntax OK
$ /usr/bin/python3 -m pytest tests/test_horizon_expectancy.py -q
....                                                                     [100%]
4 passed in 1.15s
```
테스트 4개 = 스펙 §2 그대로: ① 비용 21bp/슬리피지 40bp 정확 차감(mean −0.21%p, 승률·std 불변) ② 상위 N 확률 순서 + 동률 코드 오름차순 ③ purge 가 경계일·라벨 참조일 누출 차단(갭 종목 행 제거 확인) ④ 같은 시드 2회 → 산출 JSON 비트 동일 + 시드가 모델까지 전달됨.

추가 스모크(호스트, 가짜 모델): 파이프라인 데모에서 `label_rows h1=348`, fold1 `skipped: small_sample`(합성 96행 < wf_wave 하한 100 — 규칙 정상 작동), h3 `skipped_by_reason: {no_exit_row: 2}`, 판정 verdict 정상 생성 확인.

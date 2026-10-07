# 조사 보고: 승격 게이트 돈 기준(require-expectancy)이 2026-10-07 후보를 통과시킨 경로

**결론 요약**: CG133/CG134 실측에서 돈 기준 게이트는 **코드대로 정상 작동**했다 — 5개 조건 전부 통과가 맞고, 실제 승격을 막은 것은 라이브 스코어 게이트였다. 다만 돈 기준의 **조건 설계에 유의성(t) 검사가 없어** 순기대 +0.157%p(t=0.73, 95% CI [−0.271, +0.585] = 0 포함)라는 "통계적으로 0과 구별되지 않는" 증거로 게이트가 열렸다. `min_robust_auc 0.5`(objective.json)도 집행 코드가 없다. 즉 **게이트는 결함 없이 작동했고, 방어는 라이브 스코어 게이트가 대신했다.**

---

## (1) robust_oos.json 의 expectancy_pct·robust_auc 를 쓰는 스크립트·호출 경로

**작성자**: `scripts/model_metric_protocol_audit.py:522-548` — part-b(다중폴드 robust_auc)와 part-c(순기대, `fillable_expectancy` 재사용)를 합쳐 `<후보>/robust_oos.json` 으로 쓴다 (`--robust-oos-out` 인자).

**호출 경로(파이프라인)**: `scripts/full_pipeline_dd.sh:392-397` — 매 실행마다 후보 측정. 챔피언 기준선은 **월요일만** 갱신(`:400-407`, `[ "$(date +%u)" = "1" ]` — 2026-10-05 월요일 실행 실측: `champion/robust_oos.json` mtime 10-05 21:37 KST).

**읽는 쪽(게이트)**: `services/xgboost-ml/app/training/champion_promote.py:103-129` `_read_robust_oos()` — 후보는 `:291`, 챔피언은 `:311`에서 읽혀 `_expectancy_verdict()`(`:309-314`)로 전달. `--require-expectancy` 플래그는 `config/objective.json:58`(`promote_require_expectancy: true`) → `scripts/objective.py:107-112` `promote_flags()` → `full_pipeline_dd.sh:411-418` 으로 전달된다. CG133/CG134는 역할 틱이 동일 플래그로 직접 실행(`data/reports/me_cycle/logs/me_cycle_CG133_20261007-220012.log`, command 라인에 `--require-expectancy` 확인).

**같은 행·같은 창인가 — 사실(높음)**: 같다.
- part-c(순기대)의 원천 `data/reports/close_gate_probe/trades.csv` 는 **2026-10-01 21:18 이후 갱신 없음**, 87세션·2026-05-26~2026-09-30 고정. 챔피언(10-05 측정)과 후보(10-07 측정)의 `n_sessions=87`·`n_trades=1340`·`split_date=2026-07-28`·`protocol` 문자열이 **완전히 동일** (`champion/robust_oos.json` vs `champion_cand/robust_oos.json`).
- part-b(robust_auc)도 10-07 파이프라인 로그 `[b] 유니버스 80종목 · 거래일 200개 (2025-12-04~2026-09-30)` — 패널 창이 10-05→10-07 사이에 진전하지 않음 (`reports/full_pipeline_dd_20261007_2000.log`).

**추가 발견(높음)**: "같은 프로토콜" 가정은 **코드로 강제되지 않는다** — `_expectancy_verdict`(`champion_promote.py:132-170`)는 두 레코드의 `protocol` 문자열을 비교하지 않는다(챔피언 `protocol`은 `:121`에서 읽히기만 함). 관례(파이프라인이 같은 인자로 측정)에만 의존. 또 `objective.json` acceptance 의 `min_improvement_pct_over_incumbent: 0.1` 은 `objective.py:107-112`가 **넘기지 않고**, `champion_promote.py:480` 의 argparse 기본값 0.1 이 우연히 일치할 뿐(정책 2원화 드리프트 소지).

---

## (2) _expectancy_verdict 에 t 검사가 없는 이유와 위험

**사실(높음)**: 조건 6개는 `champion_promote.py:158-169` — ①exp ≤ min_pct ②세션 < 40 ③표본 < 30 ④`halves.stable != "both_positive"` ⑤챔피언 기준선 없음 ⑥`exp < champ_exp + min_improvement`(0.1). `expectancy_t` 는 `:155`에서 **요약에 기록만** 되고 어디서도 검사되지 않는다. docstring(`:138-143`)이 나열한 조건과 코드가 정확히 일치하고, 회귀 테스트(`tests/test_champion_promote_expectancy_gate.py`)도 t 를 assert 하지 않는다 → **누락이 아니라 원래 설계에 t 검사가 없다**.

**계산(높음, 직접 계산)** — `t_stat = mean/(sd/√n)` (`scripts/fillable_expectancy.py` `session_stats()`, 세션 단위 표본):
- 후보: t=0.73, n=87 → SE=0.2151 → **95% CI [−0.271, +0.585] — 0 포함**, 일측 p(H0: μ≤0)=**0.2337** → 양수라고 할 수 없음.
- 챔피언: t=−0.81 → SE=0.2247 → 95% CI [−0.629, +0.265], p=0.2101.
- "챔피언 대비 개선 +0.339%p" 도 t_diff=1.09, p=0.1386 → 유의하지 않음.

**위험(높음)**: 조건 ①~⑥을 전부 통과하는 노이즈 수준 후보(순기대의 95% CI 가 0 을 포함)가 돈 기준을 통과할 수 있다. 조직이 다른 곳에선 t≥2 를 기준으로 쓰고 있다는 점이 대조적 — `fillable_expectancy.py:333,335` 의 자체 합격 기준 `R.t_stat >= 2`. 즉 돈 지표 산출 도구는 t≥2 를 요구하는데 승격 게이트만 t 를 무시한다.

---

## (3) 라이브 스코어 게이트 차단 분기 위치와 운영 위험

**위치(높음)**: `champion_promote.py` 실행 순서 —
1. `:342` `live_gate = _read_live_score_gate(candidate_dir)` — **항상 읽어 결과에 기록**(토큰 상태는 승격 여부와 무관하게 관측됨).
2. `:410-415` `if dry_run:` → `"would_promote"` **조기 반환**.
3. `:417-421` `if not live_gate["ok"]:` → `"blocked_live_score"` 차단 반환 — **dry-run 조기반환 뒤**.

실측 검증: CG133(dry-run)은 `live_score_gate.ok=false(status=blocked)` 를 기록하면서도 `would_promote` 반환(me_cycle_CG133 log의 JSON), CG134(비-dry-run)는 동일 토큰으로 `blocked_live_score`(me_cycle_CG134 log). 이 동작은 `tests/test_champion_promote_live_score_gate.py:105-111`(`test_dry_run_reports_gate_but_does_not_refuse`)에 의도된 것으로 고정돼 있다.

**운영 위험**:
- **TTL 6h** (`:90-92`, env `PROMOTE_LIVE_SCORE_TTL_H` 기본 6): 토큰이 6시간 지나면 passed 여도 `"토큰이 오래됐다"` 로 차단. CG134 는 토큰 ts 21:26:52 → 실행 22:03(약 36분)이라 해당 없음. 새벽 승격 시도는 재프로브 필수 — **fail-closed 방향이라 안전성 문제는 아니고 가용성 문제**.
- **basename 불일치** (`:79-83`): 토큰 후보와 승격 후보 basename 이 다르면 차단(fail-closed). 단 `if want and got and want != got` 구조라 토큰에 `candidate` 키가 **비어 있으면 검사가 통째로 생략**된다(`got=""` → 조건 false). fresh·passed 토큰이 candidate 없이 쓰이는 경로는 현재 없음(게이트 스크립트가 항상 기록, `scripts/gate_promote_live_score.py:37`) — 잠재 허점, 현재 악용 경로 없음(중간).
- **measure_failed 의미 불일치(높음)**: `gate_promote_live_score.py:14,80-81` 은 "rc=3(측정실패) → AUC 게이트에 위임, 차단 안 함"이라고 선언하지만 ①파이프라인은 rc≠0 이면 전부 승격 생략(`full_pipeline_dd.sh:409-422`, else 문구가 rc=3 에도 "신호 0건"으로 오표기), ②rc=3 시 기록되는 토큰 `status="measure_failed"`(`:79`)가 초크포인트 `:93-98`(`status != "passed"`)에서 **역시 차단**된다. 즉 "위임"은 실제로는 절대 일어나지 않고 측정 인프라 장애 = 승격 영구 차단(fail-closed)이 된다.
- **dry-run 보고 왜곡(중간)**: CG133 은 원장 verdict `"게이트 통과(승격후보 생성)"` 로 기록됐는데(`model_engineer_cycle.py:1208` 매핑) 실제 라이브 게이트는 blocked 였다 — dry-run `would_promote` 는 라이브 게이트 판정을 무시하므로(`test_dry_run_reports_gate_but_does_not_refuse`), 원장 문구만 보면 "통과"로 오독된다.

---

## (4) 반증: 정상 작동 vs 결함

**정상 작동 쪽 증거(높음)**:
- 최종 결과(미승격)는 설계대로다. 토큰 `status=blocked`(후보 신호 0건·최대 0.5059 < 0.55·챔피언 6건) → 초크포인트가 `blocked_live_score` 로 거부 → 챔피언 무변경. MT116(10-01: 배포 스코어 0.4733 → 소비자 3세션 무진입) 재발 방지가 실제로 작동했음을 보여주는 재검증 그 자체(`docs/QUANT_MODEL_BACKLOG.json` CG134 hypothesis). 파이프라인도 호스트 게이트에서 이미 승격 생략(`full_pipeline_dd_20261007_2000.log:12757-12758`).
- 돈 기준 5개 조건 통과는 산술적으로 정확하다: 0.157 > 0, 87 ≥ 40, 1340 ≥ 30, both_positive, 0.157 ≥ −0.182+0.1(=+0.239 여유). 조건 목록과 코드·테스트가 일치.

**결함 쪽 증거(높음)**: 돈 기준 단독으로는 이 후보를 막지 못했다. 노이즈 수준 증거(t=0.73, CI 0 포함)로 5/5 통과했고, `min_robust_auc: 0.5`(objective.json:34)는 **어디서도 읽히지 않는** 문서상 기준일 뿐이며(grep: config/state/docs/실험 JSON 에만 존재, 집행 코드 없음), `promote_require_robust=false`(`objective.json:59`)라 robust_auc 0.4737(<0.5, 챔피언 0.4908 보다도 낮음)은 게이트에서 비교 대상조차 되지 못한다. 조직 자체 판단도 동일: `docs/QUANT_BOARD.md:2271` — "유의성(t)은 기준에 없다 … robust 0.4737 < 0.5". 결론: **코드 결함이 아니라 정책 커버리지 갭**(게이트는 명세대로 동작, 명세가 유의성·robust 하한을 포함하지 않음). 라이브 스코어 게이트가 없었으면 이 후보는 승격됐을 것이다(추측이 아니라 조건 전부 통과 + dry-run `would_promote` 실측 근거).

**최소 수정 제안(문턱 변경은 승인 대상 — 제안만)**:
1. `_expectancy_verdict`(`champion_promote.py:158` 부근)에 후보 t 하한 추가: `expectancy_t < min_expectancy_t`(예: 1.64 일측 — `fillable_expectancy.py:333` 의 t≥2 선례와 단위 일치) 시 거부. objective.json acceptance 에 `min_expectancy_t` 추가하고 `objective.py:107-112` 에서 전달.
2. robust 하한 집행: `--require-robust`(전체 비교 방식 변경)는 기존 잠금 이슈(`:254-258`) 때문에 켜지 말고, 후보 `robust_oos.value < min_robust_auc(0.5)` 면 거부하는 **하한만** 추가(게이트에 있는 `candidate_robust_oos` 를 이미 읽고 있으므로 `:291` 직후 몇 줄).
3. 프로토콜 일치 가드: 후보·챔피언 `protocol` 문자열 불일치 시 챔피언 기준선 없음 취급(`:166-167` 기존 분기 재사용).
4. (보고) `model_engineer_cycle.py:1208` 매핑 — dry-run `would_promote` 에 `live_score_gate.ok=false` 일 때 문구 보정, `full_pipeline_dd.sh:422` rc=3 오표기 수정.

---

## 확신도 요약

| 항목 | 확신도 |
|---|---|
| robust_oos.json 작성자(model_metric_protocol_audit.py:522-548)·읽는 쪽(champion_promote.py:291/311)·호출 배선(objective.json:58→objective.py:107→full_pipeline_dd.sh:411) | 높음 |
| 후보/챔피언 같은 행·같은 창(trades.csv 고정 87세션, protocol·split_date 동일) | 높음 |
| t 검사 부재 = 설계상 누락(코드·docstring·테스트 일치), CI 계산 [−0.271, +0.585] 0 포함 | 높음 |
| 라이브 게이트 차단 분기(:417)가 dry-run 조기반환(:410) 뒤 | 높음 |
| TTL 6h·basename 불일치 = fail-closed 차단(가용성 문제, 안전성 문제 아님) | 높음 |
| measure_failed "AUC 위임" 선언이 실제로는 이중 차단으로 무효 | 높음 |
| min_robust_auc 0.5 가 집행 코드 없이 문서상으로만 존재 | 높음 |
| 라이브 스코어 차단은 설계된 정상 작동(MT116 방어 실증) | 높음 |
| 돈 기준 통과가 정책 커버리지 갭(코드 결함 아님) | 높음 |
| dry-run 원장 문구 "게이트 통과" 오독 가능성 | 중간 |
| 토큰 candidate 키 공백 시 검사 우회(현재 악용 경로 없음) | 중간 |

파일 수정 없음 — 읽기 전용 조사 완료.

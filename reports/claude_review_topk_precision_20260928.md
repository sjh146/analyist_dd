검토 완료. 아래는 `/review` 방식(구조적 결함 중심, 스타일 지적 배제)으로 `scripts/topk_precision.py` + `scripts/_topk_precision_test.py`를 분석한 결과입니다. 파일은 수정하지 않았습니다.

## 총평

CG21의 **실제 데이터**(panel_150u, pool 중앙값 90행/일·min 88 > `--min-pool 20`)에서는 두 arm 모두 `sel`을 공유하므로 주 경로의 짝 비교는 성립합니다. 그러나 **①번의 `--min-pool` 탈락 경로에서 restrict-q가 조용히 무력화되는 결함**이 실재하며(재현 확인), **⑤번 k=10 금지는 코드에 없습니다**. 이것들은 CI(합성 데이터)로는 절대 걸리지 않는 "운영에서 터지는" 부류입니다.

---

## ① 공통 후보집합이 같은 분모 위에서 채점되는가

**F1. `--min-pool` 탈락 날짜에서 restrict-q가 무력화되어 '각자 후보집합'으로 회귀한다 — HIGH (확신도 높음)**
- `topk_precision.py:125-126` : `if len(pool) < args.min_pool: continue` — 이 날짜는 `kept_keys`에 항목이 안 남는다.
- `topk_precision.py:139-140` : `basket()`의 `keep = kept_keys[e].get(key) ... else None` → `cand = [c for c in items if (keep is None or c in keep)]`. `keep=None`이면 **전체(무제한) 집합**으로 채점한다.
- 의도는 "건너뛴다"(주석 `topk_precision.py:78-80` "분위 추정이 무의미해 건너뛴다")인데, 실제로는 **건너뛰지 않고** 각 arm의 라벨 통과 집합 그대로를 채점한다. 이는 docstring이 금지한 분모 불일치(비교 불가)를 그대로 재도입한다.
- 재현: `min_pool=4`에 3코드 날짜를 섞자 그 날짜가 탈락·스킵되지 않고 Q05 prec 0.5 / Q30 prec 1.0(서로 다른 분모)으로 집계되어 Δprec −0.25를 만들었다. 같은 버그가 `pool_sizes` 집계(`topk_precision.py:156-157`)에도 있다(제한/무제한 크기가 섞여 `pool_median` 오염).
- 같은 부류: `topk_precision.py:132-135`에서 특정 exp가 `sel`과 교집합이 없으면 그 exp만 `kept_keys` 항목이 빠지고, 역시 `keep=None`→전체 집합으로 채점된다.
- CG21 실데이터에서는 `min_pool 20 < 88`이라 이 경로가 안 걸리지만, 계약("반드시 공통집합")은 깨져 있고 테스트가 이 경로를 전혀 덮지 않는다(`_topk_precision_test.py:66-69`은 pool=4 ≥ min_pool=4라 탈락 없음).

**F2. `sel`은 "라벨의 5% 꼬리"가 아니라 합집합(30% 꼬리)의 5% 재분위 — LOW~중간**
- `topk_precision.py:128` : `lo, hi = quantile(vals, q), quantile(vals, 1-q)`는 **합집합 pool(=q0.30 arm 행, universe의 ~60%)** 위에서 분위를 잰다. 라벨은 `wf_wave.py:264-268`에서 **전체 횡단면(universe 100%)** 위에서 잰다.
- 결과적으로 `sel` ≈ universe의 상하 ~3%로, `--restrict-q 0.05`라는 이름이 암시하는 5% 꼬리보다 **좁다**. 다만 `sel` 안에서는 양 arm의 `y_true`가 동일(sel이 두 라벨 임계보다 더 극단)하므로 **비교 자체는 공정**하다. 판정을 뒤집지는 않지만, "상위 k 정밀도"가 어떤 모집단 위에서 잰 것인지 오독 소지가 있다. (`_cg21_pool_probe.py:2-4`가 이 설계를 명시하므로 의도된 것으로 보이나, 백로그 `expected` 필드의 "≈15행/일"(`QUANT_MODEL_BACKLOG.json:4327`)은 실제 `sel=10행/일`(`:4328`)과 모순된 서술로 혼선을 남긴다.)
- 부수적: `pool`은 덤프 내 **모든** exp의 합집합(`topk_precision.py:117-124`)이라, `--only` 없이 덤프에 제3의 exp가 섞이면 `sel`이 오염된다. CG21은 `--only`로 2개만 남기므로 무해하나 보호장치 없음.

---

## ② 동점 제외 부호검정 처리

**F3. 부호검정 자체는 정확 — 결함 없음 (확신도 높음)**
- `binom_p_two_sided`(`topk_precision.py:58-66`)는 정확 양측 이항검정. `n_eff = n - ties`(`:214`), `pos = sum(d>0)`(`:215`)는 동점(d=0)을 제외하고 올바르게 세며, `binom_p_two_sided(pos, n_eff)`(`:222`)로 일관된다. k=10에서 전부 동점→`n_eff=0`→p=n/a로 무의미 p값이 사라진 것도 정상 동작.
- 낮은 확신의 주의사항만: ① `prec_delta_mean = sum(diffs_p)/n`(`:220`)은 동점(0)을 **포함**한 평균이고, 부호검정은 동점 제외 — 둘이 서로 다른 분모라 혼동하면 안 된다(둘 다 보고됨). ② `prec_sign_p`는 양측이라 방향이 없어 `prec_delta_mean` 부호와 함께 읽어야 함. ③ `folds` 표시(`:224-225`)의 분모는 동점 포함이라 부호검정의 n_eff와 불일치.

---

## ③ (fold,date)를 독립 표본으로 세는 것

**F4. 운영 데이터에서는 유효하나 무방비 (중간)**
- fold test 구간은 정렬 날짜 배열의 **연속·비중첩 블록**이라(`wf_label_sweep.py:711-724`, `te = d[(date>cut) & (date<=nxt)]`), 같은 날짜가 두 폴드에 등장하지 않는다. 덤프도 test행만 기록(`wf_label_sweep.py:878-881`). 따라서 (fold,date)는 운영에서 유일하다.
- 단, 코드가 이 유일성을 **검증하지 않는다**(`topk_precision.py:199-213`은 중복 제거·가드 없이 전부 합산). 날짜가 폴드에 중복 등장하는 사고가 나면 부호검정 n을 조용히 부풀려 p값을 뒤집는다.
- 더구나 테스트가 정확히 그 **중복 시나리오를 '정상'으로 모델링**한다: `_topk_precision_test.py:32-38`은 같은 두 날짜를 fold 1·2에 넣고 `_topk_precision_test.py:84`에서 `n_dates == 4`를 단언한다. 즉 테스트는 독립성 가정을 방어하지 못한다.
- 통계적 부수: 5일 선행수익은 인접 날짜끼리 4일을 공유(자기상관)하므로, 날짜가 달라도 iid가 아니다. 부호검정은 이를 무시한다(설계 한계, 코드 결함 아님).

---

## ④ --json-out 산출물 필드 충분성

**F5. 하위 검증에 불충분한 구멍이 있다 (중간)**
- `topk_precision.py:261-269`가 남기는 필드: `preds, rows, exps, restrict_q, arm, control, per_exp, paired`.
- 빠진 것: **`--min-pool` 값**(비기본값 실행 시 재현 불가), `--k` 목록(암묵적 문자열 키뿐), fold/seed 수 메타데이터.
- 가장 치명적 구멍: **탈락 날짜 수가 없다.** `n_dates`(`:174`)는 "측정된" 날짜만 세고, min-pool·sel-empty·cand<k로 **탈락한 날짜 수와 그 목록을 남기지 않는다.** 승격 판정에 필요한 "275일 중 3일만 살아남았고 그 3일이 유리했는가"(선택/생존 편향)를 하위 역할이 JSON만으로는 검증할 수 없다. 원시 (fold,date,code) 데이터도 없어 공통집합 재구성도 불가.

---

## ⑤ k=10 판정 금지가 코드상 강제되는가

**F6. 강제되지 않는다 — 소프트 경고와 부수적 동점제외에 기댈 뿐 (확신도 높음)**
- 기본값 `--k 3,5,10`(`topk_precision.py:72`), docstring 예시도 `--k 3,5,10`(`:21`). k=10을 거부하거나 건너뛰는 검증이 **없다**.
- `basket`의 가드는 `len(cand) < k`(`:141-142`)뿐이라, cand=10·k=10이면 `10 < 10`이 거짓 → **정상 채점**된다. 공통집합(10행)에서 top-10 = 전량이라 prec은 항상 base rate, 양 arm 동일 → Δ=0 → 전부 동점 → `n_eff=0` → p=n/a가 되는 것뿐이다.
- "k≥pool이면 해석 금지"는 `pool_median` **print 경고**(`:244-246`)일 뿐 강제가 아니고, 그 값마저 F1 때문에 제한/무제한 크기가 섞여 신뢰할 수 없다. 유일한 "금지"는 백로그의 인간 주의사항(`QUANT_MODEL_BACKLOG.json:4328`)이다.
- 결과: k=10은 오탐(p<0.05)을 만들지는 않지만 "Δ+0.0000, p=n/a"를 그대로 출력/저장하므로, 주의사항을 안 읽은 하위 소비자가 이를 "차이 없음"으로 오독할 수 있다.

---

## 추가 발견 (5개 항목 밖)

**F7. `--control` 오타 시 조용히 짝 비교 전체가 사라짐 — 중간 (확신도 높음)**
- `topk_precision.py:99` : `control = args.control if (args.control in exps) else None`. `--arm`이 exps에 없으면 에러·종료(`:95-97`)하는데, `--control`이 없으면 **경고 없이 None**으로 두고 paired 비교를 통째로 스킵한다. 승격 판정 자체가 조용히 소실될 수 있는 비대칭.

**F8. (확인 결과: 아님) 백로그 명령의 호스트 경로 버그는 이미 수정됨**
- `QUANT_MODEL_BACKLOG.json:4315`의 topk 호출은 `scripts/_preds_CG21.jsonl`로 정정되어 있고, `:4328` 주의사항과 일치한다. 라이브 결함이 아니므로 보고에서 제외.

---

## 결론(판정 영향 기준 우선순위)

1. **F1(최우선, HIGH)** — `--min-pool`/빈 `sel` 탈락 시 restrict-q가 무력화되어 분모 불일치 회귀. CG21 실데이터에선 안 터지지만 계약 위반·미검증. `basket`이 `keep=None`일 때 `None`을 반환(스킵)하도록 고치는 것이 근본 수선.
2. **F6(HIGH)** — k=10 금지가 코드에 없음. `k ≥ pool`이면 paired/per_exp에서 스킵하거나 최소한 거부.
3. **F5(중간)** — JSON에 탈락 날짜 수·`min_pool`·`ks`를 남기지 않아 하위 검증 불가(생존 편향 검출 불가).
4. **F4(중간)** — (fold,date) 독립성 무방비 + 테스트가 중복 날짜를 '정상'으로 모델링.
5. **F7/F2(중간~낮음)** — `--control` 무효 시 무경고 스킵 / `sel`의 의미가 "5% 꼬리"가 아님.

수정 없이 보고만 하였습니다.

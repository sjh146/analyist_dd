검토 완료했습니다. 모든 증거는 코드 판독 기준이며, 파일을 수정하지 않고 보고만 합니다.

---

# /review 보고 — wf_label_sweep.py / wf_wave.py / feature_pipeline.py 체크포인트

## 요약 (심각도순)

| # | 발견 | 위치 | 확신도 | 유형 |
|---|---|---|---|---|
| F2 | 패널 캐시가 **비원자적 저장** + 로드 무결성 검사 없음 → 저장 중 kill 시 이후 모든 실행 영구 크래시 | wf_wave.py:152-157, 101-109 | **높음** | 운영 장애 |
| F3 | **캐시 재사용 경로에 code_sig 검증 전무** — 피처 코드 수정 후 재실행해도 구패널 조용히 재사용 (③의 최대 구멍) | wf_wave.py:101-109 | **높음** | 혼합 패널 |
| F4 | code_sig 커버리지가 `feature_engine/*.py` mtime뿐 — `_engineer_features`(scripts/) 수정은 무효화 못 함 | feature_pipeline.py:466-473 | **높음**(메커니즘) | 혼합 패널 |
| F1 | **purge는 공통 날짜축 h거래일, 라벨은 종목별 h번째 '행'** — 거래 갭 종목의 학습 라벨이 테스트 윈도우 가격을 포함 | wf_wave.py:169-170 vs 239 / wf_label_sweep.py:218 vs 230 | 중간-높음 | 누수 |
| F6 | 중복 컬럼명 + `feature_names.index(f)` → 후행 중복 컬럼 탈락·선행 이중 사용 | overnight_ml_loop.py:317 | 중간 | 정확성 |
| F7 | `--summary-out` 디렉터리 미생성 → **전 실험 완료 후 크래시**, 요약 손실 | wf_label_sweep.py:373 vs 196 | **높음** | 운영 장애 |
| F5 | 오버샘플→셔플→67/33 분할: val에 학습 행의 **정확한 복제** 포함, 조기종료 낙관 편향 | overnight_ml_loop.py:322-323, xgboost_model.py:65 | 높음(메커니즘) | 진단 편향 |
| F8 | code_sig 계산 실패 시 None → mid-build 가드 조용히 우회 | feature_pipeline.py:472-473, wf_wave.py:141 | 낮음 | 혼합 패널 |
| F9 | 두 파일 체크포인트(rows.pkl/meta.json) 비트랜잭션 → 재개 시 중복 행 | feature_pipeline.py:482-490 | 낮음 | 데이터 무결성 |
| F10 | 동시 러너 잠금 없음 — 체크포인트/최종 npz 경합 | wf_wave.py:93-165 | 낮음 | 동시성 |
| F11 | 캐시 히트 시 `--days`/`--limit` 무시 | wf_wave.py:101-109 | 낮음 | 운영 |
| F12 | `subset`의 curated 분기와 monkeypatch 불일치(latent) | wf_wave.py:42 vs 215 | 낮음 | latent |

---

## ① 누수(미래정보 사용) 가능 지점

### F1. purge(h거래일)와 라벨(종목별 h번째 행)의 단위 불일치 — **중간-높음**
- `wf_wave.py:169-170`: `ret = df.groupby("stock_code")["price"].transform(lambda s: s.shift(-horizon) / s - 1.0)` — `shift`는 **종목별 '행' 기준**입니다. 갭(정지/미상장일)이 있는 종목의 라벨은 h번째 미래 **거래일**을 참조하므로 달력상 h일보다 길어집니다.
- `wf_wave.py:239`: `purge = set(dd[max(0, step * i - h):step * i])` — purge는 패널 **공통 날짜축(dd)** 기준 h거래일만 제거합니다.
- 갭 종목의 경우 경계 밖 학습 행(t ≤ dd[step\*i−h−1])의 라벨이 참조하는 가격이 dd[step\*i](테스트 시작) **이후**일 수 있습니다 → 학습 라벨에 테스트 윈도우 가격 유입 → 테스트 AUC 부풀림.
- 구조적 근거: 유니버스 기본값이 `min_days=50`(wf_label_sweep.py:139)이라 420거래일 중 50일만 있는 종목이 포함 가능 → 갭이 흔합니다.
- **반증 방법**: 종목별로 각 행의 라벨 참조일(그 행의 h번째 미래 종목행의 date)을 계산하고, tr 행 중 참조일 ≥ dd[step\*i]인 행 수를 폴드별로 집계. 0이 아니면 확정.
- 실측/추측: shift가 행 기준이라는 것은 pandas 의미론으로 **확정(실측)**. 갭 종목의 실제 빈도는 미검증(추측).

### F5. 조기종료 val에 학습 행 복제 포함 — **높음(메커니즘) / 중간(영향)**
- `overnight_ml_loop.py:322-323`: `oversample_balance`가 양성 행을 **복원추출·셔플**한 뒤 `split_train_val(..., 0.67)`로 분할 → 동일한(복제된) 행이 train/val 양쪽에 존재할 수 있고, 라벨 윈도우도 겹칩니다.
- `xgboost_model.py:65` / `catboost_model.py:55`: 이 val로 `early_stopping_rounds` → 낙관적 val AUC로 스톱 지점 결정.
- 테스트 폴드 정보는 아니므로 **엄밀한 누수는 아님** — 측정 AUC에 간접 편향. 단 라벨이 h일 윈도우를 공유하므로 "미래정보"가 val을 통해 스톱 결정에 들어가는 경로로는 볼 수 있습니다.
- **반증 방법**: 오버샘플 복제 인덱스를 추적해 train/val 교집합 존재 확인(코드 판독으로는 확정적).

### 누수가 없는 것으로 확인한 경로 (②와 연결)
- `transform_matrix`(wf_label_sweep.py:120-125): 날짜별 그룹 내 변환 — 같은 날 종목간 정보만 사용 ✓
- `subset`/`edge_of`(wf_wave.py:80-90): Xtr/ytr만 사용 ✓
- `pool` 필터(wf_label_sweep.py:252-253), 시장레벨 마스크(:270-286): tr만 사용 ✓
- `make_labels`의 분위/중앙값(wf_wave.py:172-182): 날짜내 횡단면만 ✓
- `build_features`의 market_df 절단(feature_pipeline.py:110-115): 목표 날짜 이후 행 제거 — `_last()`의 `iloc[-1]`(feature_pipeline.py:263)도 절단 후 시리즈라 무해함을 확인 ✓

---

## ② 시장레벨 마스크 — 학습 폴드 전용 여부와 중복 컬럼명 함정

**결론: 마스크는 tr에서만 계산되며 테스트 폴드 정보 유입 경로는 없음. 중복 컬럼명 함정은 수정본이 정확히 회피했음.**

- 마스크 판정: `wf_label_sweep.py:270` `_mat = tr[base_names].values` — **tr 전용**. 이후 `cols`(:245, Xtr), `pool_mask`(:252, tr), `subset`(Xtr/ytr) 모두 tr 전용. 테스트 폴드가 마스크에 영향을 주는 경로 없음. 적용은 같은 `cols`로 Xte에도 하되(:294) 판정은 train — 올바른 방향.
- 중복 컬럼명 함정: 수정본은 (a) 위치 기반 numpy 연산만 사용, (b) 합성 유니크 컬럼명 `_f{j}`로 프레임 생성(:271-272), (c) `_d` 컬럼 drop(:277-278)까지 정확 → **pandas 라벨 정렬 함정 회피 성공**. `nunique`/`count`의 `.values`가 위치 순서를 유지한다는 전제도 성립(중복 라벨이 있어도 컬럼 순서는 보존).
- **남아 있는 함정 — F6**: `overnight_ml_loop.py:317` `idx = [feature_names.index(f) for f in curated]` — sel에 중복명이 있으면(`wf_label_sweep.py:296`에서 sel 전달) 후행 컬럼은 탈락하고 선행 컬럼이 두 번 들어갑니다. sweep의 monkeypatch(:193)가 `curated=list(n)`으로 만든 뒤 이 경로를 그대로 타므로 발현 가능.
  - 영향도는 중복 쌍이 동일값인지에 달림. `_engineer_features`가 `df[name]=...` **덮어쓰기 후** `available.extend(added)`로 **이중 등재**(overnight_ml_loop.py:215-268)하는 구조상, 패널의 14개 중복은 동일 컬럼이 두 번 저장된 것일 가능성이 높습니다(추측 — npz 값 비교로 검증하려 했으나 실행 미승인으로 **미검증**). 동일값이면 수치 영향 ≈0이고 `selected_features` 메타데이터만 오기재됩니다.
  - **반증 방법**: `panel_420.npz`에서 중복명 쌍의 `X[:, i] == X[:, j]` 동일성 비교.

---

## ③ 체크포인트 재개가 '피처 코드 변경 혼합 패널'을 확실히 막는가 — **부분적으로만**

**작동하는 부분(확인됨):**
- 재개 검증 키: `feature_pipeline.py:404-407` — (stock_codes, start_date, end_date, code_sig) 전부 일치할 때만 재개. 유니버스/구간/피처코드 변경 시 "처음부터 빌드"(:420) ✓
- 빌드 중 변경 가드: `wf_wave.py:140-149` — sig 불일치 시 체크포인트 삭제 + 명시적 실패 + npz 미저장 ✓
- 체크포인트 저장은 tmp+`os.replace` 원자적(:482-490) ✓

**구멍:**
- **F3(높음)**: `wf_wave.py:101-109` — 캐시 히트 시 **어떤 검증도 없이** np.load→반환. npz에 meta(코드시그·유니버스·구간)가 저장되지 않으므로 검증 자체가 불가. 피처 코드를 고치고 재실행해도 구패널이 조용히 재사용되고, mid-build 가드나 재개 검증은 이 경로를 아예 거치지 않습니다. 반증: feature_engine/*.py 임의 수정 후 재실행 → 로그 "panel cache 재사용" 확인.
- **F4(높음-메커니즘)**: `feature_pipeline.py:466-473` — `_feature_code_sig`는 `feature_engine/` 디렉터리 .py mtime만 해시. 패널 210개 피처 중 상당수(상호작용 8종·롤링·rank_*·target_ma_*)를 만드는 `scripts/overnight_ml_loop.py:208-269`(_engineer_features)는 커버 밖 → 이 파일 수정은 재개 검증(:407)과 mid-build 가드(wf_wave.py:141)를 모두 무력화. 다만 _engineer_features는 빌드 후 단계라 체크포인트 '행'과는 무관 → 실질 위험은 F3(캐시 재사용)으로 수렴.
- **F8(낮음)**: sig 계산 예외 시 None 반환(:472-473) → `wf_wave.py:141`의 `is not None` 조건이 가드를 조용히 통과 → 혼합 패널 저장 가능.
- **F9(낮음)**: `feature_pipeline.py:482-490` — rows.pkl 교체 후 meta.json 교체 사이 크래시 시 done_keys가 rows보다 뒤처짐 → 재개 시(:435) 갭 페어 재처리 → **중복 행**(해당 종목-날짜 이중 가중). 창은 ms 단위지만 빌드당 ~80회 반복. 참고: 지금 저장소에 `panel_150u.npz.rows.pkl/.meta.json`(mtime 22:14)이 있어 이 재개 경로가 곧 실사용될 예정.
- **F10(낮음)**: build_panel에 잠금 없음 — 같은 cache 경로로 두 러너 동시 빌드 시 체크포인트 교차 오염 + 최종 npz last-writer-wins.

**결론**: 빌드 중·재개 시점의 feature_engine 코드 변경은 잡히지만, **빌드 후 코드 변경(캐시 재사용)**·패키지 밖 코드 변경·동시성은 막지 못합니다. "확실히 막는다"는 아니고 "주요 경로 하나는 막는다"입니다.

---

## ④ pooled AUC + 날짜별 AUC 병기 진단의 타당성

**타당함.** 날짜내 분위 라벨에서 pooled AUC는 날짜 상수(시장레벨) 피처가 날짜별 점수 분포를 통째로 밀어 부풀릴 수 있고, 날짜별 횡단면 AUC 평균은 그 성분을 제거하므로 두 값을 병기하면 "개선이 종목간 실력인지 날짜 구성의 산물인지"를 판별 가능합니다. MK_nomkt 실험과 짝을 이루는 설계로 적절합니다.

주의점(모두 ⑤의 낮은 확신도 계열):
1. **날짜별 표본이 작음** — 49종목·q=0.3이면 날짜당 양성 ~15 → 날짜별 AUC 분산이 매우 큽니다. 평균과 건수만 기록하고(:327-331) std는 없으므로, 날짜별 std도 함께 기록할 것을 권합니다.
2. **비교 대상의 추정기 불일치** — 폴드 headline "mean"은 시드별 AUC 평균(:334)인데 `daily_auc_mean`은 bagged 앙상블 확률(:320)입니다. 같은 추정기로 비교하는 게 깔끔합니다(낮음).
3. F5의 조기종료 편향이 pooled·daily 양쪽에 실립니다(낮음-중간).
4. 날짜별 클래스 균형은 quantile/relative 라벨 모두 자동 균형이라 up_rate 문제는 없음 ✓. `nunique()>1` 가드(:328)로 단일클래스 예외도 차단됨 ✓.

---

## ⑤ 확신도 낮음 항목 / 추측 vs 실측

**실측(코드 판독으로 확정된 메커니즘):** F2·F3·F7·F12, F4의 커버리지, F5의 분할 순서, F6의 `.index()` 붕괴, F9의 두 파일 교체 순서, F1의 shift 행 기준.

**추측(미검증 — 빈도·영향도):**
- F1: 갭 종목의 실제 빈도/영향 크기 — 반증 집계 필요
- F6: 패널 중복 쌍의 동일값 여부 — npz 비교 필요 (수치 영향이 0인지 결정)
- F2: 크래시 창 확률, F9: 비트랜잭션 창 확률 — 둘 다 ms 단위 창
- F10: 부하 가드(커밋 메시지상 존재)가 실제로 동시 실행을 차단하는지 — 코드 레벨 확인 못 함
- F11: `--days`/`--limit`를 패널 파일명과 다르게 주는 운용 패턴이 실제 있는지

**부록 — 경미한 사항(스타일 아님, 영향 낮음):** `--out`에 디렉터리 없는 파일명만 주면 `os.makedirs("")`가 시작 시점에 크래시(wf_label_sweep.py:196).

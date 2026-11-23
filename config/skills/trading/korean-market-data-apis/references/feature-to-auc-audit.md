# 피처를 채운 뒤 AUC 로 판정하기 전에 하는 감사

데이터를 채웠는데 AUC 가 그대로일 때, 또는 "측정 AUC 0.54" 를 성능으로 부를 때
**먼저** 거치는 절차다. 순서를 뒤집으면 존재하지 않는 성능을 개선하려 들거나,
쓸 수 있는 데이터를 무용으로 버린다.

실측으로 반증된 두 개의 기본 가정:
- "데이터를 채우면 AUC 가 오른다" → 신규 피처가 **선별 문턱**을 못 넘으면 기여가 정확히 0 이다.
- "측정 AUC 가 실력" → **종목-상수 피처 룩어헤드**가 섞이면 부풀려진 값이다.

---

## 1. 종목-상수 피처 = 룩어헤드 후보 (가장 중요)

패널에서 **종목당 값이 1개뿐인** 피처를 찾는다. 재무·기업속성 스냅샷을 모든 과거 날짜에 붙인
패널이라면 최신 스냅샷이 과거 행에 들어가 있다는 뜻이고, 그 피처가 단변량 edge 상위와
top-K 선별을 독차지한다. **walk-forward 의 purge 로는 잡히지 않는다** — 누수가 라벨이 아니라
피처에 있기 때문이다.

```python
X = pd.DataFrame(z["X"].astype(float), columns=[str(n) for n in z["feature_names"]])
X["stock_code"] = [str(c) for c in z["codes"]]
nun = X.groupby("stock_code").nunique()          # 중복 컬럼명 있으면 .iloc 기반으로
frac_const = (nun <= 1).mean()                     # 피처별: 종목상수 비율
# 종목상수비율 1.00 + edge 상위  → 룩어헤드 확정으로 취급
```

판정은 **두 풀을 같은 폴드에서 각각 측정**해서 한다(`pool="const"` vs `pool="timevary"`).
`const` 풀만으로도 AUC 가 나오면 그 성능은 예측력이 아니라 **종목 식별**이다.

### 1a. 원인은 리더의 as-of 누락 — 탐지에서 끝내지 말고 고친다

종목-상수는 데이터가 없어서가 아니라 **리더가 `date` 를 안 받아서** 생긴다. 호출 체인에서 `date` 가
끊기는 지점을 찾는다(실측: `get_all_features(stock_code, db_conn, date)` 가 percentile 메서드에는 date 를
넘기면서 재무 메서드에는 안 넘겼고, 그 메서드는 `ORDER BY report_date DESC LIMIT 2` 로 **항상 최신 스냅샷**을
읽어 최대 10개월 룩어헤드를 만들었다). 수정은 시그니처 + 조회 조건 2줄이다.

```sql
-- 공시 지연을 반영한다: 결산일(report_date)과 실제 공시일은 다르다.
-- 사업보고서(12-31) 90일, 분기·반기 45일 — 늦게 잡는 쪽이 안전(과거 정보 사용 금지).
WHERE stock_code = %s
  AND report_date + (CASE WHEN report_date = date_trunc('year', report_date)::date
                          THEN INTERVAL '90 days' ELSE INTERVAL '45 days' END) <= %s::date
ORDER BY report_date DESC LIMIT 2
```

- 수정은 **시점별로 다른 값이 나오는지 3개 날짜로 확인**한다(FY2024 → FY2025 → H1 처럼). 같은 값이면 배선이 안 된 것이다.
- **정확한 정합은 공시 접수일이 필요하다.** 기간말(`report_date`)만 있는 스키마에서는 위 지연 근사가 최선이고,
  남는 한계(최대 1~3개월)를 보고에 명시한다. 접수일 컬럼 추가가 다음 단계다.
- **연간·분기 행이 섞인 테이블에서는 성장률 피처가 무의미해진다**(반기 값 ÷ 연간 값). 기간 유형 컬럼이 없으면
  `revenue_growth_yoy` 류가 엉뚱한 값을 내므로, 그 피처를 근거로 판단하지 말고 기간 유형부터 채운다.
- **누수 제거가 성능을 떨어뜨리지 않을 수 있다**(실측: 전체 0.538 vs 시간가변만 0.544). 그러면 그 룩어헤드는
  성능의 원천이 아니라 **선별을 낭비한 잡음**이었다는 뜻이다 — 제거하고 그대로 진행한다.

### 1b. 변환·정규화의 이득이 종목정체성 증폭인지 분리 측정한다

횡단면 rank/z-score 같은 변환이 AUC 를 올려 보여도, 그 이득이 **종목-상수 피처의 순위화(=종목 식별)** 에서
온 것일 수 있다. 같은 런에서 **`pool=timevary` 로도 변환을 켜서** 재측정한다.
실측: 전체 피처에서 rank 는 +0.0102(승 4/5) 였지만 종목-상수를 뺀 풀에서는 **−0.028(승 1/5)** 로 해로웠다
→ 개선으로 보고했다가 철회했다. **"변환이 좋다"는 결론은 두 풀에서 모두 나올 때만 유효하다.**

## 2. 날짜 내 횡단면 분산 0 = 랭킹 모델에서 변별력 0

```python
g = X.drop(columns=["stock_code"]).groupby(dates)
flat = [c for c in X.columns if float(g[c].std(ddof=0).fillna(0).max()) == 0.0]
```

시장 전체 값(그 날 전 종목이 동일)은 순위를 가를 정보가 없다 — 실측 edge 0.0002.
이런 소스는 **피처가 아니라 레짐 게이트**로 쓴다(수집이 무의미한 것은 아니다).

## 3. 커버리지·비영 비율은 `notna()` 로 센다

`col != 0` 은 **`NaN != 0` 이 True** 라서 NaN 을 "값 있음"으로 집계한다.
이 오독으로 "커버리지 99%" 라고 보고했다가 종목별 `nunique` 중앙값 0 으로 뒤집힌 적이 있다.

```python
cov = float(np.mean(np.notna(col)))                     # 맞다
nz  = float(np.mean(col.fillna(0) != 0))                # 0 이 아닌 값만
n_stocks_covered = col.groupby(stock).apply(lambda s: s.notna().any()).sum()
```

## 4. 선별 문턱과 신규 피처의 순위를 맞대라

피처를 단변량 edge 로 추리는 파이프라인은 **폴드 학습구간에서** 상위 K 를 뽑는다.
K=30 일 때 문턱은 **edge ≈0.045~0.05** 이다. 신규 그룹의 `max edge` 가 그 아래면
**선별 자체가 불가능**하므로 AUC 기여 0 은 당연한 결과다.

```python
def auc(x, y):   # 단일피처 AUC (Mann-Whitney U, tie 평균, NaN 제거)
    m = ~np.isnan(x); x, y = x[m], y[m]
    if len(x) < 50 or len(np.unique(x)) < 2 or y.min() == y.max(): return None
    o = np.argsort(x, kind="mergesort"); r = np.empty(len(x)); r[o] = np.arange(1, len(x) + 1)
    xs = x[o]; i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[j + 1] == xs[i]: j += 1
        if j > i: r[o[i:j + 1]] = (i + 1 + j + 1) / 2.0
        i = j + 1
    n1 = int(y.sum()); n0 = len(y) - n1
    return None if n1 == 0 or n0 == 0 else (r[y == 1].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0)

edge = lambda a: abs(a - 0.5)
ranked = sorted(names, key=lambda n: -edge(auc(X[n].values[mask], y[mask]) or 0.5))
threshold = edge(auc(X[ranked[29]].values[mask], y[mask]))   # top30 문턱
```

출력은 **신규 그룹 max edge 와 문턱을 나란히** 보고한다. 순위(예: 96/172)도 함께 남기면
"선별 불가"와 "신호 없음"이 구분된다.

## 5. 통제 비교 프로토콜 (이걸 안 지키면 노이즈를 성능차로 읽는다)

- **대조군을 같은 런에 포함**한다(`--only baseline,변형1,변형2`). 시드 3개 런과 5개 런의 평균을
  맞대는 것은 금지 — 시드 수가 다르면 평균이 이동한다.
- 같은 패널·같은 폴드 경계·같은 purge·같은 시드.
- 판정은 **폴드별 짝 델타 + 승패 수**(부호검정)로 한다. 평균만 보면 한 폴드의 0.59 같은 값이
  전체를 끌고 간다. 승 4/5, 평균 +0.010 수준은 **제안이지 확정이 아니다**.
- **문턱을 사전에 정한다**: 폴드 std 가 ±0.03 이면 **평균 +0.02 이상**일 때만 신호로 인정.
- 폴드 **표준편차 감소를 성능 개선으로 읽지 마라**(시장상대 라벨은 편차가 절반이지만 평균이 더 낮았다).

## 6. 러너·실행 함정

- **패널 캐시가 설정을 따라가지 않으면 낡은 데이터를 측정한다.** 캐시 파일명이 `--days` 로만 정해지는
  러너는 `--limit` 을 올려도 같은 파일을 읽는다 → 캐시를 **이름만 바꿔 옮기고**(삭제 금지, 복구 가능)
  재빌드하거나, 설정별 캐시 경로를 인자로 넘긴다. "데이터를 채웠는데 결과가 같다" 의 흔한 원인이다.
- **피처명 중복**이 있으면 pandas 라벨 인덱싱(`df[col]`, `groupby[c]`)이 **DataFrame 을 반환**해
  `float()`·비교가 `TypeError` 로 깨진다 → `.iloc[:, i]` 로 위치 접근하고, 중복명은 계약에서 제거한다.
- **백그라운드 체인 대기에서 자기매칭**: `while pgrep -f 'scripts/x.py'` 는 그 문자열을 포함한
  **자기 자신의 명령줄과 매칭**되어 영원히 대기한다(로그 파일조차 안 생긴다) → `pgrep -f '[s]cripts/x.py'`
  브래킷 트릭을 쓰거나, 선행 잡이 쓰는 **마커 파일**을 기다린다.
- 컨테이너에서 무거운 잡을 돌릴 때는 스레드를 제한한다(`OMP_NUM_THREADS`). 4코어에서 패널 빌드와
  학습을 동시에 돌리면 둘 다 느려지고, 패널 빌드가 지배 비용이다(50종목·281일 ≈ 100분).
  비용을 먼저 재고 유니버스를 넓힐지 결정한다(150종목이면 수 시간).
- 러너를 컨테이너에서 돌릴 때 `sys.path` 에 `/app` 과 `/app/scripts` 를 **둘 다** 넣는다.

## 7. 검증 대상 집합이 하나라고 가정하지 마라

같은 레포에 **학습 유니버스와 평가(패널·백테스트) 유니버스가 따로** 있고 서로 다른 집합인 경우가 많다.
한쪽만 데이터를 채우면 **평가 패널에서 그 피처가 0 이 되어 "효과 없음"으로 잘못 결론낸다**
(실측: 패널 50종목 중 33종목이 미커버 — 그대로 돌렸으면 틀린 결론이 나갔다).

순서: ① 두 유니버스 목록을 각각 뽑른다(학습: `select_training_universe`, 평가: `train_curated._select_universe`)
→ ② 피처 테이블이 커버한 종목 집합과 **diff 를 세어 0 이 될 때까지 채운다** → ③ 그 다음 패널을 빌드한다.
확장 비용을 먼저 계산한다(종목당 9콜 × 100종목 ≈ 15분).

## 8. 도구

`scripts/wf_label_sweep.py` — 기존 walk-forward 러너의 패널·폴드·purge·선별·학습을 import 로 재사용하고
**라벨 / 피처 변환 / 피처 풀 / 선별 K** 축만 갈아끼운다(결과가 같은 패널에서 직접 비교 가능).

```
cd /app && OMP_NUM_THREADS=2 python -u scripts/wf_label_sweep.py \
  --panel /app/app/models/wf/panel_420.npz --folds 5 --seeds 5 \
  --only LS_quant_q30_h5,TR_rank_h5,PO_timevary_h5,PO_const_h5
```

- `--panel` 로 **설정별 캐시 경로**를 지정할 수 있다(유니버스 확대 시 필수).
- `--limit` 은 패널이 없을 때만 쓰인다(있으면 재사용).
- 축: label(`quantile`/`relative` × `horizon` × `q`) · `transform`(`none`/`rank`/`zscore`) ·
  `pool`(`none`/`const`/`timevary`) · `select`(`top30`/`top40`/`top60`).
- 새 축을 추가할 때는 **CONFIGS 에 대조군을 함께 넣고**, 결과를 `auc_mean` 뿐 아니라
  폴드별 값으로 남겨 짝 델타를 계산할 수 있게 한다.

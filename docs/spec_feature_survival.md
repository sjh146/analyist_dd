# 스펙 — 피처 생존 판정기 (3축: 분산 · 커버리지 · 누수)

작성 2026-11-17. **사용자 승인**: "응 1로 진행해줘"
(직전 제안 1번: "분산·커버리지·누수 3축을 자동 산출해 죽은 피처 사유를 분류 — 부활 작업 우선순위 근거").

배경 실측:
- 패널 정본: `/home/jhshi/analyist_dd/services/xgboost-ml/app/models/wf/panel_prod200.npz`
  (호스트에서 읽을 수 있음, 17.5MB, root 소유 r--r--). 컨테이너에서는 `/app/app/models/wf/panel_prod200.npz`.
  키: `X (54800, 213) float32` · `feature_names (213,)` · `dates (54800,)` · `codes (54800,)` · `price (54800,) float64`.
  스냅샷 2025-08-04~2026-09-23, 200종목, 274~279날짜.
- 기존 분류 자산: `data/reports/r13_dead_feature_audit.json` (2026-09-29) — `dead_total=35`,
  buckets `buildable(11) / coverage_low(7) / market_level(7) / …`, features[] 에 `feature·bucket·source·why·probe`.
- 과거 판정 이력: alive/dead 가 76/97 → 16/3 → … 로 **창 정의에 따라 뒤집힌 전례**가 있다
  (`docs/QUANT_FINDINGS.md`). 그래서 이 스크립트는 **판정 기준을 출력에 함께 남겨** 재현·비교가 되게 한다.
- 계기판의 `feature_alive_count`/`feature_dead_count` 는 지금 Prometheus 경유 값이 `n/a` 로 나온다
  (`data/reports/quant_scoreboard.json`) — 이 스크립트가 그 공백을 패널 직접 계산으로 메운다.

## 목표

패널만 읽어 **피처별 생존 판정**을 3축으로 자동 산출하고, 죽은 피처는 **사유를 한 개로 분류**해
부활 우선순위를 데이터로 정할 수 있게 한다. 새 지표를 발명하지 않고, 이미 쓰는 정의
(`nonzero_ratio_naive/honest`, `cross_section_constant_ratio`, `stock_unique_median`)를 재사용한다.

## 산출물 (계약)

### 1. `scripts/feature_survival_report.py` (신규, 호스트 `/usr/bin/python3` 로 실행)

CLI:
```
python3 scripts/feature_survival_report.py [--panel PATH] [--h 5] [--min-coverage 0.05]
        [--json-out PATH] [--md-out PATH] [--top-pairs 20] [--quiet]
```
- `--panel` 기본값은 위 호스트 경로. 파일이 없으면 rc=1 + 명확한 메시지(빈 산출물로 성공 위장 금지).
- numpy/pandas 만 사용(호스트에 numpy 1.26.4 · pandas 2.1.4 있음). psycopg2·네트워크·DB 접근 금지.
- 출력 기본 경로: `data/reports/feature_survival.json` · `data/reports/feature_survival.md`.

### 2. 피처별 통계 (전부 실측, NaN 은 값으로 세지 않는다)

각 피처에 대해: `rows`, `null_ratio`, `nnz_ratio_honest`(NaN 제외 non-zero 비율),
`std`(NaN 무시), `nunique`, `coverage_days`(값이 하나라도 있는 날짜 수 / 전체 날짜 수),
`coverage_codes`, `xsec_std_median`(날짜별 횡단면 표준편차의 중앙값),
`xsec_constant_ratio`(그 날짜 안에서 값이 전부 동일한 날짜 비율),
`time_std`(날짜별 평균의 시계열 표준편차).

### 3. 판정 등급 (우선순위대로 **한 개만** 부여)

1. `DEAD_ALL_NULL` — null_ratio ≥ 0.999
2. `DEAD_CONST_ZERO` — 유효값이 전부 0
3. `DEAD_CONST_OTHER` — nunique == 1 (0 이 아닌 상수 포함)
4. `DEAD_SPARSE` — coverage_days < `--min-coverage` **또는** nnz_ratio_honest < `--min-coverage`
5. `CONST_MARKET` — `xsec_constant_ratio ≥ 0.95` (시장 공통값: 횡단면 정보 없음)
6. `CONST_STOCK` — `time_std ≈ 0` (종목 고정값: 시계열 정보 없음)
7. `LEAK_SUSPECT` — 아래 누수 검사 2건 중 하나라도 참
8. `ALIVE` — 위 어디에도 해당 없음

`alive/dead` 집계 정의(출력 `summary` 에 명시): `DEAD_*` 4종 = dead, `ALIVE` = alive,
`CONST_MARKET`/`CONST_STOCK`/`LEAK_SUSPECT` = **조건부**(횡단면·시계열 중 한 축만 유효).
→ 계기판의 alive/dead 와 숫자가 다를 수 있으므로 **세 숫자를 모두 출력**하고 차이 사유를 md 에 적는다.

### 4. 누수 검사 (2건, 객관 정의만)

종목별로 `dates` 오름차순 정렬 후 `price` 로 수익률을 만든다(코드별 결측·중복 날짜 방어).
- `L1_label_collinear`: `|corr(feature_t, fwd_ret_{t→t+h})| ≥ 0.90`
- `L2_contemporaneous`: `|corr(feature_t, same_day_ret_{t-1→t})| ≥ 0.95`
  (결정 시점에 알 수 없는 당일 종가 수익률과 사실상 동일 → 미래정보 의심)
- 표본 조건: 두 값이 모두 유효한 행이 500개 미만이면 판정하지 않고 `leak:n/a(표본부족)` 로 남긴다.
- 한계 명시: 이 두 검사는 **의심** 표시일 뿐 누수를 증명하지 않는다. md 에 "생성 코드를 봐야 확정"이라 적는다.

### 5. 중복 쌍(참고)

`|corr| ≥ 0.9999` 인 피처 쌍을 `--top-pairs` 개까지 `redundant_pairs` 에 남긴다(정보 제공용, 판정에 영향 없음).

### 6. 기존 r13 분류와의 교차표

`data/reports/r13_dead_feature_audit.json` 이 있으면 읽어(없으면 `r13: null`)
`features[]` 의 `bucket` 을 이 스크립트 판정과 교차 집계한 `r13_crosstab` 을 출력에 넣는다.
이때 r13 의 `dead_total`(35) 과 이번 실측 dead 수를 **둘 다** 적고, 차이 나는 피처 이름을 최대 20개 나열한다.

### 7. 출력 스키마 (json)

```
{"panel": {"path","mtime","shape","features","dates","span":[start,end],"codes"},
 "params": {"h","min_coverage","thresholds":{...}},
 "summary": {"total","alive","dead","conditional","by_class":{...},
             "alive_and_dead": {"alive":N,"dead":M,"note":"계기판 정의와 다름 — 사유"}},
 "features": [{"feature","class","reasons":[...],"stats":{...},
               "leak": {"L1":bool|null,"L2":bool|null,"n":int,"note":...}}],
 "redundant_pairs": [{"a","b","corr"}],
 "r13_crosstab": {...} | null}
```
- `generated_at` 같은 실행 시각은 **넣지 않는다**(재실행 멱등: 같은 패널 → 같은 바이트). 시점 정보는 `panel.mtime` 만 쓴다.

### 8. `data/reports/feature_survival.md` (사람용 요약)

- 패널 스냅샷 1줄(경로·shape·기간·mtime) — **오래된 패널이면 "스냅샷이 N일 전" 명시**
- `summary.by_class` 표, 상위 죽은 피처 20개(사유 포함), `LEAK_SUSPECT` 전체 목록, r13 교차표, 그리고 "한계" 절(임계값·누수검사 한계·패널 나이).
- **하드코딩된 숫자 금지**(전부 계산값 삽입).

## 테스트 — `tests/test_feature_survival.py` (신규 7개)

합성 패널(numpy)로 각 등급 하나씩:
① 전부 NaN → `DEAD_ALL_NULL` ② 전부 0 → `DEAD_CONST_ZERO` ③ 상수 3.0 → `DEAD_CONST_OTHER`
④ 10%만 값이 있는 피처 → `DEAD_SPARSE` ⑤ 날짜 내 상수·날짜 간 변동 → `CONST_MARKET`
⑥ 종목 고정·날짜 변동 없음 → `CONST_STOCK` ⑦ `fwd_ret` 를 그대로 넣은 피처 → `LEAK_SUSPECT`
+ ⑧ 정상 난수 피처 → `ALIVE`, ⑨ `--days` 슬라이스 없이 재실행 시 JSON 바이트 동일(멱등).
(⑦⑧은 같은 테스트에서 확인해도 된다. **기존 테스트 파일은 건드리지 않는다**.)

## 검증 (실행하고 출력 원문을 보고에 붙인다)

```
cd /home/jhshi/analyist_dd
/usr/bin/python3 -m pytest tests/test_feature_survival.py -q
/usr/bin/python3 scripts/feature_survival_report.py --quiet && echo OK
/usr/bin/python3 -c "
import json;d=json.load(open('data/reports/feature_survival.json'))
print(json.dumps(d['panel'],ensure_ascii=False)); print(json.dumps(d['summary'],ensure_ascii=False))
print('features:',len(d['features']),'| classes:',sorted({f['class'] for f in d['features']}))"
md5sum data/reports/feature_survival.json
/usr/bin/python3 scripts/feature_survival_report.py --quiet && md5sum data/reports/feature_survival.json   # 멱등: 두 md5 동일
```
- `panel.shape == [54800, 213]`, `len(features) == 213`, 모든 class 가 위 8개 중 하나.
- md5 두 번 동일(멱등).
- `LEAK_SUSPECT` 로 분류된 피처가 있으면 이름을 보고에 나열하고, **없으면 "0건"이라고 명시**(둘 다 정상, 사실대로).

## 금지

- `git` 명령 실행 금지(커밋은 오케스트레이터).
- 패널 `.npz` 수정·이동·삭제 금지(읽기 전용). `services/xgboost-ml/` 아래 파일 수정 금지.
- DB·브리지·네트워크 접근 금지. `docker` 명령 실행 금지.
- 기존 산출물(`r13_dead_feature_audit.json` 등) 수정 금지 — 읽기만.
- 기존 테스트 기대값 수정 금지. 리포 밖 파일 생성 금지(임시파일은 /tmp).
- 판정 임계값을 결과에 맞춰 사후 조정하지 말 것(스펙 값 그대로 쓰고, 결과가 이상하면 보고에 적는다).

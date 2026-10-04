# CG57 리그 배포 전 리뷰 — Δ 해석을 못 믿게 만드는 결함만

검토 방법: 5개 파일 전 경로 정독 + 앙상블 로드·체크포인트 키 코드까지 추적. 스타일 지적 없음. 판정 하나는 **"결함 확실함"과 "발생 조건"을 분리**해 표기했다.

---

## 0. 판정 요약

| # | 발견 | 위치 | 확신도 |
|---|------|------|--------|
| A | liq 학습 표본 순위가 [now-60d, **now**] 구간 — 학습종료(06-24) 이후·평가창(07-29~09-23)과 겹치는 미래 데이터로 결정됨 (look-ahead). 상한 파라미터 자체가 없어 as-of-cutoff 재측정이 API로 불가 | `universe.py:87`, `universe.py:146`, `retrain_champion.py:135-137` | 높음(존재) / 중간(방향=liq 유리) |
| B | 한 arm이 부분 앙상블(1~2개 모델)로 조용히 채점될 수 있음 — 로드가 `loaded>0`면 성공, 게이트는 xgb+feature_names만 확인 | `ensemble_model.py:174-186`, `cg57_run.sh:35`, `retrain_champion.py:220-222,245` | 높음(경로) / 조건부(발생) |
| C | 평가 재개가 전날의 낡은 JSON을 재사용 → 시드 짝이 서로 다른 유니버스·서로 다른 창으로 채점됨. 집계기는 measured_at·창을 비교하지 않음 | `cg57_run.sh:34,55`, `champion_robust_eval.py:90`, `universe.py:62`, `champion_seed_family_agg.py:46-47` | 높음(경로) / 중간(발생) |
| D | 판정 게이트는 '+0.02 AND 전 페어 양(+)'이지만 **'5/5'라는 숫자는 코드 어디에도 없다** — n≥3이면 3/3으로 통과, 페어링은 파일 위치 기준(시드 식별자 미사용) | `champion_seed_family_agg.py:94-99,108,120-124` | 높음(코드 사실) |
| E | 동점(Δ=0)은 음수 취급(strict `d>0`), 4자리 반올림 후 비교라 유효 문턱 ≈ +0.0195 | `champion_seed_family_agg.py:98-99,108,110,122` | 낮음 |
| F | 학습 0행 시 `return`(exit **0**) → set -e가 못 잡고, 기존 eval JSON이 있으면 모델 없는 arm으로 '완료' | `retrain_champion.py:336-338`, `cg57_run.sh:34` | 중간 |
| G | 실질 채점 폴드는 2/5(학습 이전 1 + 전방 1) — "5-fold" SE 전제가 과대. Δ 방향 편향은 아님 | `champion_robust_eval.py:152-167,241-251` | 중간 |

검증해 **정상인 것**: 학습 체크포인트·출력 경로의 arm 분리(`cg57_run.sh:39,47`), 체크포인트 재개 키에 종목목록 포함(`feature_pipeline.py:427-430`), 두 arm의 평가 플래그·라벨 정의·HP 동일.

---

## Q1 — 두 arm이 정말 같은 평가 유니버스·창·라벨·HP로 채점되는가

**단일 실행 내에서는 예.** 경로 증명:

- 평가 호출은 arm마다 `--model-dir`/`--out`만 다르고 나머지 플래그는 비트 동일 (`cg57_run.sh:60-65`).
- 유니버스: `--universe training` 분기에서 `select_training_universe(conn, limit=60, min_days=30, seed=args.universe_seed)` 호출 시 **mode를 전달하지 않아** 기본값 `recency` (`champion_robust_eval.py:284-286`). 함수는 모델 dir를 받지 않으므로 두 arm이 seed s마다 같은 60종목. recency 경로는 `random.Random(seed)` 기반 결정적 (`universe.py:162-172`).
- 라벨: 평가는 `_make_labels(rets, "rel")` (`champion_robust_eval.py:385`), 학습은 `_create_labels_relative(horizon=5)` (`retrain_champion.py:169-172`) — 같은 정의(당일 횡단면 중앙값 대비). 횡단면 크기(60 vs 200)만 다르고 이는 양 arm 동일.
- 창: 같은 DB 질의 `trading_dates` + 같은 `--train-start/--train-end` → `_split_oos` 결과 동일 (`champion_robust_eval.py:241-262`).
- HP: `--model-params` 미지정 → 각 모델 기본값, 양 arm 동일 (`cg57_run.sh:44-47`).

**그러나 Δ를 못 믿게 만드는 구멍 3개:**

**[발견 B — 높음(경로)/조건부] 한쪽 arm이 부분 앙상블로 조용히 채점될 수 있다.** `EnsembleModel.load`는 pkl 하나라도 로드되면 `_is_trained = True` (`ensemble_model.py:174-186`) — 모델 개수 검증 없음. 채점기도 `_is_trained`만 확인 (`champion_robust_eval.py:314-318`). 재학습은 모델별 실패를 삼키고 계속 진행 (`retrain_champion.py:220-222`)한 뒤 `feature_names.json`을 어쨌든 기록 (`retrain_champion.py:245`)한다. 즉 xgb만 살아남고 lgb/cat이 실패한 dir도 **`have_model` 게이트(xgb pkl + feature_names만 확인, `cg57_run.sh:35`)를 통과**하고, 재개 시에도 영영 보완되지 않는다. 이 상태에서 rec=3모델 vs liq=1모델이 채점되면 Δ는 "유니버스 차이"가 아니라 "앙상블 구성 차이"를 측정한다. CI는 당연히 통과(모든 경로가 로그 경고만 남김). 훈련 실패가 발생해야 하는 조건부이지만, 실패를 삼키도록 설계된 코드라 이 경로는 상시 열려 있다.

**[발견 C-유니버스 부분 — 중간] 평가 유니버스의 실제 종목 코드가 JSON에 기록되지 않는다.** `universe_info`는 mode/n/seed/overlap 카운트만 저장 (`champion_robust_eval.py:296-298`) → "같은 60종목"이라는 사전등록 주장을 사후 검증할 수단이 아티팩트에 없다. 유니버스는 `datetime.now()` 기반(`universe.py:62,146`)이라 실행일이 바뀌면 같은 seed여도 목록이 통째로 바뀐다(셔플 입력 리스트가 달라짐). Q4의 재개 경로와 결합하면 실질 위험.

**[발견 G — 중간] 실질 채점 폴드는 2/5.** 200거래일(≈2025-12 중순~2026-09-24)을 5청크로 나눈 뒤, 학습구간 [03-26, 06-24]와 조금이라도 겹치는 창을 `_split_oos`가 제외하면 (`champion_robust_eval.py:160-165`) 살아남는 창은 **학습 이전 1개 + 전방 1개 ≈ 2개**. 즉 robust_auc는 폴드 2개의 평균이고, 집계기 docstring이 전제한 SE 계산(단일 유니버스 폴드평균 std 0.0133 기반)보다 노이즈가 크다. 양 arm 대칭이라 Δ 방향 편향은 아니지만, 5시드 설계의 파워 과대평가다. 날짜 경계는 DB 실측으로 확정 가능(아래 반증 1의 fold window 출력으로 확인).

---

## Q2 — `_fetch_liquid`의 look-ahead 판정

**판정: look-ahead 확정(높음). 방향: liq arm에 유리(Δ 상향)로 보되 간접 경로 — 중간. 교락: 분리 불가 — 높음.**

1. **미래 데이터 사용 확정**: `_fetch_liquid`의 SQL은 `md.trade_date >= %s` **상한이 없다** (`universe.py:87`). `select_training_universe`는 `date_from = date_from or _default_date_from()` (`universe.py:146`), `_default_date_from()` = `datetime.now() - 60일` (`universe.py:61-62`), 그리고 재학습 경로 `_select_stocks`는 date_from을 아예 전달하지 않는다 (`retrain_champion.py:135-137`). 따라서 liq arm의 순위 창은 **[2026-08-02, 2026-10-01]** — 학습 종료(06-24)보다 40일 이후에 시작해서 평가창(07-29~09-23)과 ~80% 겹치고, 09-24 이후 데이터까지 포함한다. 이는 학습 구간(03-26~06-24)과 평가창 양쪽 모두에 대한 미래 정보로 학습 **표본 구성**을 결정하는 셈. 사전등록 문구 "두 arm은 유니버스 모드만 다르다" (`cg57_run.sh:10`)는 사실상 위반 — liq arm에만 미래 정보가 있다. (rec arm도 같은 date_from으로 *자격* 필터를 쓰지만, 그 순위는 최신거래일+시드셔플이라 미래 정보가 순위 키로 들어가지 않는다.)

2. **부수 결함**: `window_days: int = 60` 파라미터는 SQL에서 미사용(죽은 인자, `universe.py:65`), 그리고 **상한( date_to ) 파라미터 자체가 없다** — 즉 "학습 종료 시점 기준 유동성"을 이 API로 측정하는 것은 원천적으로 불가능하고, 원시 SQL을 써야 한다. 이 실험을 되돌리려면 코드 수정이 필요하다.

3. **편향 방향(중간)**: 
   - liq 유리 채널: 학습 표본이 **평가 시대의 유동성**으로 정렬된다. 미래(평가창과 겹치는 구간)에 활발했던 종목들의 과거 패턴으로 함수를 적합시키는 "리짐 정렬" 효과는 대조군(무작위 표본)에 없다. 44종목(78% 교집합 기준 이탈분)이 정확히 평가 시대 활발 종목일수록 이 효과는 커진다.
   - 직접 채널은 약함: 평가 유니버스는 무작위 60종목이라 liq-200과의 교집합이 기대값 ≈ 4~5종목 — "평가 승자를 학습 표본에 넣는다"는 기계적 경로는 거의 닫혀 있다. 편향은 학습된 함수의 분포 정렬을 통해서만 들어온다. 그래서 방향은 liq 유리로 보되 크기는 불확실, 확신도 중간.
   - liq 불리 방향 채널은 확인되지 않았다.

4. **'학습 표본 교체' 효과와의 교락(높음)**: 측정되는 Δ = (무작위→유동성 표본 교체 효과) + (미래 유동성 선택 효과). 둘은 얽혀서 분리 불가. 당신이 측정한 156/200(78%)·15/20(75%) 교집합은 **표본 차이(44/200, 5/20)의 지표일 뿐 AUC 왜곡의 상한이 아니다** — 44종목이 Δ에 기여하는 방향·크기는 표본 수에 선형이지 않다. 실무적 귀결: 이 Δ가 +0.02 게이트를 통과해 승격돼도, 배포 시점에 재현되는 유니버스는 06-24 기준(측정본과 22% 다른) 표본이므로 **측정한 개체와 배포되는 개체가 다르다** — 승격 근거로 쓸 수 없는 측정이다.

---

## Q3 — paired Δ 계산·판정 문턱

**문턱은 박혀 있다 — 그러나 '5/5'는 아니다.**

- `--threshold` 기본 0.02 (`champion_seed_family_agg.py:61-62`), 신호 조건 = `delta_mean >= threshold AND pos_frac == 1.0` (`:122`). 즉 "+0.02 AND 전 페어 양수"는 실제 코드에 있다.
- **그러나 5라는 숫자는 코드 어디에도 없다**: 판정불가는 `n < 3`뿐 (`:120-121`)이고 `pos_frac == 1.0`은 "존재하는 페어 전원 양수"를 뜻한다. 시드 5개 중 2개 JSON이 빠지면 `min(len(...))`이 조용히 절단하고 (`:94`), 남은 **3/3 양수로 "신호있음(양(+) 3/3)"이 출력**된다 — 사전등록된 5/5와 다른 판정. (이번 런에서는 set -e + 고정 순서 + 재사용 게이트 때문에 5개가 다 갖춰져 5/5로 동작하지만, 게이트가 이를 강제하지는 않는다.)
- **페어링이 위치 기반이다** (`:94-99`): JSON의 `universe.seed`(로드됨, `:50`)는 페어링에 전혀 사용되지 않는다. 파일 순서가 어긋나면(예: s2, s1 순) 서로 다른 시드 유니버스끼리 Δ를 계산해도 감지하지 않는다. `cg57_run.sh:70-74`는 고정 순서라 이번 런은 안전.
- **동점 처리**: `pos = sum(1 for d in deltas if d > 0)` (`:108`) — 4자리 반올림 후 Δ=0인 시드는 음수 취급 → 4/5 양수 + 동점 1개면 Δmean≥0.02여도 "부호 불일치 → 노이즈"로 강등 (`:125-127`). 60종목 AUC가 4자리에서 정확히 같을 확률은 낮지만 문턱 경계에서 실질 효과.
- **반올림 경계**: Δ는 4자리 반올림된 값끼리 평균·비교 (`:98-99,110`) → 유효 문턱은 +0.02가 아니라 **≈ +0.0195**. 경계 근처 승격 판정이 ±0.0005 흔들린다.
- **폴드 불일치 무검증(낮음~중간)**: `_load`가 fold_means/windows를 들고 오지만 (`:46-47`) 두 arm의 폴드 수·창 날짜가 같은지 아무 데서도 검사하지 않는다. 한 arm의 JSON에 폴드 3개(빈 폴드는 `w_aucs` 없으면 미기록, `champion_robust_eval.py:404-411`)가 있으면 서로 다른 날짜 부분집합의 평균끼리 Δ. 폴드 스킵은 모델과 무관한 경로라 두 arm이 다르게 스킵할 확률은 낮음(모델별 추론 예외 시에만).

---

## Q4 — 재개 경로의 arm 섞임·낡은 JSON 재사용

**학습 쪽은 정상(검증 완료)**: out-dir `cand_cg57_$tag`·체크포인트 `cg57_ck_$tag.pkl`이 arm별로 분리 (`cg57_run.sh:39,47`), 체크포인트 재개 키가 `(stock_codes, start_date, end_date, code_sig)` 전부를 순서까지 비교 (`feature_pipeline.py:427-430`) → 학습 측에서 arm이 섞이거나 다른 구간의 행이 이어붙는 일은 없다. 공유 경로 붕괴(Δ=0 사고)는 여기선 차단돼 있다.

**평가 쪽이 구멍이다:**

**[발견 C — 높음(경로)/중간(발생)] 전날 JSON 재사용.** `have_json`은 **파싱 가능 여부만** 확인한다 (`cg57_run.sh:34,55`) — measured_at·창·유니버스·train 구간·모델 일치를 하나도 검사하지 않는다. 그런데 평가의 두 입력이 모두 실행일에 의존한다:
- 창 끝 = `trade_date <= CURRENT_DATE - 7일` (`champion_robust_eval.py:90`) → 하루 지나면 200거래일 창이 통째로 한 칸 밀리고, 청크 경계·샘플 날짜가 전부 바뀜.
- 유니버스 = `datetime.now() - 60일` (`universe.py:62`) → 같은 seed s여도 목록이 바뀜.

따라서 1일차에 rec-s0·s1만 남기고 죽어서 2일차에 재개하면, rec-s0(1일차 창·유니버스)와 liq-s0(2일차 창·유니버스)이 **서로 다른 스코어보드로 채점**된 채 짝을 이루고, 집계기는 이를 감지할 정보를 모두 들고 있으면서(`:46-47`) 비교하지 않는다. 게다가 JSON에 유니버스 코드가 안 남아(발견 C-유니버스) 사후에 발각할 방법도 없다. 스크립트 주석이 밝히듯 타임아웃-재시도가 상시 운영 모드(`cg57_run.sh:18-19`)라 발생 조건은 일상적이다. 이것이 "실측 사고: 공유 경로 → Δ=0"과 같은 계열의, 이번엔 **Δ가 0이 아니라 임의로 오염되는** 버전이다.

**[발견 F — 중간] 훈련 0행 시 exit 0.** `if df is None or len(df) < 500: logger.error(...); return` (`retrain_champion.py:336-338`) — `return`은 exit code **0**이다. set -e가 못 잡고, 그 arm의 eval JSON이 이전 시도에서 남아 있으면 `have_json`이 재사용 → **모델이 하나도 없는 arm으로 파이프라인이 '완료'를 출력**한다. (JSON이 없으면 eval이 exit 2로 죽어 뒤늦게 발각.)

**[낮음~중간] `have_model`은 pkl 존재만 확인** (`cg57_run.sh:35`) — 이전 시도에서 다른 END/라벨로 훈련된 모델이어도 재사용한다. END가 스크립트에 하드코딩돼 있어 스크립트를 편집한 시도 사이에서만 발생.

---

## 반증 명령 (컨테이너에서 실행 가능, 질문별 한 줄)

**1) Q1 — arm 간 유니버스·창 동일성 + 앙상블 완전성:**
```bash
docker compose exec -T xgboost-ml python -c "
import json
for s in range(5):
 r=json.load(open('app/reports/cg57_rec_s%d.json'%s)); l=json.load(open('app/reports/cg57_liq_s%d.json'%s))
 print('s%d'%s, r['universe']==l['universe'], [f['window'] for f in r['folds']]==[f['window'] for f in l['folds']], len(r['folds']))" && docker compose exec -T xgboost-ml bash -lc 'for t in rec liq; do echo $t pkls=$(ls app/models/cand_cg57_$t/*_model.pkl 2>/dev/null | wc -l); done'
```
기대: 모든 s에서 `True True 2`(폴드 2개 — 발견 G 실증), pkls=3×2. `False` 또는 pkls<3이면 Δ 해석 불가.

**2) Q2 — look-ahead 재현(사용 유니버스 vs 06-24 기준 유니버스 교집합):**
```bash
docker compose exec -T postgres psql -U stock_user -d stock_trading -c "WITH used AS (SELECT stock_code FROM market_data m JOIN stocks s ON s.stock_code=m.stock_code WHERE m.trade_date >= CURRENT_DATE-60 AND s.market IN ('KOSPI','KOSDAQ') AND s.instrument_type='STOCK' GROUP BY 1 HAVING COUNT(*)>=30 ORDER BY AVG(COALESCE(m.trading_value,m.close_price*m.volume)) DESC, stock_code LIMIT 200), cut AS (SELECT stock_code FROM market_data m JOIN stocks s ON s.stock_code=m.stock_code WHERE m.trade_date BETWEEN '2026-04-25' AND '2026-06-24' AND s.market IN ('KOSPI','KOSDAQ') AND s.instrument_type='STOCK' GROUP BY 1 HAVING COUNT(*)>=30 ORDER BY AVG(COALESCE(m.trading_value,m.close_price*m.volume)) DESC, stock_code LIMIT 200) SELECT count(*) AS overlap_200 FROM used JOIN cut USING(stock_code)"
```
(코드의 used 쿼리는 `>=`만 있고 상한이 없는 것과 동일하게 작성 — 156/200 재현 확인용. 이탈 44종목의 Δ 기여를 보려면 `EXCEPT` 목록을 뽑아 그 종목들만 뺀 표본으로 liq arm을 재훈련해 Δ를 비교.)

**3) Q3 — 게이트 실증(동점 1개 → 노이즈 강등 / 3시드 → 신호 통과):**
```bash
python3 -c "import json
[json.dump({'robust_auc':0.50,'folds':[]},open('/tmp/r%d.json'%i,'w')) for i in range(5)]
[json.dump({'robust_auc':0.53 if i<4 else 0.50,'folds':[]},open('/tmp/l%d.json'%i,'w')) for i in range(5)]" && python3 scripts/champion_seed_family_agg.py --agg-out /tmp/cg57t.json --arm rec /tmp/r0.json /tmp/r1.json /tmp/r2.json /tmp/r3.json /tmp/r4.json --arm liq /tmp/l0.json /tmp/l1.json /tmp/l2.json /tmp/l3.json /tmp/l4.json | grep -E 'pos_seeds|verdict' && python3 scripts/champion_seed_family_agg.py --agg-out /tmp/cg57t3.json --arm rec /tmp/r0.json /tmp/r1.json /tmp/r2.json --arm liq /tmp/l0.json /tmp/l1.json /tmp/l2.json | grep verdict
```
기대: 첫 판정은 Δmean=0.024인데 `4/5` → "부호 불일치 → 노이즈"(동점 취급 실증), 두 번째는 `3/3` → "신호있음"(5/5 미강제 실증).

**4) Q4 — 교차일 재개 짝 붕괴 감지(measured_at·창 비교):**
```bash
docker compose exec -T xgboost-ml python -c "
import json, glob
for p in sorted(glob.glob('/app/reports/cg57_*_s*.json')):
 d=json.load(open(p)); print(p.split('/')[-1], d['measured_at'][:10], len(d['folds']), [f['window'] for f in d['folds']])"
```
기대: 모든 파일의 measured_at 날짜가 같고, 같은 s의 rec/liq 창이 동일해야 함. 날짜가 섞였거나 rec_s0과 liq_s0의 window가 다르면 발견 C가 실제로 발화한 것.

---

**결론**: 승격 판정에 쓸 Δ를 못 믿게 만드는 결함은 발견 A(look-ahead — 방향 liq 유리, 교락 분리 불가), B(부분 앙상블 무검증), C(교차일 재개 짝 오염) 세 개가 핵심이고, D(3/3 통과)는 게이트의 보증 문제다. A는 측정 대상 자체를 배포 불가능한 개체로 만들고, C는 재개가 상시인 운영 환경에서 발화 조건이 일상적이다. 파일은 수정하지 않았다.

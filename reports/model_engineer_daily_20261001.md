# 모델엔지니어 일일 사이클 (2026-10-01 22:0x KST) — 실측 기록

## 1. 밤사이 진행 (원장 정합 · 미보고 0)
- **CG56(라벨 다양성 앙상블) 종결 — 축 종결**. 1차 런 rc=124(timeout) → 재개 런 rc=0(19:07).
  in-run 짝 Δ(blend − champ) **+0.0072** (SE 0.0034 · t 2.14 · 양(+) 7/10 · 10시드×3창·16,754행) →
  사전문턱 +0.02 및 양(+) ≥8/10 미충족 = **노이즈**. 결합 평균 0.5246 · champ 0.5174 · cand 0.5217.
  판정: '서로 다른 라벨로 학습한 두 모델의 rank-평균 결합' 축도 닫는다(뒤처진 성분을 평균이 메우지 못한다).
- 원장 76건 · `reported=false` **0건** · `check_undelivered_reports()` **[]** (미전달 없음).

## 2. CG57 착수 (학습 유니버스 정렬 A/B) — 진행 중
- 22:00:38 시작 · 로그 `data/reports/me_cycle/logs/me_cycle_CG57_20261001-220039.log`.
- 진행률(컨테이너 UTC 라벨): 22:04:47 1,000/11,805(8.5%) 4.16 pair/s · 22:07:15 2,000/11,805
  → arm1(recency) 피처빌드 ETA ≈ 28분. 이후 arm2 는 날짜 캐시로 빠르고, 평가 10회×≈5분.
- **프로토콜 정합성(내가 코드로 확인)**: 두 arm 의 평가는 `champion_robust_eval --universe training`
  → `select_training_universe(recency, seed)` 로 **모델과 무관**하게 결정되므로 **두 arm 이 같은 60종목**을
  채점한다(구성 효과 교란 0). 라벨(rel h5)·창(공통 `--train-start/--train-end`)·HP 동일 → Δ 해석 가능.
- **caveat (내가 SQL 로 정량, 신규)**: liq arm 의 유니버스 선정은 `_fetch_liquid(date_from=now−60일)`
  = 2026-08-02~10-01 로, 학습구간(03-26~06-24)·평가창(07-29~09-23)보다 **미래** 정보다. PIT(학습 종료일
  기준 직전 60일) 상위200 과의 교집합 **156/200(78%)** · 상위20 교집합 15/20 · PIT 선정 종목의
  '최근 60일' 순위 중앙 **109/2605** → look-ahead 는 실재하나 크기는 제한적. Δ 보고 시 명시할 것.

## 3. 저녁 파이프라인 승격 실측 (게이트 통과 — 게이트가 권위)
- `reports/ml_result.json`(21:26 KST): candidate ensemble_auc **0.5548** ≥ baseline 0.5513(robust_auc.json)
  and ≥ floor 0.53 · min-improvement 0.0 → `promoted: true`. 백업 `champion_prev_20261001-122646` 생성.
- ⚠ **같은 조건 비교가 아니다**: old = data 2026-06-25~09-23 · n_rows 12,629 · up_rate **0.4296** /
  new = data 2026-07-03~10-01 · n_rows 12,053 · up_rate **0.4597** → 창·표본·양성률이 모두 다르다(양성률 +3.0pp).
- ⚠ **마진 +0.0035 는 잡음 바닥의 1/7**: 이 스택 실측 프로토콜 잡음 = 창 구성 교체 +0.0261 · 유니버스
  시드 교체 0.0211 · 같은 커맨드 5시간 뒤 재측정 +0.0124(CG55). → 개선 증거로 쓸 수 없다.
- ⚠ 이득의 출처도 잡음 패턴: xgb 0.5492 → **0.5236(하락)** · lgb 0.5448 → 0.5663(상승) · cat 0.5185 → 0.5262.
- 학습 분할은 **시간순**(retrain_champion L186 `split = int(len(df)*(1-val_frac))`, df 는 date 정렬) —
  랜덤 누출은 아니다(이 부분은 정상).

## 4. 서빙 정합성 결함 (신규 발견) — 승격이 서빙 프로세스에 반영되지 않는다
- ① `scripts/full_pipeline_dd.sh` L334 `champion_promote` 뒤에 **restart/reload 0건**(grep 확인).
  ② xgboost-ml 서비스는 **HTTP 라우트가 없다**(스케줄러형) — `initialize()` 에서 startup 시 1회
  `self.model.load(MODEL_PATH/xgboost_model.pkl)` 만 수행, 재로드 경로 없음(app/main.py L40-62, L157-163).
- 실측: `champion/xgboost_model.pkl` mtime **2026-10-01 21:26** > 컨테이너 app.main 프로세스 시작
  **10:40** → **지금 서빙되는 모델은 09-23 학습분(auc 0.551318)** 이다. 재기동 전까지 다음 19:00 예측도
  옛 챔피언으로 돈다(예측은 `run_scheduled` 가 매일 19:00 1회).
- 조치(구현·가동): `scripts/model_reload_pending.sh` — 구동기 락(`running_pid()`)과 컨테이너 학습
  프로세스가 **모두 빈 시각**에만, 06:00~07:30 창에서 `docker restart stock_xgboost_ml`.
  현재 대기 중(pid 327258 · 데드라인 10-02 07:30 · 로그 `data/reports/me_cycle/model_reload_20261001.log`).
  **즉시 재기동 금지** — 하면 CG57 이 SIGKILL(137) 로 죽는다(20:00 재생성 사고와 동형).

## 5. CG58 신설·구현 (다음 슬롯) — 게이트 vs 우리 프로토콜 정합성 검정
- `scripts/cg58_run.sh`(신규) + 백로그 CG58(pending·p3·est 70분·metric `champion_seed_family`).
- 두 모델(new=champion · prev=champion_prev_20261001-122646)을 **같은 창**(양쪽 모두 OOS 가 되도록
  `--train-start 2026-06-25 --train-end 2026-10-01`)·**같은 라벨**(abs h1 = 두 모델의 실제 학습 과제)·
  **같은 유니버스 시드 5개**로 채점 → 짝 Δ. 스모크 통과(소형 설정 14초 · JSON 스키마 정상).
- 한계: DB 마지막 거래일이 10-01 이라 남는 OOS 창은 학습구간 **이전**(2025-11~2026-05)뿐이다 —
  암기 제거는 보장하지만 워크포워드 **전방** 검증은 아니다.

## 6. 누수/DQ 신호 (게이트)
- `dq_feature_stock_constant_ratio` **0.3354**(기준선 0.38 · warn 0.35 미만) · `market_level_count` 26 ·
  `coverage_illusion` 36 · `alive_xsec_count` 138 / `feature_count` 199 · `null_ratio_max` 0.999(기지 dead).
- 단일 피처 AUC > 0.75 신호 **없음** → 누수 게이트 통과(리서처 성적표에서도 반려 대상 없음).

## 7. 스코어보드
- 로버스트 **0.5727**(best_exp `Q5s_30_60` — **판정이 '노이즈'인 arm 의 최고값**) vs 등록 기준선 0.5406
  Δ+0.0321 · `no_improve_streak` 9 · 누적 미달 44/47. **헤드라인은 배포 성능이 아니다**(배포 경로 OOS 0.44~0.49).

## 8. 위임 (하이브리드 3안)
- Claude Code review 진행 중: `reports/claude_review_cg57_universe_ab.md`
  (CG57 리그의 ① 같은 창·유니버스·라벨 여부 ② look-ahead 방향·교락 ③ 집계기 paired 정확성 ④ 재개 경로
  arm 혼입). 결과는 파일:라인 근거 확인 후에만 채택하고 확신도 '낮음'은 채택하지 않는다(자기신고 동급).

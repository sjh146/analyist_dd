# 승격 전 라이브 스코어 게이트 — 모델 교체를 '경로가 열려 있는가'로 판정하는 법

상태 보고의 probe #8(라이브 모델 상태)과 짝을 이루는 depth 파일이다. 챔피언 교체·롤백을 보고하거나
판정할 때, AUC 숫자만 인용하지 않고 **배포 스코어 분포**까지 본다.

## 1. 왜 AUC 만으로 부족한가 (기전)

스윙 스크리너는 앙상블 확률을 절대문턱(0.55)과 비교해 `batch_type` 을 `signal`/`raw_fallback` 으로 표시하고,
소비자(`services/strategy-agents/app/signals/signal_validator.py`)는 `raw_fallback` 배치를 **거래 금지**로 거부한다.
모델 교체로 전 시점 확률이 문턱 아래로 내려가면:

- 등록 지표(val split `ensemble_auc`)는 올라가 있고 짝 검정(dry-run)도 통과하며,
- 그런데 전략 경로는 0건이고, 스크리너·소비자 로그는 모두 "정상"을 찍는다(조용한 경로 폐쇄).

재현 규모(같은 유니버스·같은 피처행렬): 교체 모델 `max 0.4733 / 문턱 초과 0건` vs 직전 모델
`max 0.6733 / 9건` — val AUC 차이는 +0.0035 뿐이었다. **AUC 문턱을 조정해도 이 유형은 안 잡힌다.**
경로가 다시 열렸는지 확인하는 판정값은 AUC 가 아니라 `batch_type=signal` 과 문턱 초과 건수다.

## 2. 도구 3종 (저장소에 상주 — 새로 만들지 말고 이것을 쓴다)

| 도구 | 역할 | 기록 |
|---|---|---|
| `scripts/_swing_ensemble_weight_probe.py` | 한 유니버스에서 피처행렬을 **한 번만** 만들어 모델 디렉터리별 배포(균등평균)·검증(가중평균) 분포를 측정 | `PROBE_SUMMARY {…}` |
| `scripts/gate_promote_live_score.py` | 후보 vs 챔피언을 프로브로 채점 → 후보의 문턱 초과 신호 0건이면 승격 차단(rc=2), ≥1건 허용(rc=0), 측정 실패는 AUC 게이트에 위임(rc=3) | `data/reports/promote_live_score_gate.json` |
| `scripts/log_champion_swap.py` | 교체·롤백을 멱등 append 로 기록(mtime 역추적 금지) | `data/reports/champion_swaps.jsonl` |

`scripts/full_pipeline_dd.sh` 의 승격 단계는 **게이트 통과 시에만** `champion_promote` 를 호출하고, 차단이면
`라이브 스코어 게이트: 승격 생략` 한 줄만 남기고 챔피언을 유지한다(**이 줄은 실패가 아니다**).
그 줄이 없는데 챔피언 `auc.txt` 가 그대로면 AUC 게이트가 막은 것이므로 로그의
`candidate ensemble_auc ... (+0.0200 required)` 문구를 인용한다.

## 3. 실행 방법

- 통합: `python3 tools/release_precheck.py --target champion --candidate <dir> --with-probe`
  → 감사 4종 + 피드 계약 + 승격 dry-run + 프로브를 묶어 PASS/BLOCK(큐 위생·미반영 수수료는 경고로 내림).
- 단독(약 90초, 컨테이너 필요):

```bash
docker exec -e PYTHONPATH=/app \
  -e 'PROBE_MODEL_DIRS=[["candidate","/app/app/models/champion_cand"],["champion","/app/app/models/champion"]]' \
  stock_xgboost_ml python /app/scripts/_swing_ensemble_weight_probe.py | grep -a PROBE_SUMMARY
```

- 유니버스는 **스크리너와 동일 필터**(KOSDAQ 300)라 게이트 판정이 곧 피드 조건이다.
- 각 모델은 **자신의 `feature_names.json`** 으로 벡터를 만든다(피처 수가 다른 모델을 같은 벡터로 채점하면 비교 무효).
- 컨테이너 로그가 경고로 도배되므로 파싱은 `PROBE_SUMMARY`/`DEPLOYED` 줄만 grep 한다.

## 4. 롤백 (게이트가 AUC 단조 개선을 요구하므로 음수 허용이 필요하다)

```bash
docker exec -w /app stock_xgboost_ml python -m app.training.champion_promote \
  --candidate app/models/champion_prev_<ts> --champion app/models/champion --min-improvement -0.01
```

- 승격은 현 챔피언을 `champion_prev_<ts>` 로 자동 백업한다 — 롤백 대상은 승격 출력의 `backup_dir` 또는
  디렉터리 목록에서 읽는다(추측 금지).
- 롤백 뒤 **스크리너를 1회 재실행**해 `N candidates above threshold 0.55` 와 `batch_type=signal` 을 확인한다.
- **되돌렸다고 경로가 바로 열리는 것은 아니다**: 피드의 swing 원천(`reports/swing_latest.json`)은 08:30 스윙
  파이프라인만 갱신하므로, 롤백 효과의 첫 확인 지점은 **다음 거래일의 08:40 발행**이다 — **평일과 거래일을
  혼동하지 마라**: 휴장(주말·공휴일·대체휴일)이 끼면 그만큼 밀리므로 `data/krx_holidays.json` 으로 다음 거래일을
  먼저 확정한 뒤 시점을 말한다(그때까지 swing 0건은 정상).

## 5. 앙상블 가중치 미복원 — 배포값 ≠ 검증값 (구조 사실)

`app/models/ensemble_model.py` 의 `load()` 는 `val_weights` 를 복원하지 않는다(가중치 파일 없음) → 추론은
**균등 평균**, `robust_auc.json.ensemble_auc` 는 **AUC 가중 평균**이다. 서브모델 AUC 격차가 크면(max−min > 0.02)
두 값이 벌어지므로 승격·기준선 수치를 인용할 때 **어느 가중인지** 밝히고, 격차는
`scripts/audit_measure.py`(`submodel_auc_spread`)가 매일 기록하게 둔다.

## 6. 보고에서 하지 말 것

- 단일 val split AUC 차이를 개선 근거로 쓰기(노이즈 바닥보다 작다) — 짝·다중창·라이브 스코어 분포를 함께 본다.
- WEIGHTED 값만 보고 "고쳐졌다"고 쓰기 — 배포 경로는 균등 평균이므로 판정값은 DEPLOYED 다.
- `submodel_auc_spread` 경고를 곧바로 모델 성능 문제로 단정하기 — 그건 "검증값과 배포값이 어느 가중인지 다르다"는
  구조 경고이고, 경로가 열려 있는지와는 별개다(`batch_type` 으로 판정한다).

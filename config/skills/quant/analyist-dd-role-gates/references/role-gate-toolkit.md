# 역할 게이트 툴킷 — 명령·판정 표

설계 정본: 레포 `docs/QUANT_ROLE_PLAN_V2.md`. 모든 스크립트는 읽기 전용이고 자기 산출 디렉터리에만 쓴다.

## 1. 감사 4종

| 스크립트 | 보는 것 | 산출 JSON |
|---|---|---|
| `scripts/audit_protocol_lock.py` | pending 항목의 계약 4필드(`counterfactual`·`success`·`est_minutes`·`command`), `est_minutes` vs 커맨드의 `timeout`, **승격 문턱 == 사전등록 값**, 핸드오프 미소비 | `reports/locks/lock_audit_<날짜>.json` |
| `scripts/audit_path_daily.py` | 루프 기동 vs 청산창(fail/warn 구분), 브리지 반쪽세션, `last_cycle` 정지, feed 신선도 | `reports/audit/path_<날짜>.json` |
| `scripts/audit_measure.py` | 라이브 스코어 분포(`batch_type`·0.55 초과 건수), 챔피언 vs 최근 백업, 서브모델 AUC 격차, `per_exp` 오용 | `reports/audit/measure_<날짜>.json` |
| `scripts/audit_safety.py` | 킬스위치 파일, 한도 일치, 창 전 세션 생존(`/health`+`/balance` 둘 다), 프로세스 2개, fees 미반영 | `reports/safety/safety_<날짜>.json` |

종료코드: **0 정상 / 2 이상 / 3 사람 단계 필요**. 경고는 종료코드를 올리지 않고 JSON 에만 남긴다.
회귀는 `python3 scripts/_audit_smoke_test.py`(허용 종료코드·산출 JSON·필수 키)로 확인한다.

## 2. 차단 vs 경고 (릴리스 판정 분류)

`tools/release_precheck.py --target champion|feed|cron|image [--candidate <dir>] [--with-probe]` →
`PASS / PASS_WITH_WARN / BLOCK` (rc 0/2).

- **경고로 내리는 항목(SOFT)**: `live_score_path_closed`·`swing_max_below_threshold`(다음 파이프라인에서 갱신),
  `fees_unbooked`(사람 승인 대기), `lock_fields_missing`·`handoffs_unconsumed`(큐 위생)
- **차단**: `threshold_below_registered_floor`(문턱 불일치), `est_exceeds_timeout`, 킬스위치 존재, 세션 끊김·반쪽
  세션, 프로세스 부재, 피드 계약 위반, **후보의 0.55 초과 신호 0건**

## 3. 라이브 스코어 분포 검사 (모델 교체가 거래 경로를 닫는 사고 방지)

배포 모델을 갈아끼운 뒤 스코어 분포가 절대문턱 아래로 내려가면 소비자가 배치를 전량 거부해 경로가 조용히 닫힌다.

1. **산출물 기준(빠름)**: `data/reports/{close,swing}_candidates_*.csv`(JSON 형식)에서 `batch_type`·`up`·`total`·`auc`.
   `batch_type != signal` 이면 경로가 닫힌 것. 단 이 값은 **마지막 파이프라인 실행 시점의 모델** 기준이라
   모델만 교체하고 파이프라인을 안 돌렸으면 stale 이다 → 후보를 직접 채점한다.
2. **후보 기준(약 90초)**: 컨테이너에서 프로브를 돌려 같은 유니버스·같은 피처행렬 위에서 모델 디렉터리별
   배포(균등평균)·검증(가중평균) 분포를 비교.

```
docker exec -e PYTHONPATH=/app \
  -e PROBE_MODEL_DIRS='[["candidate","/app/app/models/champion_cand"],["champion","/app/app/models/champion"]]' \
  stock_xgboost_ml python /app/scripts/_swing_ensemble_weight_probe.py
```

   판정 줄은 `PROBE_SUMMARY {"candidate": {"deployed_max": .., "deployed_gt_conf": ..}, ...}` 이다.
   `deployed_gt_conf == 0` 이면 승격 금지(사전검사가 이 값을 읽어 BLOCK).
   로그에 neo4j 경고가 초당 여러 줄 찍히므로 `PROBE_SUMMARY`·`===`·`DEPLOYED`·`WEIGHTED` 줄만 골라 읽는다.

## 4. 릴리스 도구

```
tools/release_champion.sh --candidate app/models/champion_cand                 # 리허설(동결→백업→dry-run→사전검사)
tools/release_champion.sh --candidate <후보> --with-probe                      # + 라이브 스코어 프로브(90초)
tools/release_champion.sh --candidate <후보> --apply                           # 실제 적용(대표 승인 후)
tools/release_champion.sh --candidate <백업dir> --min-improvement -0.01 --apply # 롤백
```

- **`--apply` 없이는 아무것도 바꾸지 않는다**(리허설 기본). 적용 경로는 즉시/15분 검증 후 롤백 명령을 문서에 남긴다.
- 게이트가 AUC 단조 개선을 요구하므로 **롤백은 `--min-improvement` 를 음수로 열어야** 통과한다(`--dry-run` 선확인).
- 문서는 `docs/releases/<날짜>-champion-<note>.md`. 리허설 문서는 지우고 정본만 남긴다.
- 사전검사 `BLOCK` 이면 릴리스 문서를 만들지 않고 rc=2 로 중단한다 — 중단 자체가 증거다.
- 실제 승격은 `champion_promote` 가 `champion_prev_<ts>` 를 자동 백업한다 — 롤백 대상 경로를 그 출력에서 읽어 적는다.

## 5. 크론 래퍼 규약 (no_agent 스크립트 틱)

`~/.hermes/scripts/<이름>_tick.sh` + `cronjob_manage(no_agent=true, script=..., deliver='platform:chat_id')`.

```bash
STATE=/home/jhshi/.hermes/state/<이름>.last
mkdir -p "$(dirname "$STATE")"
out=$(/usr/bin/python3 /home/jhshi/analyist_dd/scripts/audit_x.py 2>&1); rc=$?
if [ "$rc" -eq 0 ]; then rm -f "$STATE"; exit 0; fi     # 정상 복귀 시 상태 초기화
prev=$(cat "$STATE" 2>/dev/null)
if [ "$out" != "$prev" ]; then echo "[헤더 $(date '+%m-%d %H:%M')]"; echo "$out"; printf '%s' "$out" > "$STATE"; fi
exit 0     # 항상 0 — 2/3 을 그대로 두면 크론이 '잡 실패'로 오해해 별도 오류 알림을 보낸다
```

- 헤더의 시각을 **비교 대상 텍스트 밖**에 둔다(안에 넣으면 매 틱 달라져 억제가 무력화된다).
- 검증은 2회 연속 실행: 1회차 출력 · 2회차 침묵이면 정상이다.

## 6. 배선된 틱 (평일)

08:25 안전(개장 전) · 14:45 안전(close 창 전) · 15:50 감사(경로+측정) · 15:55 락·큐 심사.
15:35 장마감 역할 틱(리서처→엔지니어→트레이더)과 16:00 엔지니어 야간 시작 사이에 있으므로 충돌하지 않는다.
휴장일에는 경보가 없어 조용한 것이 정상이다(스케줄은 공휴일을 모른다).

## 7. BLOCK 사유를 읽는 순서 (실측 예)

노이즈급 개선(+0.0035)인데 라이브 신호가 0건인 후보의 BLOCK 사유는 네 줄로 나온다:
① 라이브 스코어 프로브 "후보의 0.55 초과 신호 0건" ② 승격 dry-run `kept_incumbent`(문턱 게이트)
③ AUC 델타 < 사전등록 문턱 ④ 서브모델 AUC 격차 > 0.02(배포 균등평균 ≠ 검증 가중평균).
겹칠 때는 **가장 구체적인 ①을 판정 근거로 인용**하고 나머지는 보조로 쓴다.

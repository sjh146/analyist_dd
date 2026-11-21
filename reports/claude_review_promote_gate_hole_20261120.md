# 검토 보고 — 승격 게이트 구멍 검증 (읽기 전용, 2026-11-20)

## (a) 주장 검증 — 모두 확인됨

**A1. `--require-robust` 없이 호출 시 비교 지표는 단일분할 `ensemble_auc`다 — 확인 (확신도: 높음)**
- `services/xgboost-ml/app/training/champion_promote.py:318` — `cand_auc = float(meta.get("auc_mean") or meta.get("ensemble_auc") or 0.0)`. 오늘 후보의 `training-result-20261120-114452.json`에는 `auc_mean` 키가 없으므로 `ensemble_auc 0.6175`가 그대로 쓰인다.
- `robust_oos.json`의 `robust_auc 0.4376`은 `_read_robust_oos()`(`:103-129`)로 읽혀 `result["candidate_robust_oos"]`(`:322-323`)에 **관측용으로만** 실리고, `require_robust`/`PROMOTE_REQUIRE_ROBUST=1`(`:324`)이 아니면 비교에 들어가지 않는다(`:325-333`).

**A2. `--require-robust`만 켜고 `--min-robust-auc`를 안 주면 `min_auc 0.53` 기본값이 막는다 — 확인 (확신도: 높음)**
- `:333`에서 `cand_auc = robust_oos["value"] = 0.4376`으로 바뀐 뒤, `:458-462`의 `cand_auc < min_auc` 검사가 `kept_incumbent`(reason `candidate robust_auc 0.4376 < floor 0.53`)로 거부한다. 파이프라인도 `--min-auc 0.53`을 명시적으로 넘긴다(`scripts/full_pipeline_dd.sh:431`). (`:473`의 기준선+개선폭 검사도 0.4376 < 0.5513으로 거부하지만, 하한 검사가 먼저 발화한다.)
- `--min-robust-auc 0.5`를 주면 `:379-393`의 acceptance 하한이 그보다 먼저 발화한다 — 님이 관측한 reason 문자열("다중 폴드 OOS AUC 하한 미달: 후보 0.4376 < 0.5000")과 정확히 일치.

**A3. `promote_flags`는 하한 플래그를 내보내지 않는다 — 확인 (확신도: 높음)**
- `config/objective.json:34` `acceptance.min_robust_auc: 0.5` 선언. `:56-64` `gates`: `promote_require_expectancy: true`(`:58`), `promote_require_robust: false`(`:59`).
- `scripts/objective.py:117-124` `promote_flags()`: `--require-expectancy` 계열 4종(`:118-121`)과 `promote_require_robust`일 때만 `--require-robust`(`:122-123`)를 내보낸다. `--min-robust-auc`/`--min-expectancy-t`는 어디에도 없다.
- 부수 확인: `acceptance`에는 `min_expectancy_t` 키 자체가 없다. t 하한은 `champion_promote.py:560`의 CLI 기본값 `None`으로만 존재한다.

## (b) 승격에 도달하는 경로

**B1. 야간 파이프라인 — 구멍의 실제 경로 (확신도: 높음)**
- 스케줄 실존: `/etc/cron.d/analyist_dd:9` `0 20 * * 1-5 root /home/jhshi/cron/evening_pipeline.sh`. `evening_pipeline.sh`의 `cd /home/dduckbeagy/analyist_dd`는 `/home/dduckbeagy/analyist_dd → /home/jhshi/analyist_dd` 심링크(2026-11-17 00:52)로 현재 리포를 가리킨다. 오늘 20:00 실행 실측: `reports/full_pipeline_dd_20261120_2000.log` 존재, 21:16 종료.
- 흐름: `full_pipeline_dd.sh:425` `gate_promote_live_score.py` → 통과 시 `:427` `GATE_FLAGS=$(objective.py promote-flags)` → `:429-434` `champion_promote`(**`--dry-run` 없음**, `--min-auc 0.53 --min-improvement 0.02` + `$GATE_FLAGS`).
- **오늘 밤 실제 판정**: 로그에 `라이브 스코어 게이트: 승격 생략(후보가 문턱 초과 신호 0건 — MT116 유형)` — 토큰(`services/xgboost-ml/app/models/promote_live_score_gate.json`)이 `status=blocked, candidate_signals=0, ts=21:04:31`이어서 **오늘은 신호 게이트가 우연히 막았다**. robust 하한은 어디서도 조회되지 않았다.
- **구조적으로는 도달 가능**: 후보가 0.55 초과 신호를 1건 이상 내고 돈 게이트를 통과하면(오늘 후보: expectancy +0.0905 > 0, 세션 113 ≥ 40, 거래 1771 ≥ 30, halves both_positive, 챔피언 -0.182 대비 +0.1%p 초과 — 전부 통과), `robust_auc 0.4376 < 0.5`는 **아무 검사도 못 받고** `promoted`가 된다(`:495-524`).

**B2. 수동 릴리스 경로 — 돈 게이트·robust 하한 둘 다 없음 (확신도: 높음)**
- `tools/release_champion.sh:49-51`(dry-run)과 `:86-88`(`--apply`) 둘 다 `--min-improvement`만 넘기고 `--require-expectancy`·`--require-robust` 없음. `tools/release_precheck.py:112-124`의 dry-run 인용도 동일(`:114-117`). 프리체크는 감사 스크립트 4종 + 선택적 프로브(`:200-208`)만 보고, robust_auc·expectancy는 검사 항목에 없다. 야간 토큰이 "passed"면 6시간 TTL 안에 `--apply`가 증거 없이 승격할 수 있다.

**B3. 레거시/수동 드라이버 (확신도: 중간 — 현재 스케줄 없음)**
- `scripts/extra_experiments.py:476-481` `run_promote_h`: `--min-auc 0.60`만, 게이트 없음. `scripts/overnight_ml_loop.py:528-533` 동일. cron.d·jhshi crontab·`/home/jhshi/cron/*.sh` 어디에도 이 둘을 거는 항목 없음 → 현재는 수동 경로. `job_runner`(services/job-runner)는 swing/close/backtest/factor 잡만 있고 champion_promote 호출 없음(반증됨).
- `scripts/model_engineer_cycle.py`: `champion_promote`를 **dry-run metric 파서로만** 사용(`:1963`, `:2304`), 자체적으로 실승격 명령을 만들지 않음. 다만 백로그에 등록된 임의 명령이 비-dry-run 승격을 실행할 수 있고, 그 경우 `champion_promote.py:489`의 토큰 검사만 남는다.

## (c) 반증 시도 — 미처 확인 못 했을 가드 검사 결과

| 후보 가드 | 결과 | 근거 |
|---|---|---|
| live_score_gate 강제 | **활성, 하지만 직교 게이트** — 신호 수(≥1)만 보고 robust AUC는 안 본다. 오늘 후보는 우연히 이 게이트로 막혔을 뿐, robust 하한 구멍을 닫지 못함 | `champion_promote.py:489-493` · `gate_promote_live_score.py:92-103` |
| `PROMOTE_REQUIRE_ROBUST` 환경변수 | **설정 없음(꺼짐)** — docker-compose·.env·Dockerfile·crontab·cron.d 전수 grep 0건 | `champion_promote.py:324` 기본 "0" |
| protected_paths | 에이전트의 **파일 편집**만 막는 목록 — 런타임 승격과 무관 | `scripts/protected_paths.py:18-19` |
| 백업 | 활성 — 승격 시 `champion_prev_<ts>` 3개 유지. 단, 사후 복구용이지 사전 차단 아님 | `champion_promote.py:242-256` |
| 자동 롤백 | **실제로는 안 돈다** — `objective.json:60` `auto_rollback: true`인데 `champion_rollback_monitor.py`를 거는 스케줄이 cron.d/crontab/cron 디렉터리 어디에도 없음(반증 실패) | `champion_rollback_monitor.py:182-202` |
| 수동 롤백 경로 | **문서대로 하면 실패한다** — `release_champion.sh:9,106`의 롤백 명령은 `champion_prev_<ts>`를 후보로 승격시키는데, `champion_promote.py:79-83`의 basename 일치 검사가 기존 토큰(`champion_cand`)과 불일치 → `blocked_live_score`. 자동 모니터만 `PROMOTE_LIVE_SCORE_ENFORCE=0`로 우회(`champion_rollback_monitor.py:160`) | `champion_promote.py:67-83` |

즉, 남은 가드 중 실제로 이 구멍을 막는 것은 **없다**. 오늘의 차단은 robust 하한이 아니라 신호 수 게이트의 우연이다.

## (d) 결론: **구멍 있음** — 단, "돈 게이트 통과 + robust 0.4376" 조합이 실제로 승격된 사례는 아직 없다(오늘은 신호 게이트가 우연 차단). 구조적 경로는 열려 있다.

**추가로 발견한 결함**(CI 통과·운영 폭발 계열, 스타일 아님):

1. **t 유의성 미검사 비대칭 (확신도: 높음)** — `_expectancy_verdict`(`champion_promote.py:158-196`)는 t를 `:181`에서 기록만 하고 검사 조건이 없다. 오늘 후보 t=0.38(95% CI 0 포함)로 "돈 증거 통과". `--min-expectancy-t` 하한은 존재하나(`:394-409`) 기본 `None`(`:560`)이고 아무 호출자도 넘기지 않는다. 코드 주석(`:370-378`)도 "닫을 때는 t≥2, 열 때는 안 씀" 비대칭을 자인.
2. **돈 게이트 기준선 46일 경과 (확신도: 중간)** — 챔피언 `robust_oos.json` `created_at 2026-10-05`, expectancy -0.182. 월요일만 갱신(`full_pipeline_dd.sh:419-428`)인데 10-05 이후 미갱신. `objective.json:39` `max_evidence_age_days: 14`는 롤백 판정용으로만 쓰이고 승격 돈 게이트는 낡은 기준선을 그대로 비교한다(`:341`). -0.182 기준선 덕에 +0.1%p 개선 문턱이 사실상 무의미.
3. **`GATE_FLAGS` 페일오픈 (확신도: 중간)** — `full_pipeline_dd.sh:3` `set +e` + `:427` 명령 치환. `objective.json`이 검증 실패(`scripts/objective.py:156-158` rc=2)하면 stdout이 비어 `GATE_FLAGS=""`이 되고 **돈 게이트 없이** AUC 게이트만으로 승격이 진행된다. 실패해도 파이프라인은 멈추지 않는다.
4. **수동 릴리스 경로가 증거 게이트 전면 우회 (확신도: 높음)** — B2 참조. `release_champion.sh --apply`는 돈 증거 없이 ensemble_auc 0.53 하한만으로 승격 가능(토큰이 passed인 경우).
5. **문서화된 롤백 명령이 토큰에 막힘 (확신도: 높음)** — (c) 표 참조. `release_champion.sh:9,106` 경로로는 롤백이 실행되지 않는다.

**최소 수정안** (요구된 형식):

- **파일/행**: `scripts/objective.py:117-124` `promote_flags()`에 두 줄 추가 —
  ```python
  if acc.get("min_robust_auc") is not None:
      flags.append(f"--min-robust-auc {acc['min_robust_auc']}")
  ```
  (t 하한까지 닫으려면 `acceptance`에 `min_expectancy_t` 추가 + 같은 곳에서 `--min-expectancy-t` 전달 — 단 이건 acceptance 스키마 변경이라 별건.)
- **함께 고쳐야 함**: `tests/test_objective.py:71-72`의 플래그 허용 목록에 `--min-robust-auc` 추가(안 고치면 CI 실패). `full_pipeline_dd.sh`는 GATE_FLAGS를 그대로 소비하므로 무변경.
- **승인 종류**: `champion_promote.py:370-378` 주석이 명시하듯 "게이트 문턱 변경 = 리뷰보드 승인 대상, 코드는 준비·플래그는 OFF" 상태의 정책 결정을 뒤집는 것이므로 **리뷰보드(quant-review-board) 승인** 필요. (선언된 acceptance 값을 집행하는 배선 수리라는 해석도 가능하나, 보수적으로 리뷰보드.)
- (권장 병행) 페일오픈 봉쇄: `full_pipeline_dd.sh:427`에서 `GATE_FLAGS`가 빈 문자열이면 승격 생략 또는 최소한 `--require-expectancy` 강제. 롤백 경로 수리: `release_champion.sh` 롤백 명령에 `PROMOTE_LIVE_SCORE_ENFORCE=0`(모니터와 동일 방식) 또는 토큰 재발급 절차 문서화. 이 둘은 게이트 강화·절차 수리이므로 동일하게 리뷰보드 성격의 승인 권장.

**보고만 했습니다 — 파일 수정·코드 실행·모델 승격 없음.**

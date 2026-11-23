# 자율 루프의 기계 가독 목표·게이트·되돌림 — 구체 스키마와 명령

목표·봉투·게이트를 한 파일에 고정하고, 게이트 임계값은 그 파일에서 생성해 파이프라인·승격·감시가 같은 값을 보게 만든다.

## 1. 파일과 역할

| 파일 | 내용 | 소비자 |
|---|---|---|
| `config/objective.json` | `goal`(primary_metric·protocol·acceptance) · `envelope` · `gates` · `autonomy`(T0/T1/T2) | 파이프라인·게이트·계기판 |
| `scripts/objective.py` | `check`(스키마) · `envelope-drift`(트레이더 설정과 대조) · `promote-flags`(정책→CLI 인자) · `gate set/get` | 파이프라인·운영자 |
| `<모델디렉토리>/robust_oos.json` | 증거: `robust_auc`·`expectancy_pct`·`expectancy_t`·`n_sessions`·`n_trades`·`halves{front_pct,back_pct,stable}`·`protocol`·`created_at` | 승격 게이트·되돌림 감시 |
| `data/state/objective_state.json` | 오늘의 목표값·돈(실현손익·자본)·봉투 위반·1순위 손실원·데이터 신선도 | 루프·경보·보고 |
| `data/state/rollback_monitor.json` | 되돌림 판정(`verdict`·경과 세션·증거·degrade) | 감시 틱·사람 |

`config/objective.json` 의 핵심 키:

```json
{"goal": {"primary_metric": "fillable_oos_net_expectancy_pct",
          "protocol": {"folds": 5, "topk": 3, "max_day_chg_pct": 25.0,
                       "fee_buy": 0.00015, "fee_sell": 0.00015, "tax_sell": 0.0018,
                       "exit": "next_open"},
          "acceptance": {"primary": "expectancy_pct > 0", "min_expectancy_pct": 0.0,
                         "min_improvement_pct_over_incumbent": 0.1,
                         "min_sample_sessions": 40, "min_sample_trades": 30,
                         "split_sample_required": "both_halves_positive"},
          "rollback": {"monitor_sessions": 5, "degrade_pct": -0.2, "max_evidence_age_days": 14}},
 "envelope": {"max_entries_per_day": 3, "max_position_pct": 0.1, "max_invested_pct": 0.3,
              "daily_loss_limit_pct": 0.03},
 "gates": {"promote_require_expectancy": true, "promote_require_robust": false,
           "auto_rollback": false}}
```

- 금액·비중 필드(`envelope`)는 **트레이더 설정의 사본**이다. 값을 고칠 때는 트레이더 설정을 보고 고치고
  `python3 scripts/objective.py envelope-drift` 로 대조한다(불일치면 rc=2).
- `promote-flags` 는 게이트가 **실제로 받는 인자만** 내보낸다(모르는 플래그를 내보내면 파이프라인이 죽는다).

## 2. 승격 게이트 (돈 증거)

```bash
# 후보 증거 생성(컨테이너, 전 파트: 라이브신호·다중폴드 AUC·순기대/분할표본)
docker exec -w /app -e PYTHONPATH=/app stock_xgboost_ml \
  python /app/scripts/model_metric_protocol_audit.py \
  --models '[["cand", "/app/app/models/champion_cand"]]' \
  --out /app/app/reports/oos_evidence_cand.json \
  --robust-oos-out /app/app/models/champion_cand/robust_oos.json

# 게이트(플래그는 objective.json 에서 생성)
FLAGS="$(python3 scripts/objective.py promote-flags)"
docker exec stock_xgboost_ml python -m app.training.champion_promote \
  --candidate app/models/champion_cand --champion app/models/champion \
  --min-auc 0.53 --min-improvement 0.02 $FLAGS
```

거부 사유는 그대로 원장·로그에 남는다(사유별로 다르다): `돈 증거 없음` · `순기대 ≤ 문턱` · `세션 부족` ·
`표본 부족` · `분할표본 불안정` · `챔피언 기준선 없음(같은 프로토콜로 먼저 측정)` · `챔피언 대비 개선 부족`.
라이브 스코어 토큰(절대문턱 경로가 닫히는 사고 방지)은 별개 축이며 항상 적용된다.
비용 감각: 피처 빌드 ≈13분 + 파트당 수 분(전 파트 ≈15~20분). 챔피언 기준선은 주 1회 같은 명령으로 갱신한다.

## 3. 되돌림(rollback) 감시

```bash
python3 scripts/champion_rollback_monitor.py --check --write          # 판정만(상태파일 기록)
python3 scripts/champion_rollback_monitor.py --check --write --execute # auto_rollback 게이트가 켜져 있으면 원복까지
```

판정은 스왑 로그(`data/reports/champion_swaps.jsonl`) 마지막 항목의 `prev_dir`(직전 챔피언)과 현재 챔피언의
**같은 프로토콜 증거**를 비교해 내린다: `too_early`(경과 세션 < monitor_sessions) / `no_evidence` /
`stale_evidence`(증거 나이 > max_evidence_age_days) / `ok` / `watch`(조금 나쁨) / `rollback`(degrade 이하).
**판정 불가 상태(no_evidence·stale)로는 절대 원복하지 않는다** — 근거 없이 챔피언을 흔들면 진동한다.
원복은 '이전에 실제로 라이브였던' 모델로 되돌리는 것이므로 새 모델 유지보다 위험이 낮다는 논거로 자율 실행을 허용한다
(그래도 스왑 로그·경보에 남긴다).

## 4. 일일 상태 틱(무인, 침묵 기본)

```bash
python3 scripts/money_scoreboard.py --days 30        # 돈 계기판(실현손익·자본사용·게이트 기회비용·1순위 손실원)
python3 scripts/objective_state.py --write            # 목표 상태 한 장(봉투 위반이면 rc=2)
```

틱 규약: 정상이면 아무 출력도 내지 않는다(no_agent 틱은 빈 stdout 이면 전송되지 않는다). 봉투 위반·판정
이상이 있을 때만 경보하고, **같은 경보 문구는 상태파일로 중복 전송을 막는다**(매일 같은 경고가 쌓이면
사람이 경보를 무시하게 된다).

## 5. T1 자율 탐색 — 재학습 없는 파라미터 격자

탐색은 **재학습이 필요 없는 파라미터**(선별 깊이 top-K · 체결성 필터 · 청산 규칙)부터 돈 지표로 평가한다.
호스트 측에서 기존 후보·점수만으로 돌아 싸고, 되돌리기 쉽고, 무엇보다 돈으로 측정된다. 모델·라벨 변경
(재학습)은 엔지니어 틱 소관이다.

```bash
python3 scripts/experiment_runner.py --run    # 격자 평가 → data/reports/experiments/<날짜>.json + 원장 experiments.jsonl
python3 scripts/experiment_runner.py --list   # 통과 조합 · registry(같은 격자 재평가 방지)
```

불변 규칙 4개:

1. **격자는 코드에 사전등록**(`GRID`)하고 수용 기준은 `objective.json` 에서 읽는다 — 사후에 축을 추가하면 p-hacking 이다.
2. **체결성은 축이 아니라 전제조건**이다. 필터 없는 조합은 '살 수 없는 상한가'를 포함해 두 자리수 착시를 만든다
   (실측: 필터 없이 +5.23%p → cap 25% 를 걸면 −0.67%p). 격자에서 빼고, 판정에도 가드를 둔다(2중 방어).
3. **비교 기준선은 같은 데이터 안의 현행 조합**(`INCUMBENT` topk·cap·exit)이다. 외부에서 잰 챔피언 기준선과
   섞으면 부호가 뒤집힌다.
4. **통과 조합은 자동 반영하지 않는다** — 선별·청산 변경은 실주문 경로라 `protected` 제안으로 큐에 넣고 사람이
   판단한다. 통과가 없으면(=대부분의 날) 조용히 기록만 남긴다.

실측(2026-10-04, 현 신호 · 87세션 · 48조합): **전부 탈락, 최고 −0.32%p(t=−1.71)**. 탐색을 자동화해도 양(+) 기대는
저절로 생기지 않는다 — 루프가 하는 일은 '기회를 놓치지 않고 평가하는 것'이고, 엣지 발견은 별개 문제다.
이 사실을 보고에 명시하라(자율 루프가 있으니 수익이 난다'는 착각을 막는다).

## 6. 속도·비용 함정

- 피처 빌드가 대부분의 시간이다(≈13분). 여러 모델을 같은 런에 넣으면 빌드는 1회만 지불된다 — 한 모델씩
  여러 번 돌리는 것은 같은 비용을 반복해 낸다.
- 스코어보드·감시 틱은 **파일 읽기**만 해야 싸다(측정을 틱 안에서 돌리면 야간 작업과 CPU 를 다툰다).
- 증거 파일은 오래되면 판정을 막는다(노화 검사). 재측정을 자동화할 때는 주기를 정해 파이프라인에 얹는다.

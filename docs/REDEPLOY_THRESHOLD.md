# 정책 — 재배포 문턱 (측정된 양(+) 엣지 없이는 배포하지 않는다)

확정 2026-11-19 (사용자 지시 "1"). 근거: 대표 결정 C-1 채택.

## 규칙 (양보 불가)

새 전략·파라미터·모델을 **실계좌에 배포(promote)** 하려면 아래 **두 조건을 동시에** 만족해야 한다.

**A. 라이브 실측 (현행 규칙 체제 내)**
- `--since` 이후 **청산 표본 ≥ 20건**
- 진입 원금 대비 **순수익 > 0** (수수료 포함, 저널 `pnl` 합)

**B. 챔피언 OOS**
- 세션 기준 **순기대 > 0** 이고 **t ≥ 1**
- 세션 지표 산출물이 없으면 **측정 불가 = 배포 금지**(추정으로 통과시키지 않는다)

둘 중 하나라도 미달이면: **현행 규칙 유지**(매매 자체는 계속), 신규 배포만 금지.

## 왜

- 라이브 누적(2026-11-08~11-18, 15건): **−39,829.5원**(진입원금 1,044,598원 대비 −3.81%/거래, 승률 15.4%).
- 챔피언 OOS: **−0.875%/세션(t −2.51, 25세션, OOS-only)** ← 게이트가 쓰는 값.
  (정책 초안이 인용한 −0.182%(t −0.81, 87세션)은 학습구간이 섞인 값이라 채택하지 않는다.
   출처: `services/xgboost-ml/reports/overnight/expectancy_is_oos_split.json`,
   정본: `data/reports/champion_oos_sessions.json`) 최신 walk-forward AUC 0.5039.
- 즉 **어느 축도 (+)가 아니므로**, 그 상태의 추가 배포는 기대값이 측정되지 않은 베팅이다.
- 2026-11-19은 새 규칙(문턱 4 · 1%/3% · 90분 · 상승률 대역) 첫날이고 3건 +4,688원(수수료 전)이다 —
  **표본 3건으로 규칙을 또 바꾸면** 같은 실수를 반복한다(규칙 변경 → 표본 리셋 → 판정 불가의 악순환).

## 집행 수단 — `scripts/redeploy_gate.py`

```
python3 scripts/redeploy_gate.py [--since 2026-11-19] [--min-trades 20]
        [--oos-file data/reports/oos_accrual.jsonl] [--session-file PATH]
        [--json-out data/reports/redeploy_gate.json] [--quiet]
```
- 읽기 전용: 저널은 `/tmp` 복사 후 `mode=ro`(기존 관례), OOS·세션 파일은 read-only.
- 출력:
  ```json
  {"since":"2026-11-19","live":{"n":3,"net_pnl":4688.0,"win_pct":66.7,"pass":false},
   "oos":{"session_expectancy_pct":null,"t_stat":null,"source":null,"pass":false,"reason":"측정 불가"},
   "verdict":"BLOCK","reasons":["표본 3 < 20","OOS 세션 지표 없음"]}
  ```
- 종료 코드: **0 = PASS(배포 가능) · 1 = BLOCK · 2 = 입력 오류**.
- 결측은 null + 사유. `0` 으로 메우지 않는다.
- 멱등(실행 시각 미기록), `--quiet` 는 콘솔 억제.

## 집행 지점 (사람 절차)

배포 전 아래 작업은 **반드시 이 게이트를 먼저 통과**해야 한다(PR/커밋 메시지에 `redeploy_gate.json` verdict 첨부):
1. `config.py`/`profiles.py` 의 주문 파라미터 변경(문턱·손익선·보유한도)
2. 피드 발행 로직 변경(`feed_export.py`, 스크리너 점수·필터)
3. 챔피언 모델 promote
4. 신규 전략 프로파일 추가

> 게이트는 **자동 차단 장치가 아니라 증거 요구 장치**다. 코드가 스스로 배포를 막지는 않는다
> (실주문 경로에 자동 잠금을 넣으면 정상 운영까지 멈춘다). 대신 배포 기록에 판정 JSON 을 남긴다.

## 게이트 실측 판정 (2026-11-19)

```
live: n=3, net +4,688원, 승률 66.7%      → 표본 < 20 (미달)
oos : 순기대 -0.875%/세션, t -2.51 (n=25) → 음(-) · t<1 (미달)
verdict = BLOCK
```

## 테스트 — `tests/test_redeploy_gate.py` (6개) + `tests/test_champion_oos_sessions.py` (2개)

① 표본 < 20 → BLOCK ② 표본 ≥ 20 & 순수익 > 0 → live pass ③ OOS 파일 없음 → oos pass=false·사유
④ OOS 세션지표 >0 & t≥1 → PASS ⑤ t<1 → BLOCK ⑥ 저널 없음 → exit 2(입력 오류)

## 금지

- 버전관리 금지. 저널·OOS 파일 쓰기 금지. 기존 스크립트 수정 금지.
- 게이트를 **실주문 경로에 자동 삽입하지 말 것**(운영 정지 위험) — 문서·기록 장치로만.

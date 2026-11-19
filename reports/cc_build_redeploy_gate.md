## 구현 완료 보고

**신규 파일 2개** (기존 파일 수정 0건, git 명령 0회, 실주문 경로에는 저널 복사 읽기 외 무접근)

### 1. `scripts/redeploy_gate.py` — 재배포 문턱 게이트
- **계약 준수**: `docs/REDEPLOY_THRESHOLD.md` 그대로 — A(라이브: `--since` 이후 청산 표본 ≥ 20 & `pnl` 합 > 0) ∧ B(챔피언 OOS: 세션 순기대 > 0 & t ≥ 1, 산출물 없으면 측정 불가=배포 금지) → `PASS`/`BLOCK`, 종료 코드 0/1/2.
- **① WHY + 실측 근거**: 파일 상단 docstring에 정책 근거(라이브 −39,829.5원/15건, OOS 세션 −0.182%·t −0.81)와 **실제 실행한 조회 명령 원문** 수록 — `/tmp` 복사 후 `mode=ro`로 `(3, 4687.9996…)`, 승 2건 → 정책 예시 `net_pnl 4688.0 / win_pct 66.7`과 일치함을 확인해 기록.
- **읽기 관례**: 저널은 `/tmp` 복사(+`-wal`/`-shm`) 후 `file:…?mode=ro`(trader_cycle과 동일, drvfs+WAL 함정 회피). 청산 표본 정의도 `_REAL_CLOSED`와 동일(`unfilled:` 제외).
- **③ 멱등**: 산출물에 실행 시각 없음, 원자적 덮어쓰기 — 재실행 후 md5 동일 확인.
- **④ 자기신고 3분리**: `dq_claim.record_claim` 재사용 — `source_rows`(저널 전체 18행) / `claimed_rows`(청산-since 3행) / `persisted_rows`(저장된 live.n 3, 기록 후 재읽어 대조). 실측 기록 확인: `redeploy_gate|redeploy_gate|18|3|3|verdict=BLOCK …` (gap 0). 실패해도 판정 안 깨짐, 테스트는 `REDEPLOY_GATE_NO_CLAIM=1`.

### 2. `tests/test_redeploy_gate.py` — 스펙의 6개 시나리오
**검증 실행 결과 — `pytest tests/test_redeploy_gate.py -v` → 6 passed** (① 표본<20→BLOCK, 정책 예시 수치 그대로 고정 ② 20건+순수익→live pass ③ OOS 파일 없음→사유 ④ 세션지표>0&t≥1→PASS(실산출물 형식 `c_net_expectancy.champion`으로) ⑤ t<1→BLOCK ⑥ 저널 없음→exit 2).

### 실행 검증 원문 (실저널, `--since 2026-11-19`)
```
[redeploy_gate] since=2026-11-19 min_trades=20
live: n=3 net_pnl=+4,688원 win_pct=66.7% pass=False
oos : 측정 불가 · accrual 마지막 행 2026-11-18T22:16:47 robust_auc=0.5039
verdict=BLOCK (exit 1)
{
  "since": "2026-11-19",
  "live": {"n": 3, "net_pnl": 4688.0, "win_pct": 66.7, "pass": false},
  "oos": {"session_expectancy_pct": null, "t_stat": null, "source": null,
          "pass": false, "reason": "측정 불가"},
  "verdict": "BLOCK",
  "reasons": ["표본 3 < 20", "OOS 세션 지표 없음"]
}
EXIT=1
```
→ **실제 3건이 잡혔고, 정책 예시 출력과 필드·수치가 정확히 일치**합니다.

추가 검증 — 실세션지표 지정 시(정책의 "세션 기대 −0.182%" 출처인 `reports/model_metric_protocol_audit_results.json`):
```
"oos": {"session_expectancy_pct": -0.182, "t_stat": -0.81, "source": "reports/model_metric_protocol_audit_results.json",
        "pass": false, "reason": "순기대 -0.182% ≤ 0; t -0.81 < 1"} → BLOCK, EXIT=1
```

**참고**: `--session-file`은 스펙상 기본값이 없어 미지정 시 "측정 불가"(예시 그대로)이고, 지정 시 위처럼 실측 판정합니다. 기본 실행으로 `data/reports/redeploy_gate.json`(게이트 산출물, 신규)이 생성되며, 원본 저널·OOS·세션 파일은 읽기만 했습니다. 커밋은 오케스트레이터에 맡깁니다.

# 수집 호출 정책 (net_guard) — IP 차단 회피를 프로세스 밖에서 강제

WHY (2026-10-02): 각 수집기는 **자기 프로세스 안에서만** 지연을 지킨다
(`KIS_REQUEST_DELAY=3.0+지터`, `KRX_REQUEST_DELAY`, DART `DELAY_BASE=1.5`).
그래서 같은 호스트/앱키에 **두 프로세스가 겹치면** — 크론 + 수동 실행, 또는 크론 두 개 —
실제 호출률은 2배가 되고, 로그에는 두 러너 모두 정상으로 보인다. 차단은 그 다음에 온다.

`scripts/net_guard.py` 는 호스트(자격증명) 키별 상태 파일 + `flock` 으로 다음을 강제한다.

1. **최소 호출 간격**(프로세스 간) — 락을 잡고 대기하므로 두 프로세스가 직렬화된다
2. **일일 호출 예산** — 소진 시 `BudgetExhausted` → 러너는 정상 종료하고 다음 실행에서 재개
3. **차단 쿨다운** — 401/403/WAF(HTML 본문)/451 등은 즉시 중단 + 쿨다운을 **모든 프로세스에 전파**
4. **이벤트 기록** — `data/reports/net_guard/events.jsonl` (감사·경보가 읽는다)

정확도는 건드리지 않는다 — 호출 **시각**만 조정하고, 무엇을 얼마나 받아 어떻게 적재하는지는
각 러너가 그대로 한다(부분 수집·스킵 없음). 가드 자체가 실패하면 **fail-open**(기존 동작으로 폴백)이다.

## 배선 현황 (호스트 키)

| 키 | 러너 | 간격 | 예산(env) |
|---|---|---|---|
| `kis-<앱키해시>` | `services/kis-collector`(일봉·분봉·수급) · `kis_short_selling_backfill` · `kis_program_trading_collect` · `data_gap.py` 휴장 프로브 | `KIS_REQUEST_DELAY` 3.0s + 0.5s 지터 | `KIS_DAILY_BUDGET` (기본 0=무제한) |
| `krx` | `krx_daily` · `krx_derivatives_collect` · `refresh_valuation_ratios` | 3.0s + 0.5s | `KRX_DAILY_BUDGET`(0) · krx_daily 는 `KRX_MAX_CALLS`(600) |
| `dart` | `dart_disclosure_backfill` | 1.5s + 0.8s | `DART_DAILY_BUDGET`(2500) |
| `ecos` / `fred` | `macro_backfill` | 0.5s + 0.3s | `MACRO_DAILY_BUDGET`(6000) |
| `naver` | `naver_daily_backfill` | 0.5s + 0.3s | `NAVER_DAILY_BUDGET`(0) |

**예산은 '일일' 상한이지 '실행' 상한이 아니다.** 러너별 실행 상한(`KIS_MAX_CALLS`,
`--max-calls`, `KRX_MAX_CALLS`)을 예산으로 그대로 쓰면 분봉처럼 콜이 많은 러너가 조용히
토막난다 → 그래서 별도 env 이고 기본 0(무제한)이다. 켜려면 명시적으로 올린다.

## 함께 바뀐 것 (2026-10-02)

- **KIS 클라이언트**: `_sleep_before_call` 이 가드를 통과한다. 정책 중단은
  `KisApiError("NETGUARD-BLOCK"/"NETGUARD-BUDGET")` 로 올라오고 이 코드는
  `RATE_LIMIT_CODES` 에 포함 → 수집 루프의 `quota_hit` 분기가 멈춘다(재시도 금지).
  401(토큰 재발급 경로)은 차단으로 기록하지 않는다.
- **분봉 수집기**: `quota_hit` 중단 분기가 없어 정책 중단을 무시하고 계속 돌았다 → 추가.
- **DART**: 어떤 실패든 즉시 `break` 하던 것을 **429/5xx/타임아웃 지수 백오프 재시도**
  (`DART_RETRY_MAX` 3, `DART_RETRY_BACKOFF_BASE` 5.0s)로 바꾸고, 차단 신호는 재시도하지 않는다.
- **macro(FRED/ECOS)**: 429 를 즉시 포기하던 것을 백오프 재시도로 바꾸고, JSON API 가
  HTML(WAF 페이지)을 주면 **성공으로 읽지 않고** 차단으로 처리한다.
- **naver**: 403 이 재시도 루프에 말려들어 **빈 목록(='데이터 없음')으로 오독**되던 결함을
  즉시 중단으로 고쳤다(스모크 테스트가 잡았다).
- **가드가 있을 때는 러너 자신의 대기(sleep)를 생략**한다 — 같은 간격을 두 번 기다려
  수집이 2배 느려지는 것을 막는다(정확도·속도 유지).

## 읽는 법 / 운영

```bash
cd /home/jhshi/analyist_dd
python3 scripts/net_guard.py status          # 호스트별 오늘 호출·예산·차단 남은 시간
python3 scripts/net_guard.py events -n 20    # 최근 이벤트(blocked/transient/transport_error)
python3 scripts/net_guard.py clear --key krx --note "상황 확인 후 해제"
python3 scripts/net_guard_alert.py --window-hours 24   # 경보(문제 없으면 출력 없음)
```

- 경보 틱: `net_guard_alert_tick.sh` — 매일 01:05·19:05·20:05·23:05 (no_agent, Discord,
  직전과 같은 경보면 침묵). 크론 job_id `ffeda630ef2b`.
- 쿨다운 기본 900초(`NET_GUARD_BLOCK_COOLDOWN`). 실제 차단이면 **원인을 확인한 뒤** 해제한다 —
  해제는 "다시 때려도 된다"는 선언이고, 그 판단 근거를 `--note` 에 남긴다.
- 상태 파일: `data/state/net_guard/<키>.json` (오늘 호출수·마지막 호출시각·차단 사유·정책값).

## 검증 (네트워크 불필요, 3종)

```bash
python3 scripts/_net_guard_smoke_test.py        # 가드 자체: 분류·간격·프로세스 간 직렬화·예산·fail-open
python3 scripts/_kis_guard_smoke_test.py        # KIS 클라이언트: 403 차단 후 HTTP 미발신·예산 중단
python3 scripts/_collector_guard_smoke_test.py  # krx/dart/macro/naver/kis_program 배선
```

실측 근거(2026-10-02): 3 프로세스 × 4콜, delay 0.25s → 합친 타임스탬프 최소 간격 **0.25s**
(가드 OFF 대조군은 **0.000s**) · 403 응답 뒤 다음 호출의 HTTP 발신 **0회** ·
`macro_backfill --dry-run` 실호출 2콜이 `fred` 키에 기록됨.

## 되돌리기

러너별로 `net_guard` import 를 지우면 기존(프로세스 내) 지연 동작으로 완전히 돌아간다 —
가드는 러너의 필수 의존성이 아니고, 없으면 조용히 폴백한다(`NET_GUARD_DISABLE=1` 로 일괄 비활성).

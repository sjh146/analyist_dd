# 알림이 조용히 사라지는 두 경로와 회복 절차

용도: "경보가 왜 안 왔나" · "감시가 조용한 게 정상인가" 를 판정하고, 같은 유실이 반복되지 않게 배선할 때.
감시 스크립트를 새로 만드는 것이 아니라 **전달 계층**을 고치는 작업이다.

## 두 실패 모드는 원인도 처방도 다르다

| 모드 | 증거 | 의미 | 처방 |
|---|---|---|---|
| 전달 대상 없음 | `executions.delivery_outcome = not_configured`(스크립트 크론 기본값 `origin` 포함) | 잡은 정상 완료, 본문은 `~/.hermes/cron/output/<job_id>/` 에만 남음 | `deliver` 를 살아있는 채널(`platform:chat_id`)로 명시 |
| 회선 단절 | `deliveries.status='failed'`, `error` 에 `ClientConnectorDNSError ... name resolution` / `TimeoutError` | 경보는 생성됐지만 **전송 자체가 불가** — 사람은 영원히 못 받음 | 회선 감시 + 실패분 백로그 재발송 |

둘 다 `last_status=ok` 로 보인다. 판정은 반드시 `~/.hermes/cron/executions.db` 와 `deliveries.db` 로 한다.

## 실측 집계 (하루 단위)

```sql
-- 실행 상태·전달 결과
select delivery_outcome, count(*) from executions
where started_at like '2026-01-01%' group by 1;
-- 어떤 잡이 왜 실패했나 (execution_id 로 조인)
select d.created_at, ex.job_id, substr(d.error,1,80), d.status
from deliveries d left join executions ex on ex.id = d.execution_id
where d.status='failed' order by d.created_at desc limit 20;
```

`delivered` / `failed` / `suppressed`(빈 stdout = 의도적 침묵) / `not_configured` 를 구분해 센다.
`suppressed` 를 '경보 없음'으로, `failed` 를 '경보 없음'으로 읽으면 감시가 죽은 상태를 정상으로 보고한다.

## 백로그 재발송 잡 (전달 성공을 확인한 뒤에만 지운다)

상태 파일(`~/.hermes/state/<name>.json`)에 `pending` / `inflight` / `watermark` 를 두는 `no_agent` 스크립트.

1. 직전 실행분이 실제로 갔는지 확인: `executions.delivery_outcome=='delivered'`(자기 job_id 의 최신 행)일 때만
   `inflight` 의 id 를 `pending` 에서 제거한다. **전달 전에 지우면 경보가 영영 사라진다.**
2. `deliveries` 에서 `status='failed'` 이고 watermark 이후인 행을 `pending` 에 합친다(id 로 중복 제거).
3. 회선 확인(probe 204 + DNS). 죽어 있으면 **아무것도 출력하지 않고** pending 만 쌓는다(이 실행도 전달 실패할 것이므로).
4. 살아 있으면 잡 이름·오류 유형·시간대로 묶어 12줄 이내 요약을 출력하고 `inflight` 를 기록한다.
   스케줄러가 이 stdout 을 그대로 전달한다.

자기 자신과 회선감시 잡의 실패는 집계에서 제외한다(자기 실패를 알리려다 무한 재귀).

## 회선 도달성 감시 (전이 기반)

- 판정: `curl --noproxy '*' -w '%{http_code}' http://www.gstatic.com/generate_204` == 204 **그리고** DNS 해석 성공
  (프록시 환경에서는 `--noproxy` 없이 재면 프록시 응답을 서비스 상태로 오독한다). 보조 증거로 터널 로그의
  마지막 `reachable` / `not found on any interface` 줄을 붙인다.
- 출력 규칙: **정상이면 침묵**, 단절 시작 1회, 지속 중에는 60분 간격 재경보, 복구 1회. 상태 파일에 `state`/`down_since`/`last_alert`.
- 이 잡도 회선이 죽으면 전달되지 않는다 — 그것이 백로그 잡이 필요한 이유다(복구 시점에 한꺼번에 도착).

## 검증 (실회선을 건드리지 않고)

프로브 함수를 주입해 3단 전이를 시험한다: `probe → ('000', False)` 경보 1회 → 같은 값 재호출 시 침묵
→ `('204', True)` 복구 1회. 백로그는 실제 실패가 쌓인 상태에서 잡을 1회 발화(`cronjob run`)해
`delivery_outcome=delivered` 를 확인하고, 다음 틱에서 `pending 0`·stdout 침묵을 확인한다.

⚠ 손으로 먼저 돌려 출력을 확인하면 그 출력이 상태 파일에 남아 **배선 직후 첫 크론 전달이 억제**된다(빈 stdout).
검증은 잡의 기록(`executions`/`deliveries`)으로 하고, 손으로 돌렸다면 상태 파일을 지워 다음 실행이 한 번은 전달되게 한다.

## 보고

단절 구간의 유실은 **건수·시간대·원인 문자열**로 적고(예: `08:20~13:00 / DNS 실패 11건`),
복구 후 재발송이 실제로 도착했는지까지 붙인다. "알림이 안 왔다"와 "경보가 없었다"는 다른 사실이다.

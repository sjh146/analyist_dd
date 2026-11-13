# 알림 도달성 — 전달 실패 백로그 + 회선 감시

이 문서는 매매 경로 감시가 **조용히 죽는 두 가지 방식**과 그것을 잡는 배선을 적는다.

## 문제: 이 회선은 프록시 전용이다

집/사무실 회선이 아니라 폰 핫스팟 + 로컬 HTTP 프록시(`<gateway>:8080`)로만 egress 가 있다.
그 프록시가 사라지면 mihomo 터널이 내려가고(`tunnel.log`: `phone proxy not found on any interface -
waiting, tunnel stays off`) **DNS·아웃바운드가 전부 죽는다**. 그때:

1. Creon 세션/브리지가 죽는다 → 매매 경로 정지(사람이 재로그인해야 복구)
2. Hermes 크론의 Discord 전달이 실패한다 → **경보는 생성되지만 아무도 못 받는다**
   (실측 오류: `Discord send failed: ClientConnectorDNSError: ... Temporary failure in name resolution`,
   `TimeoutError`)

2026-11-13 실측: 06:46 터널 내려감 → 13:29 복구. 그 사이 08:20·08:25·08:50 개장 전 경보와
09:30~13:00 `alert-dispatch` 7건이 전달 실패(총 15건). 아침에 "로그인 필요"가 전달되지 않아
09:00~13:33 swing 진입창을 그대로 놓쳤다.

## 해결 1: `scripts/proxy_line_watch.py` (10분 주기)

probe(204) + DNS(discord.com) + `tunnel.log` 마지막 이벤트로 회선을 본다.
- 단절 시작 → 1회 경보(`[회선 단절]` + down_since + tunnel 마지막 줄 + 마지막 정상 시각)
- 지속 → 60분 간격으로만 재경보(스팸 방지)
- 복구 → 1회 `[회선 복구]`
- 정상이면 침묵(watchdog 패턴). 상태: `~/.hermes/state/proxy_watch.json`

## 해결 2: `scripts/alert_delivery_backlog.py` (30분 주기)

`~/.hermes/cron/deliveries.db` 의 `status='failed'` 행을 모아 **회선이 살아난 뒤 1회 요약 발송**한다.
- pending 은 `~/.hermes/state/alerts_backlog.json`
- **전달 성공을 확인한 뒤에만 지운다**: 다음 실행에서 `executions.db.delivery_outcome == 'delivered'`
  를 보고 inflight 를 제거한다(전달 실패했는데 지우면 경보가 영영 사라진다)
- 회선이 죽어 있으면 아무것도 출력하지 않고 pending 만 쌓는다
- 자기 자신과 회선감시 job 의 실패는 재귀 방지로 제외

## 해결 3: 개장 전 점검에 회선 항목 추가

`scripts/preopen_path_watch.py`(08:20 크론)가 probe/DNS/tunnel 마지막 이벤트를 함께 보고,
회선 미도달이면 `회선(프록시·터널) 미도달 — egress/DNS 불가` 를 경보에 포함한다.
(브리지 미연결·루프 미기동·오늘 사이클 없음과 **원인을 분리**해서 보여준다.)

## Hermes 크론 배선 (래퍼는 `~/.hermes/scripts/`)

| job | id | 주기 | 스크립트 |
|---|---|---|---|
| trader-preopen-check | cd7fd36bc8c4 | `20 8 * * 1-5` | `preopen_check.sh` → `scripts/preopen_path_watch.py --once` |
| alert-delivery-backlog | f374180fc0db | 30분 | `alerts_backlog.sh` → `scripts/alert_delivery_backlog.py --once` |
| proxy-reachability-watch | 67aef3417961 | 10분 | `proxy_watch.sh` → `scripts/proxy_line_watch.py` |

전부 `no_agent`(LLM 0) + `deliver=discord:<채널>`. 정상이면 침묵하므로 평소 메시지가 없다.

## 검증 방법

- 회선 감시: 단절/지속/복구 3단 전이는 `probe` 를 주입해 실회선 없이 시험한다
  (`pw.probe = lambda: ('000', False, 0)` → 경보, 재호출 → 침묵, `('204', True, 1)` → 복구).
- 백로그: 실제 실패가 쌓인 상태에서 job 을 `cronjob run` 으로 1회 발화 →
  `executions.delivery_outcome='delivered'` 확인 → 다음 틱에서 `pending 0` + stdout 침묵 확인.
- 정상 상태의 침묵이 곧 "이상 없음"의 증거이므로, **배선 검증은 stdout 이 아니라
  `executions.db`(status/delivery_outcome)로 한다.**

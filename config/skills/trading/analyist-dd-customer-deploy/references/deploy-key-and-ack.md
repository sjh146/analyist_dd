# 읽기 전용 deploy key + 고객 수신 확인(ack)

비공개 저장소(`sjh146/trader-feed`)를 유지하면서 고객을 붙이는 두 조각. 공개 전환은 검토 후 **보류**로 결정됐다
(피드·히스토리·전략 파라미터가 그대로 노출되면 후보를 따라 사는 쪽이 체결가를 나쁘게 만든다).

## 1. deploy key — 고객은 GitHub 계정이 필요 없다

고객 PC(1회):

    ssh-keygen -t ed25519 -C "customer-<이름>" -f "$env:USERPROFILE\.ssh\trader-feed"

공개키(`trader-feed.pub`) 내용만 받아서 서버에서 읽기 전용으로 등록:

    gh api -X POST repos/sjh146/trader-feed/keys \
      -f title='customer-<이름>' -f key="$(cat customer.pub)" -F read_only=true

고객 PC 원격 교체: `git remote set-url origin git@github.com:sjh146/trader-feed.git`
(무인 동기화면 `~/.ssh/config` 의 `Host github.com → IdentityFile ~/.ssh/trader-feed / IdentitiesOnly yes`).

**등록 직후 양방향으로 검증한다**: 같은 키로 `git clone --depth 1` 이 **성공**하고 `git push` 가
`does not have write access` 로 **거부**되어야 정상이다. 읽기 전용이 확인되지 않으면 고객 사고가 그대로 저장소 오염이 된다.

회수: `gh api repos/sjh146/trader-feed/keys --jq '.[] | "\(.id) \(.title)"'` → `gh api -X DELETE .../keys/<id>`.
고객별 키 1개, 이탈 시 그 id만 지운다.

## 2. ack — 고객이 실제로 받았는지 서버가 판정

흐름: 고객 `sync_feed.py` 동기화 성공 → `POST <ack_url>/v1/ack` (Authorization: Bearer <token>,
본문 `customer_id·feed_generated_at·version·commit·synced_at·hostname`) → relay 가
`data/feed_relay/ack/<YYYYMMDD>.jsonl` 에 적재 → `scripts/feed_ack_check.py` 가 판정.

- 토큰은 relay `customers.json` 의 `token_sha256` 과 대조된다(`services/feed_relay/add_customer.py` 로 발급,
  **토큰 평문은 발급 시 1회만 표시**). 등록 안 된 토큰은 401 — ack 실패는 동기화를 실패시키지 않는다(경고만).
- 클라이언트는 `config.ack_url` 이 비어 있으면 ack 를 보내지 않고, 결과는 피드 옆 `sync_status.json` 에 남는다
  (`ack.sent`·`ack.status`).
- `feed_ack_check.py` 종료코드: **0 = 전원 확인 / 1 = 미확인 고객 있음 / 2 = 판정 불가(피드 없음)**.
  리포트는 `reports/feed_ack_latest.json` (`customers[]`, `missing[]`, `age_min`).
  크론은 동기화(08:45) 뒤 여유를 두고 **08:58**(평일)에 돈다.

검증(운영 데이터를 건드리지 않게 임시 root + 별도 포트):

    python3 services/feed_relay/server.py --root /tmp/relay_test --port 8096
    python3 services/feed_relay/add_customer.py --root /tmp/relay_test --id pilot1 --base-url http://127.0.0.1:8096
    python3 services/feed_relay/client/sync_feed.py --config /tmp/relay_cfg.json --skip-git   # ack.sent=true 확인
    python3 scripts/feed_ack_check.py --ack-dir /tmp/relay_test/ack \
        --feed-path <fixture>/latest.json --customers-config <fixture>/customers.json --report /tmp/r.json

## 3. 고정 공개 주소 — Tailscale Funnel (도메인 불필요)

고객 `ack_url` 은 재시작마다 바뀌면 안 된다. WSL tailnet 노드(`dduckbeagy-wsl`, 100.93.220.52)에
Funnel 을 걸면 **고정 공개 HTTPS 주소**가 나온다(도메인·Cloudflare 터널 불필요):

    tailscale funnel --bg --https=443 http://127.0.0.1:8095
    tailscale funnel status          # 127.0.0.1:8095 프록시 확인

→ 고객 config 의 `ack_url` = `https://<node>.<tailnet>.ts.net/v1/ack`
(실측 `https://dduckbeagy-wsl.tail7dae99.ts.net/health` → 200, 왕복 ~13초 → ack 타임아웃은 35초).

- Funnel 은 DERP 릴레이를 거쳐 느리다. 고객 ack 타임아웃을 20초로 두면 간헐 실패한다.
- **WSL 재시작하면 tailscaled 가 죽어 Funnel 도 사라진다** → `service_supervisor.py` 가
  `tailscale0` 인터페이스와 `tailscale funnel status`(8095 프록시)를 5분 주기로 확인하고, 없으면
  `tailscaled` 기동 + `funnel --bg` 재설정. 감시 대상이 아니면 ack 만 조용히 끊긴다.

## 4. 남는 운영 공백

- Discord 알림은 `services/discord_notify/config.json`(웹훅 URL)이 없으면 크론이 조용히 건너뛴다 —
  알림이 "왔다"고 말하려면 그 파일 존재를 먼저 확인한다.
- ack 는 고객이 실제로 sync 를 **성공**했을 때만 온다. ack 부재를 "고객이 안 돌렸다"로 단정하기 전에
  고객 쪽 `logs/sync_feed.log`·`sync_status.json` 을 함께 본다.

# 고객 PC 배포 키(deploy key) 절차 — 비공개 저장소 유지

고객은 **GitHub 계정이 필요 없습니다.** 고객 PC에서 만든 SSH 공개키를 저장소의 읽기 전용 deploy key로 등록하면 됩니다.

저장소: `sjh146/trader-feed` (private)

---

## 1. 고객 PC에서 키 생성 (고객 1회)

PowerShell:

```powershell
ssh-keygen -t ed25519 -C "customer-<고객이름>" -f "$env:USERPROFILE\.ssh\trader-feed"
```

- 암호(passphrase)는 비워도 됩니다(자동 동기화가 무인 실행이므로 비우는 편이 안전).
- 생성되는 두 파일:
  - `trader-feed` → **개인키, 절대 전송 금지**
  - `trader-feed.pub` → **이 파일 내용만** 운영자에게 전달

공개키 내용 복사:

```powershell
Get-Content "$env:USERPROFILE\.ssh\trader-feed.pub"
```

## 2. 운영자: 읽기 전용으로 등록

```bash
gh api -X POST repos/sjh146/trader-feed/keys \
  -f title='customer-<고객이름>' \
  -f key="$(cat customer.pub)" \
  -F read_only=true
```

등록 확인:

```bash
gh api repos/sjh146/trader-feed/keys --jq '.[] | "\(.id) \(.title) read_only=\(.read_only)"'
```

## 3. 고객 PC에서 원격을 SSH로 변경

`C:\Users\<고객>\trader-feed` (clone 위치)에서:

```powershell
git remote set-url origin git@github.com:sjh146/trader-feed.git
```

`%USERPROFILE%\.ssh\config` 에 아래를 추가하면 `GIT_SSH_COMMAND` 없이 동작합니다:

```
Host github.com
  HostName github.com
  User git
  IdentityFile ~/.ssh/trader-feed
  IdentitiesOnly yes
```

## 4. 검증 (고객 PC)

```powershell
cd $env:USERPROFILE\trader-feed
git fetch --depth 1 origin main
git log --oneline -1 FETCH_HEAD
git reset --hard FETCH_HEAD
git push origin HEAD:main   # 실패해야 정상(읽기 전용)
```

`git push` 가 `ERROR: The key ... does not have write access` 로 거부되면 올바르게 설정된 것입니다.

## 5. 키 회수 (고객 이탈·키 교체)

```bash
gh api repos/sjh146/trader-feed/keys --jq '.[] | "\(.id) \(.title)"'
gh api -X DELETE repos/sjh146/trader-feed/keys/<id>
```

고객별로 키를 1개씩 발급하고, 회수 시 해당 id만 삭제합니다.

## 운영 메모

- 실측 검증(2026-11-24): 읽기 전용 deploy key로 `git clone` 성공, `git push` 거부 확인.
- 저장소가 비공개이므로 매일 게시(08:40) 이후에만 고객이 새 피드를 받습니다.
- 고객이 5명을 넘으면 계정 초대(collaborator) 방식이 관리상 유리할 수 있습니다.

## 수신 확인(ack) 설정

고객 `config.json` 에 아래를 넣으면 매일 08:45 동기화 후 수신 확인이 우리 쪽에 기록됩니다.

```json
"ack_url": "https://dduckbeagy-wsl.tail7dae99.ts.net/v1/ack",
"token": "<add_customer.py 가 발급한 토큰>"
```

- ack 는 **동기화 성공 후** 전송되며, 실패해도 거래에는 영향을 주지 않습니다(경고만).
- 우리 서버 쪽 판정: 평일 08:58 `scripts/feed_ack_check.py` → 미확인 고객이 있으면 rc=1.
- 이 주소는 Tailscale Funnel 고정 주소입니다(터널 재시작에도 변하지 않음). relay 프로세스와 funnel 은 `service_supervisor.py` 가 5분 주기로 점검·복구합니다.

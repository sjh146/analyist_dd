---
name: trader-customer-deployment
description: Use when deploying the trader stack to customer PCs.
---

# 고객 PC 배포 (중앙 추론 → 고객 실행기)

이 스킬은 데이터·모델·추론은 내 서버, 고객 PC 에는 얇은 실행기(trader-agent + Creon Plus COM 브리지)만
두는 배포 구조를 다룬다. Creon 자동화 자체는 `creon-cybosplus-trader-agent`, 실주문 안전은
`live-order-path-safety` 를 본다. 여기서는 **배포 계약·업데이트·공개 경로·함정**만 다룬다.

## 1. 형태를 먼저 고정한다

- A 구축형: 고객 PC 에 전부 설치, 운영은 고객. 유지보수 수입 없음.
- B 중앙추론형(권장): 매일 08:30 까지 서버가 추론 → 고객 PC 가 피드를 pull 해서 주문만 한다.
- C 자문형: 구축만. 책임은 작지만 지속 수입이 없다.

B 를 권하는 이유: API 키·쿼터를 고객마다 발급하지 않아도 되고, 모델·전략 수정이 전 고객에 동시 반영되고,
KRX/KIS/DART 재배포 이슈를 피할 수 있고(고객은 신호만 받음), 지원 로그가 한 형식으로 모인다.

## 2. 전제 (환경)

- Creon Plus API 는 Windows COM/OCX → **고객 PC 는 Windows**.
- 32bit 파이썬 = COM 브리지, 64bit 파이썬 = 트레이더 코어. 두 프로세스, 로컬 HTTP 로 통신.
- 크레온 로그인·UAC 승격은 사람 단계다. 설치 1회 + 재부팅 시 로그인 절차를 계약서에 적는다.
- 고객이 하는 일은 가입·비대면 계좌·모의투자 신청·API 신청 4가지로 한정하고 나머지는 운영자가 한다.

## 3. 통신은 pull 전용

고객 PC 가 NAT 뒤에 있으므로 서버→고객 push 는 불가능하다(테일넷 가입이나 WebSocket 상시 연결이 필요).
고객 PC 는 **아웃바운드 443 으로 당겨가는** 방식만 쓴다. 계약 요약(상세: `references/api-contract.md`):

| 메서드 | 경로 | 인증 | 내용 |
|---|---|---|---|
| GET | `/v1/feed?customer_id=` | Bearer | 오늘 피드 + 서버가 정한 limits 주입 |
| GET | `/v1/manifest?channel=` | 서명 | 버전·파일별 sha256 |
| GET | `/v1/pkg/<version>` | 없음 | 번들 zip |
| POST | `/v1/ack` | Bearer | 피드 수신 확인 |
| POST | `/v1/telemetry` | Bearer | 로그 요약·상태 |

- **fail-closed**: 피드 미수신·스키마 불일치면 거래하지 않는다. 네트워크 장애로 주문이 나가는 것보다 안 도는 게 낫다.
- 한도는 서버 `limits` 와 클라이언트 게이트 중 **작은 값**이 적용된다(고객이 로컬 설정을 고쳐도 서버 상한을 못 넘는다).

## 4. 배포 런북

1. 피드 게시: `python3 services/feed_relay/publish_feed.py` — `generated_at` 이 18h 초과거나 후보 0건이면 거부한다.
2. 고객 등록: `add_customer.py --id <id> --base-url <url>` — 토큰은 1회만 표시되고 서버에는 sha256 만 남는다.
3. 릴리스 빌드: `build_release.py --version X.Y.Z --channel stable|beta` — 파일별 sha256 + 번들 sha256 + 서명.
4. 고객 PC 설치: 앱 디렉토리 + `config.json`(base_url/customer_id/token/channel/release_key) + 예약작업
   (08:10 preflight·update, 08:45 feed pull, 08:50 루프 시작, 15:40 telemetry+update).
5. 검증: 공개 URL `/health` 200, 무토큰 401, 토큰 피드 200 + limits, pull 후 `ack/*.jsonl` 행, 업데이트 후 `version.json`.
6. 모의 1~2주 → 실계좌 소액 → 증액. 각 단계 통과 기준을 문서로 남긴다.

자동화·설치 상세: `references/jenkins-orchestration.md`(잡 생성·플러그인·트리거·토큰 처리),
`references/windows-install-kit.md`(PowerShell 인코딩·MinGit ssh·예약작업·preflight).

## 5. 업데이트 안전 규칙

- 다운로드 → 서명·sha 검증 → 스테이징 → 파일별 sha 검증 → 현재 app 백업 → 교체 → 실패 시 롤백.
- 적용 시간은 **장 마감 후 또는 개장 전만**. 장중 업데이트 금지.
- 스키마·최소 버전 핀: 클라이언트가 `feed_schema_version`/`min_client_version` 미충족이면 피드를 거부한다.
- 채널은 stable/beta 로 나누고 파일럿 고객만 beta 에 둔다.

## 4a. git pull 배포 경로 (릴레이 없이 쓰는 두 번째 경로)

터널·서버 없이 **비공개 git 저장소 하나로 피드와 코드를 함께** 배포할 수 있다. 레이아웃:

    deploy/latest.json · deploy/meta.json · deploy/history/YYYY-MM-DD.json   (피드 + sha256)
    app/  VERSION · BUILD_INFO.json + trader-agent 코드              (고객이 실행)
    client/ sync_feed.py · preflight.ps1 · install.ps1              (클라이언트 자신도 git 으로 갱신)

- 고객 PC 는 `git fetch --depth 1 origin main && git reset --hard FETCH_HEAD` 로 맞춘다.
  **`pull` 을 쓰지 않는다** — 고객 저장소는 읽기 전용(deploy key)이라 커밋이 없고, reset 이 충돌·드리프트를 원천 차단한다.
- 피드 검증: `meta.sha256`(파일 바이트) + `generated_at` 이 오늘인지 + `feed_schema_version`. 실패면 파일을 건드리지 않고
  종료코드 1 → 트레이더는 거래하지 않는다(fail-closed).
- 코드 교체: `app/VERSION` 과 설치된 `version.json` 비교 → 스테이징 복사 → 필수 파일(`trader_core/engine.py` 등) 검증
  → 현재 app 백업 → `os.replace` → 실패 시 백업 복원. `config_live.json`·`logs/`·`journal/`·`kill_switch.txt` 는 제외(PRESERVE)한다.
- 게시 측은 내용 해시(`published_at` 제외)로 **같은 내용이면 커밋하지 않는다** — 매일 빈 커밋이 쌓이면 이력이 못 읽게 된다.
- **줄바꿈이 무결성을 깬다**: Windows 체크아웃이 CRLF 로 바꾸면 `sha256` 이 서버와 달라져 고객이 피드를 거부한다.
  저장소에 `* -text` `.gitattributes` 를 두고, 클라이언트는 `core.autocrlf false` 설정 후 이미 오염된 트리를
  `git rm --cached -r -q . && git reset --hard HEAD` 로 **재체크아웃**해야 고친다(설정만 바꿔도 기존 파일은 그대로다).

## 6. 보안 (사고 방지)

- **서명키를 전 고객이 공유하면 고객 1명이 전 고객용 악성 업데이트를 서명할 수 있다 — CRITICAL 이다.**
  고객 config 에는 **고객별 파생 키**만 넣는다: `key_c = HMAC-SHA256(master, "release:<customer_id>")`.
  마스터는 서버에만 두고, 서버가 `/v1/manifest` 를 요청 고객의 파생 키로 서명해 내려준다(manifest 도 Bearer 인증 필수).
  회귀 테스트로 **다른 고객 키로는 검증이 실패**하는지 고정한다.
- 파생 키도 대칭(HMAC)이다. 상용 전 Ed25519 로 올려 **개인키는 서버에만**, 공개키를 클라이언트에 고정한다.
- 고객 토큰은 서버에 sha256 만 저장하고 발급 시 1회만 표시한다. `.env`·서명키·고객 데이터는 `.gitignore` 에 넣는다.
- 텔레메트리는 계좌번호 패턴을 마스킹하고 집계 수치만 보낸다. 원격 지원은 고객 동의 하 임시 경로만.

## 7. 공개 경로 선택

| 방식 | 추가 필요 | 비용 | 특징 |
|---|---|---|---|
| Cloudflare 빠른 터널 | 없음 | 무료 | 파일럿용. **재시작마다 URL 변경** → 고객 설정 깨짐 |
| Tailscale Funnel | 없음 | 무료 | 고정 ts.net 주소, 도메인 불필요. 파일럿~초기 상용 |
| Cloudflare Tunnel | 도메인 | 연 1~2만원 | 고정 커스텀 도메인, 상용 권장 |
| VPS/오브젝트스토리지 pull | VPS·버킷 | 월 수천원 | 홈서버 다운과 분리. 고객 늘면 이 구조로 |

홈 서버(WSL)에 고객 트래픽을 직접 물리지 않는다. 이 박스는 OOM 으로 죽은 이력이 있다.
상용은 서버가 생성만 하고 버킷/VPS 로 올리는 분리가 정석이다.

## 8. 함정 (반드시)

- 스테이징 디렉토리를 **대상 app 과 같은 부모/볼륨** 아래에 만든다: `os.replace` 는 파일시스템을 넘으면
  EXDEV 로 실패하고, 목적지 부모가 없으면 FileNotFoundError 로 실패한다(`/tmp` → `C:` 같은 조합이 대표 사례).
- 릴레이 서버는 `customers.json`/`channels.json` 을 **요청마다 mtime 비교로 재로드**한다. 시작 시 캐시하면
  고객 추가·새 릴리스가 404 로 보인다.
- 공개 URL 검증은 DNS 를 믿지 않는다. 로컬 리졸버가 막히면 터널이 정상이어도 000 이 뜬다.
  DoH 로 IP 를 얻어 `curl --resolve <host>:443:<ip>` 로 확인한다.- 코드 변경 후 `curl` 검증은 `--noproxy '*'` 를 붙인다: 셸 프록시가 로컬 호출을 502 로 오탐시킨다.
- 고객 PC 가 KRX/DART/KIS 를 직접 호출하게 만들지 않는다: 약관 재배포 + 키·쿼터 관리 폭증.
- 자동매매 프로그램 제공/운용은 자문·일임 규제와 맞닿는다. **소프트웨어 라이선스 vs 운용 서비스** 를 먼저 정하고
  계약서에 손실 미보장·책임 한도·데이터 사용 범위를 넣은 뒤 변호사 검토를 받는다.

## 9. 완료 증거 체크리스트

- [ ] 공개 URL `/health` 200 + 무토큰 401 + 토큰 피드 200(limits 주입 확인)
- [ ] 번들 서명·sha 검증 통과, 클라이언트 적용 후 `version.json` 생성
- [ ] 피드 pull 성공 후 서버에 `ack/<날짜>.jsonl` 행
- [ ] 텔레메트리 저장 파일(계좌번호 마스킹 확인)
- [ ] 장중 업데이트 금지·fail-closed 를 테스트로 고정
- [ ] 남은 점(공개 경로 안정성, 설치 키트, 서명 강도)을 보고에 적었다

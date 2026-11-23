# feed_relay — 고객 PC 로 피드·업데이트를 내보내는 릴레이

중앙 서버(analyist_dd)가 만든 **오늘의 추론 결과**와 **코드 번들**을 고객 PC 가 HTTPS 로 pull 해가는 구조.
고객 PC 는 아웃바운드 443 만 쓰므로 공유기·방화벽 설정이 필요 없다.

## 구성

    services/feed_relay/
      server.py          릴레이 HTTP 서버(표준 라이브러리, 의존성 없음)
      publish_feed.py    오늘의 피드를 릴레이에 게시(신선도·0건 검사)
      build_release.py   trader-agent → bundle.zip + 서명 manifest
      add_customer.py    고객 토큰 발급 + customers.json 갱신 + config 출력
      serve_public.sh    로컬 서버 기동 + 공개 터널(cloudflared / Tailscale Funnel)
      client/
        feed_pull.py     피드 pull + ack (원자적 쓰기)
        updater.py       서명 검증 → 스테이징 → 교체 → 롤백
        telemetry.py     로그 요약·상태 전송(계좌번호 마스킹)
        config.example.json

## 엔드포인트

| 메서드 | 경로 | 인증 | 내용 |
|---|---|---|---|
| GET | `/health` | 없음 | 상태 |
| GET | `/v1/feed?customer_id=` | Bearer | 오늘 피드 + 고객 limits 주입 |
| GET | `/v1/manifest?channel=` | 없음(서명됨) | 버전·파일 sha256·HMAC 서명 |
| GET | `/v1/pkg/<version>` | 없음 | bundle.zip |
| POST | `/v1/ack` | Bearer | 피드 수신 확인(누락 판정용) |
| POST | `/v1/telemetry` | Bearer | 일일 로그 요약·상태 |

## 일일 흐름

1. 08:20~08:35 중앙 파이프라인이 피드 생성(`data/feed/screener_latest.json`)
2. `publish_feed.py` 가 `data/feed_relay/feed/latest.json` 으로 게시 (18시간 초과·후보 0건이면 거부)
3. 고객 PC 08:45 `feed_pull.py` → 성공 시 ack, 실패 시 **거래하지 않음**(fail-closed)
4. 08:50 트레이더 루프 시작
5. 15:40 `telemetry.py` 전송 + `updater.py` 로 코드 갱신(장중 금지)
6. 서버에서 ack/텔레메트리로 "오늘 못 받은 고객"을 판정

## 운영 명령

    python3 services/feed_relay/publish_feed.py
    python3 services/feed_relay/add_customer.py --id pilot1 --base-url https://xxx.trycloudflare.com
    python3 services/feed_relay/build_release.py --version 1.1.0 --channel stable
    bash services/feed_relay/serve_public.sh

## 보안

- 고객 토큰은 sha256 만 저장(원문 미보관). 유출 시 `add_customer.py` 재실행으로 교체.
- 업데이트 서명은 **고객별 파생 키**를 쓴다: `HMAC-SHA256(master, "release:<customer_id>")`.
  마스터 키(`data/feed_relay/release_key.txt`)는 서버에만 두고, 고객 config 에는 그 고객의 파생 키만 넣는다.
  따라서 고객 1명의 키가 유출돼도 **다른 고객용 업데이트를 위조할 수 없다**(이전 공유 키 방식의 CRITICAL 결함 수정).
- `/v1/manifest` 는 Bearer 토큰이 필요하고, 서버가 요청 고객의 파생 키로 서명해 내려준다.
- 텔레메트리는 계좌번호 패턴을 `***-****` 로 마스킹하고 집계 수치만 보낸다.
- 공개 경로는 파일럿에서 Cloudflare 빠른 터널(URL 매번 변경) 또는 Tailscale Funnel. 상용은 커스텀 도메인 + Cloudflare Tunnel 을 권장.
- 서명은 현재 HMAC-SHA256(릴리스 키 공유). 상용 전 Ed25519 로 교체하고 공개키를 클라이언트에 고정한다.

## 아직 안 한 것

- TLS 종료를 릴레이가 직접 하지 않는다(터널이 담당). 사내망 직접 노출 시 `--cert/--key` 사용 가능.
- 고객별 대시보드(ack 누락·텔레메트리 조회)는 파일 기반 확인만 있다.
- 다중 인스턴스/무중단 배포, 요청 레이트리밋, 감사 로그 보존 정책.

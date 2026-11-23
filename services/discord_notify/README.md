# 고객 Discord 운영 — 알림·소통

고객을 한 서버에 모아 두고, 매일 08:30 장전 체크 알림과 08:55 수신 확인, 15:40 마감 요약을 보낸다.

## 1. 서버 만들기 (대표 1회, 3분)

1. Discord 좌측 `+` → **서버 만들기** → `직접 만들기` → 이름 예: `트레이딩 운영`
2. 채널 3개 생성
   - `#공지` — 알림 전용(고객은 읽기만, 쓰기 권한 제거)
   - `#체크인` — 고객이 ✅ 반응/짧은 확인
   - `#질문` — 문의(스레드 사용)
3. `#공지` 채널 권한: `@everyone` → **메시지 보내기 끄기**, **반응 추가 켜기**
4. 고객 역할 `고객` 생성 후 초대 시 부여

## 2. 초대

1. 서버 설정 → **초대** → 만료 7일, 최대 사용 10회로 링크 생성
2. 고객에게 링크 + 아래 안내문 전달
3. 입장 후 역할 `고객` 부여, 채널별 권한 확인

> 고객이 PC 를 켜야 알림을 볼 수 있으므로, **휴대폰 Discord 앱 설치 + 서버 알림 켜기**를 안내문에 반드시 포함한다.

## 3. 알림 연결 (운영자, 2분)

**웹훅(가장 간단)**
1. `#공지` → 채널 설정 → **연동(Integrations)** → **웹훅** → 새 웹훅 → 이름 `analyist_dd` → **웹훅 URL 복사**
2. `services/discord_notify/config.json` 생성:

       {"mode":"webhook","webhook_url":"<복사한 URL>",
        "feed_repo":"/home/jhshi/trader-feed",
        "relay_root":"/home/jhshi/analyist_dd/data/feed_relay",
        "customers":["pilot1","pilot2"]}

3. 확인: `python3 services/discord_notify/notify.py test --text "연결 확인"`

**봇(스레드·DM·반응 집계까지)**
1. https://discord.com/developers/applications → New Application → Bot → **Reset Token** 복사
2. Bot → Privileged Gateway Intents 는 **모두 끄기**(메시지 전송에는 불필요)
3. OAuth2 → URL Generator → scopes `bot` + permissions `Send Messages`, `Create Public Threads`, `Read Message History` → 생성된 URL 로 서버 초대
4. config 에 `"mode":"bot","bot_token":"...","channel_id":"<#공지 채널 ID>"` (Discord 설정 → 고급 → 개발자 모드 → 채널 우클릭 ID 복사)

## 4. 자동 발송 (크론)

| 시각 | 명령 | 내용 |
|---|---|---|
| 08:30 | `notify.py morning` | 피드 상태 + PC 전원/크레온 로그인 3단계 + risk_off 경고 |
| 08:55 | `notify.py ack` | 수신 완료/미완료 고객 목록 |
| 15:40 | `notify.py close` | 후보 수·수신·텔레메트리 요약 |

피드가 오늘 것이 아니면 morning 은 **"PC 켜지 마세요" 경고**로 바뀐다.

## 5. 고객 안내문 템플릿 (그대로 전달)

    안녕하세요. 자동매매 운영 서버에 초대드립니다.
    1) 아래 초대 링크로 입장해 주세요: <링크>
    2) 휴대폰에 Discord 앱을 설치하고, 이 서버의 알림을 켜 주세요.
    3) 평일 아침 8:30 에 공지가 올라옵니다. 그때 PC 전원을 켜고 크레온 PLUS 에 로그인해 주세요.
    4) 8:45 자동 동기화가 끝나면 8:55 공지에서 수신 완료 여부를 확인해 주세요.
    5) 문의는 #질문 채널에 PC 이름과 함께 남겨 주세요. 계좌번호·비밀번호는 절대 적지 마세요.

## 6. 금지 사항

- 공개 채널에 **후보 종목명**을 쓰지 않는다(정보 유출). 알림은 건수·상태만.
- 계좌번호·주문번호·비밀번호를 채널에 남기지 않는다. 필요하면 DM.
- 손익을 공개 채널에 게시하지 않는다(고객별 비공개).

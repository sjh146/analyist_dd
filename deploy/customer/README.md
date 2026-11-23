# 고객 PC 설치 (Windows)

고객이 직접 하는 일은 4단계뿐이다. 나머지는 설치 시 방문/원격에서 처리한다.

## 고객이 하는 일

1. 대신증권 크레온 회원가입
2. 비대면 계좌 개설
3. 모의투자 신청 (실계좌 전환 전 2주)
4. 크레온 PLUS API 신청

## 운영자가 하는 일

1. `C:\TraderAgent\` 생성 후 구성:
       app\           (trader-agent 코드, feed_repo에서 받은 번들)
       feed-repo\     (trader-feed 저장소 clone)
       data\feed\     (sync_feed 가 쓰는 피드)
       logs\
       config.json    (add_customer.py 가 출력한 값)
       sync_feed.py, sync_feed.bat
2. Python 설치: 3.12 64bit(`app` 구동) + 3.11 32bit(크레온 브리지)
3. Git for Windows 설치 (`git` 필요 — 저장소 pull)
4. deploy key 등록(비공개 저장소 읽기 전용):
       ssh-keygen -t ed25519 -f C:\TraderAgent\customer_key
       gh repo deploy-key add customer_key.pub --repo sjh146/trader-feed --title <고객ID>
5. 예약작업 등록:
       powershell -ExecutionPolicy Bypass -File install_tasks.ps1
6. 크레온 PLUS 로그인은 **사람이 1회** 해야 한다(자동화 불가). 재부팅 시 재로그인 필요.

## 하루 흐름

| 시각 | 작업 | 실패 시 |
|---|---|---|
| 08:45 | `sync_feed.bat` — trader-feed pull, 오늘 피드 검증 | 거래하지 않음(fail-closed) |
| 08:50 | `loop_start_bg.bat` — 트레이더 루프 시작 | 로그 확인 |
| 15:40 | `post_close.bat` — 텔레메트리 전송 + 코드 번들 업데이트 | 다음날 재시도 |

## 고객 PC 점검 명령

    python sync_feed.py --config config.json
    python -c "import json;print(json.load(open(r'data\feed\sync_state.json')))"
    type logs\sync_feed.log

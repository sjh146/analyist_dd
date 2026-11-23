# Windows 설치 키트 (고객 PC)

한 번 설치하면 이후 **피드와 코드가 매일 08:45 git pull 로 자동 갱신**된다.
고객이 직접 하는 일은 크레온 로그인뿐이다.

## 구성

| 파일 | 역할 |
|---|---|
| install.ps1 | 사전검사 → git(없으면 MinGit 자동) → deploy key → clone → config/배치 생성 → 리허설 sync → 예약작업 3개 |
| preflight.ps1 | 장전 점검(피드 신선도·코드 버전·크레온 프로세스·브리지 헬스·디스크) |
| uninstall.ps1 | 예약작업·프로세스 정리, `-RemoveData` 로 전체 삭제 |

install.ps1 은 저장소의 `client/` 에서 sync_feed.py·preflight.ps1 을 받아 쓰므로,
클라이언트 코드도 git 으로 업데이트된다.

## 설치 (운영자, 목표 60분)

1. 사전 준비(고객): 크레온 가입 → 비대면 계좌 → 모의투자 신청 → 크레온 PLUS API 신청
2. Python 설치: 3.12 **64bit**(코어), 3.11 **32bit**(크레온 브리지)
3. 설치 실행:

       powershell -ExecutionPolicy Bypass -File install.ps1 -CustomerId pilot1 `
         -PythonCore "C:\Users\me\Python312-64\python.exe" `
         -PythonBridge "C:\Users\me\Python311-32\python.exe"

4. clone 이 실패하며 deploy key 등록 명령이 출력되면, 운영자 PC에서:

       gh repo deploy-key add "C:\TraderAgent\keys\feed_deploy.pub" --repo sjh146/trader-feed --title pilot1

   다시 install.ps1 을 실행하면 이어서 진행된다(멱등).
5. 리허설 sync 가 성공하면 `install_report.json` 확인 → 예약작업 3개 등록 확인:

       schtasks /Query /TN TraderAgent-SyncFeed

## 하루 흐름

| 시각 | 예약작업 | 내용 |
|---|---|---|
| 08:45 | TraderAgent-SyncFeed | git pull → 피드 검증 → 코드 교체(백업·롤백) |
| 08:50 | TraderAgent-LoopStart | 트레이더 루프 시작(로컬 피드 경로 사용) |
| 15:40 | TraderAgent-PostClose | 마감 후 sync(코드 갱신) + 로그 정리 |

## 검증·문제 해결

    powershell -ExecutionPolicy Bypass -File preflight.ps1 -Root C:\TraderAgent
    type C:\TraderAgent\logs\sync_feed.log
    type C:\TraderAgent\logs\preflight_report.json

- `feed FAIL` 은 08:45 sync 실패다. 네트워크·deploy key·서버 게시 여부를 본다.
- `creon_process FAIL` 은 크레온 PLUS 미로그인 상태다(사람이 로그인해야 한다).
- `bridge FAIL` 은 32bit 브리지 미실행이다. `run_bridge.py` 를 띄운다.
- 코드 교체 실패는 자동 롤백되며 `app.bak-*` 가 남는다. 로그에서 sha 불일치를 확인한다.

## 제거

    powershell -ExecutionPolicy Bypass -File uninstall.ps1 -Root C:\TraderAgent -RemoveData

deploy key 는 GitHub 에서 따로 지운다: `gh repo deploy-key list --repo sjh146/trader-feed`

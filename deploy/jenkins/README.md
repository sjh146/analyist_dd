# Jenkins — 피드 게시 오케스트레이션

호스트 크론 대신 Jenkins 가 08:40(KST) 에 `feed-publish` 파이프라인을 돌려
analyist_dd 피드를 `trader-feed` 저장소에 커밋·푸시한다. 실패하면 고객 PC 는 fail-closed 로 거래하지 않는다.

## 구조

    deploy/jenkins/
      docker-compose.yml   Jenkins LTS (127.0.0.1:8081, 메모리 1.2GB 제한)
      casc.yaml            JCasC: 관리자 계정, GitHub PAT 자격증명
      init.groovy.d/create_jobs.groovy  잡 2개 생성(피드·코드 게시)
      Dockerfile           python3 포함 이미지(파이프라인이 python3 사용)
      .env                 JENKINS_ADMIN_PASSWORD, GH_TOKEN (커밋 금지)
      home/                JENKINS_HOME (커밋 금지)
    ci/Jenkinsfile.feed    파이프라인 본문(검증 → 게시 → 번들 → 요약)

컨테이너는 호스트 디렉터리를 그대로 본다:

    /work/analyist_dd   = /home/jhshi/analyist_dd
    /work/trader-feed   = /home/jhshi/trader-feed
    /work/trader-agent  = C:\Users\jhshi\analyist_dd\trader-agent (읽기 전용)

## 잡

| 잡 | 트리거 | 내용 |
|---|---|---|
| `feed-publish` | 평일 08:40 KST | 피드 검증 → 게시 → (VERSION 지정 시) 코드 게시 |
| `feed-publish-manual` | 수동 | 같은 파이프라인, ALLOW_STALE 기본 true |

코드 게시는 `VERSION` 파라미터를 넣고 빌드하면 `scripts/publish_code_github.py` 가
`app/` 을 갱신한다. 고객 PC 는 08:45 sync 에서 피드와 코드를 함께 받는다.

## 기동

    cd deploy/jenkins
    ./bootstrap.sh

bootstrap.sh 가 순서대로 한다: 기존 컨테이너 정지 → 플러그인 설치(회선 23KB/s면 20~30분) →
홈 소유권 정리 → 이미지 빌드(python3 포함) → 기동 → 잡 확인.

수동 절차:

    docker compose build
    docker compose up -d

- 잡은 `init.groovy.d/create_jobs.groovy` 가 기동 시 만든다(JCasC 의 jobs 는 job-dsl 플러그인이 필요해 쓰지 않음).
- 파이프라인은 컨테이너 안에서 `python3` 를 쓰므로 이미지에 python3 를 넣는다(Dockerfile).
- `/work` 디렉터리는 jenkins 소유여야 한다(sh 스텝이 `@tmp` 를 만든다).

- 접속: http://127.0.0.1:8081 (admin / .env 의 JENKINS_ADMIN_PASSWORD)
- 최초 1회는 플러그인 설치 후 재시작해야 JCasC 가 잡을 만든다.
- 호스트 크론에 08:50 백업 게시(`publish_feed_github.py`)가 걸려 있어 Jenkins 장애 시에도 피드는 나간다(중복 게시는 스크립트가 `unchanged` 로 걸러낸다).

## 트러블슈팅 기록

- JCasC 에 `jobs:` 를 넣으면 `UnknownConfiguratorException: No configurator found for root element: jobs` 로 부팅 실패(job-dsl 필요) → init.groovy.d 로 이전.
- `timestamps()` 옵션은 timestamper 플러그인 필요 → 제거.
- `$(dirname)` 규칙: sh 스텝이 `/work/analyist_dd@tmp` 를 만들므로 `/work` 소유권이 jenkins 여야 한다(AccessDenied).
- 파라미터 잡은 `/build` 가 아니라 `/buildWithParameters` 로 트리거해야 400 이 안 난다.

## 운영 규칙

- 게시 실패는 조용히 넘기지 않는다: Jenkins 빌드 실패 = 그날 고객 거래 없음.
- 번들 업데이트는 `VERSION` 파라미터를 넣은 수동 빌드로만 한다(장중 금지).
- Jenkins 홈 삭제 시 잡·자격증명이 사라진다. 백업 대상은 `deploy/jenkins/home/`.

# Jenkins — 피드 게시 오케스트레이션

호스트 크론 대신 Jenkins 가 08:40(KST) 에 `feed-publish` 파이프라인을 돌려
analyist_dd 피드를 `trader-feed` 저장소에 커밋·푸시한다. 실패하면 고객 PC 는 fail-closed 로 거래하지 않는다.

## 구조

    deploy/jenkins/
      docker-compose.yml   Jenkins LTS (127.0.0.1:8081, 메모리 1.2GB 제한)
      casc.yaml            JCasC: 관리자 계정, GitHub PAT 자격증명, 잡 2개
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
| `feed-publish` | 평일 08:40 KST | 피드 검증 → 게시 → (VERSION 지정 시) 코드 번들 |
| `feed-publish-manual` | 수동 | 같은 파이프라인, ALLOW_STALE 기본 true |

## 기동

    cd deploy/jenkins
    docker compose up -d
    docker exec analyist_jenkins jenkins-plugin-cli --plugins configuration-as-code workflow-aggregator git credentials-binding timestamper github
    docker compose restart

- 접속: http://127.0.0.1:8081 (admin / .env 의 JENKINS_ADMIN_PASSWORD)
- 최초 1회는 플러그인 설치 후 재시작해야 JCasC 가 잡을 만든다.
- 호스트 크론에 08:50 백업 게시(`publish_feed_github.py`)가 걸려 있어 Jenkins 장애 시에도 피드는 나간다(중복 게시는 스크립트가 `unchanged` 로 걸러낸다).

## 운영 규칙

- 게시 실패는 조용히 넘기지 않는다: Jenkins 빌드 실패 = 그날 고객 거래 없음.
- 번들 업데이트는 `VERSION` 파라미터를 넣은 수동 빌드로만 한다(장중 금지).
- Jenkins 홈 삭제 시 잡·자격증명이 사라진다. 백업 대상은 `deploy/jenkins/home/`.

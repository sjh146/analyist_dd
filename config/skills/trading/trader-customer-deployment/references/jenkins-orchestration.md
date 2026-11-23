# Jenkins 로 피드·코드 게시 자동화

호스트 크론을 Jenkins 로 옮기거나 병행할 때의 확립된 규칙. 전제: 피드 원본이 **호스트 디렉토리에 있고
리포에는 없다** → GitHub Actions 같은 원격 CI 로는 대체할 수 없다. 호스트 경로를 마운트할 수 있는
Jenkins(같은 머신)나 호스트 크론만 가능하다.

## 잡 구성

- JCasC 의 `jobs:` 루트는 **job-dsl 플러그인**이 있어야 한다(없으면 `UnknownConfiguratorException: No configurator found for root element: jobs` 로 부팅 실패).
  플러그인을 늘리지 않으려면 `init.groovy.d/*.groovy` 로 `WorkflowJob` 을 만들고 `CpsFlowDefinition` 에 파일 본문을 넣는다.
- 잡 파라미터는 init 스크립트에서 `ParametersDefinitionProperty` 로 붙인다. 파이프라인 본문은 기동 시 파일에서 읽으므로
  **Jenkinsfile 을 고치면 컨테이너 재시작**이 필요하다.
- 스케줄은 `TimerTrigger('TZ=Asia/Seoul\n40 8 * * 1-5')` 처럼 TZ 를 문자열에 포함한다.

## 이미지·권한 (막히는 순서대로)

1. `python3` 가 이미지에 없다 → Dockerfile 로 `apt-get install -y python3` 후 `USER jenkins`.
2. `sh` 스텝은 작업 디렉토리 옆에 `<dir>@tmp` 를 만든다 → 마운트한 상위 디렉토리(예: `/work`)가 **jenkins 소유**여야 한다.
   아니면 `AccessDeniedException: /work/xxx@tmp`. Dockerfile 에서 `mkdir -p /work && chown jenkins:jenkins /work`.
3. 볼륨 홈이 root 소유면 「missing rw permissions on JENKINS_HOME」으로 무한 재시작 → `alpine` 컨테이너로 `chown -R 1000:1000`.
4. 플러그인 설치: `jenkins-plugin-cli --plugins ... -d /var/jenkins_home/plugins`.
   `--output` 은 **출력 포맷 플래그**지 디렉토리가 아니다. 컨테이너가 떠 있으면 락 경합으로 멈추므로 **정지 후** 설치한다.
   회선이 느리면(20KB/s 수준) 의존성 트리 30MB 에 20~30분 걸린다 — 멈춘 것과 구분하려면 직접 `curl` 로 속도를 재본다.

## 파이프라인 함정

- `options { timestamps() }` 는 timestamper 플러그인 필요 — 없으면 컴파일 실패.
- 빈 환경변수를 `set -u` 스크립트에서 `$VAR` 로 쓰면 터진다(`VAR: parameter not set`). Groovy `environment {}` 의 빈 문자열은 export 되지 않을 수 있으니
  `withEnv(["STALE=${params.X ? '--allow-stale' : ''}"])` 로 감싸고 `set -e` 만 쓴다.
- **토큰을 git remote URL 에 박지 않는다**(`.git/config`·프로세스 인자에 남는다). 단계 안에서 `remote set-url` 로 넣고,
  `post { always { ... } }` 에서 깨끗한 URL 로 되돌린다. 장기적으로는 `http.extraheader` 방식.
- 파라미터 잡 트리거: `/job/<name>/build` 는 **400**, `/buildWithParameters` + crumb 헤더 + **cookie jar** 가 필요하다(`201`).
  crumb 만 보내고 쿠키를 안 들고 가면 403.
- 게시 실패는 “그날 거래 없음”으로 이어지므로, 호스트 크론을 **백업 경로**로 남기고 게시 스크립트를 멱등(내용 해시 동일 시 무시)으로 만든다.

## 검증

- API 로 `jobs[name]`, `lastBuild[number,result]`, `consoleText` 를 읽어 성공을 확인한다(UI 눈으로 대신하지 않는다).
- `config.xml` 에서 트리거 spec 이 실제로 들어갔는지 확인한다.

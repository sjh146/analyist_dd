# Jenkins(도커)로 호스트 디렉터리 게시하기

목적: 리포지토리에 없는 로컬 파일(발행 피드 등)을 CI 스케줄로 처리. Jenkins 가 호스트 디렉터리를 직접 보게 한다.

## 컴포우즈 골격

    services:
      jenkins:
        build: { context: ., dockerfile: Dockerfile }   # python3 포함
        image: analyzer_jenkins:local
        ports: ["127.0.0.1:8081:8080", "127.0.0.1:50001:50000"]
        environment: [TZ=Asia/Seoul, CASC_JENKINS_CONFIG=/var/jenkins_home/casc.yaml]
        volumes:
          - ./home:/var/jenkins_home
          - <repo>:/work/repo
          - <feed-repo>:/work/feed
          - ./init.groovy.d:/var/jenkins_home/init.groovy.d:ro

Dockerfile: `FROM jenkins/jenkins:lts-jdk17` → `apt-get install -y python3 git` → `mkdir -p /work && chown jenkins /work`.

## 함정 (전부 실측)

| 증상 | 원인·해결 |
|---|---|
| `AccessDeniedException: /work/repo@tmp` | `sh` 스텝이 작업폴더 옆에 `@tmp` 를 만든다 → `WORKDIR` 소유자를 jenkins(1000)로. 볼륨 부모 디렉터리도 이미지에서 만들어야 한다 |
| `python3: command not found` | 공식 이미지에 없음 → Dockerfile 에서 설치 |
| `UnknownConfiguratorException: ... root element: jobs` | JCasC 의 `jobs:` 는 job-dsl 플러그인 필요 → `init.groovy.d/*.groovy` 로 잡 생성하는 게 빠름 |
| `Invalid option type "timestamps"` | timestamper 플러그인 미설치 |
| 잡 트리거 `POST /build` 가 400/403 | 파라미터 잡은 `/buildWithParameters` + crumb 쿠키/헤더가 필요. `http.cookiejar` 로 세션 유지해서 crumb 획득 → 헤더 전달 |
| 플러그인 설치가 끝나지 않음 | 실행 중인 Jenkins 에서 설치하면 락/경합한다. **컨테이너 정지 후** `docker run --rm -v $PWD/home:/var/jenkins_home jenkins/jenkins:lts-jdk17 jenkins-plugin-cli --plugins ... -d /var/jenkins_home/plugins`, 그 다음 `chown -R 1000:1000 home` 후 기동. 회선이 느리면 20~30분 걸린다(진행 상황은 `/tmp` 의 다운로드 디렉터리와 `docker images`) |
| 파이프라인이 비밀번호 없이 레포지토리에 push 못함 | 컴테이너에 호스트 git 자격증명이 없다 → `git remote set-url` 에 토큰 URL 을 넣고(로그에 남지 않게 `set +x`), `post { always }` 에서 원래 URL 로 복구 |
| 환경번수가 `parameter not set` | 빈 문자열 환경번수는 `set -u` 와 만나면 죽는다 → `withEnv(["FLAG=${params.X ? '--flag' : ''}"])` 로 넣고 `set -e` 만 쓴다 |

## 잡 생성 (init.groovy.d)

    def p = jenkins.createProject(WorkflowJob.class, 'feed-publish')
    p.setDefinition(new CpsFlowDefinition(new File('/var/jenkins_home/ci/Jenkinsfile').text, true))
    p.addProperty(new ParametersDefinitionProperty(
      new BooleanParameterDefinition('ALLOW_STALE', false, '...'),
      new StringParameterDefinition('VERSION', '', '...')))
    p.addTrigger(new TimerTrigger('TZ=Asia/Seoul\n40 8 * * 1-5'))

스크립트를 수정하면 잡을 다시 만들어야 반영된다(정의가 텍스트로 저장됨) → 컨테이너 재시작.

## 역할 분담

- Jenkins: 스케줄·이력·수동 재실행·로그 보존
- 호스트 크론: 같은 게시를 5~10분 뒤 백업으로 돌린다(게시가 멱등이면 중복은 `unchanged` 로 끝난다)

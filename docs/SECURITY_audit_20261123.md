# 보안 점검 결과 (2026-11-23)

점검 방식: 저장소 자체 시크릿 스캔 + strix(오픈소스 AI 펜테스트, deepseek-chat, quick 모드) 코드 감사. strix 보고는 **원문 주장을 저장소에서 직접 재확인**한 뒤 채택했다.

## 1. 실제 유출 (조치 완료)

- **DB 비밀번호가 공개 저장소에 노출.** `docs/spec_R12.md.stderr.oc`(에이전트 실행 로그)에 `POSTGRES_PASSWORD` 실값이 포함된 채 2026-09-25 커밋(`60b79bf`)됨. `analyist_dd` 저장소는 PUBLIC 상태였다.
- 조치: 비밀번호 회전(ALTER USER + `.env` 갱신 + 컨테이너 18개 재생성), 유출 아티팩트 4개 untrack, `*.oc`·`*.stderr*` ignore, 재발 방지 테스트 `tests/test_no_secrets.py`(4건) 추가.
- `.env` 는 히스토리에 커밋된 적 없음. KIS·DART·DeepSeek 키도 히스토리 검사에서 미검출.
- 잔여: 공개 히스토리에 옛 비밀번호 문자열이 남아 있다(값은 이미 무효). 이력 재작성은 별도 승인 사항.

## 2. strix 발견 (재확인 후 채택)

| 심각도 | 내용 | 검증 | 조치 |
|---|---|---|---|
| HIGH (CVSS 8.8) | 메인 compose 의 레거시 `jenkins` 서비스가 `/var/run/docker.sock` 을 마운트하고 8080·50000 을 `0.0.0.0` 에 공개 → CI 코드 실행 시 호스트 root 등가 | `docker-compose.yml` 소켓 바인드 확인, `docker ps` 에서 `analyist_dd-jenkins-1` 이 2일간 0.0.0.0:8080 노출 확인 | 서비스 블록 제거 + 컨테이너 삭제. 신규 Jenkins 는 `127.0.0.1:8081`, 소켓 미마운트 |
| LOW (CVSS 3.6) | 레거시 JENKINS_ADMIN_PASSWORD 가 env 로 전달되지 않아 기동마다 랜덤 비밀번호 → 관리자 접근 상실 | `config/jenkins/init.groovy` 가 env 폴백 + compose 에 environment 부재 | 서비스 제거로 소멸 |

strix 보고서의 수정안 중 `ml_pipeline.groovy`·`ml_infinite_loop.groovy`·`plugins.txt(jobs)` 는 저장소에 없는 파일이었다(환각). 핵심 지적인 소켓·포트 노출은 실재하여 그대로 반영했다.

## 3. 추가 발견 (직접 점검)

컨테이너 포트가 모두 `0.0.0.0` 에 열려 있었다: redis 6379, neo4j 7474/7687/2004, grafana 3000, prometheus 9090, api_gateway 8000, postgres-exporter 9187, redis-exporter 9121, postgres 5434.

- 조치: 전부 `127.0.0.1:` 로 바인딩(compose 수정 후 재생성). 확인: `ss -ltn` 에 남은 0.0.0.0 은 피드 서버 8090 하나(Windows 트레이더가 읽는 경로).
- 검증: 컨테이너 18개 Up, DB 261 테이블 접속, 피드 200.

## 4. 남은 위험 (권고)

1. `pg_hba.conf` 가 `127.0.0.1/32 trust` → WSL 내부 프로세스는 비밀번호 없이 DB 접속 가능. 로컬 신뢰 모델을 유지할지, `scram-sha-256` 으로 올릴지 결정 필요(호스트 스크립트 영향 있음).
2. 피드 서버(8090)는 0.0.0.0 유지 중. 필요 시 Windows 방화벽으로 WSL 대역만 허용.
3. GitHub 저장소에 남은 옛 비밀번호 문자열(무효값) — 이력 정리 여부 결정.
4. Jenkins 관리자 비밀번호·GH 토큰이 `deploy/jenkins/.env`(gitignore) 에 평문. 파일 권한 600 유지, 백업 시 제외.
5. 공개 저장소에 데이터 파일 280개(ETF 프리미엄·공시 이력) 추적 중 — 자격증명은 아니지만 사업 데이터 노출 범위 검토 필요.

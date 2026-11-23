#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo ".env 없음 (JENKINS_ADMIN_PASSWORD, GH_TOKEN 필요)" >&2
  exit 2
fi

echo "[1/5] 기존 컨테이너 정지"
docker compose down >/dev/null 2>&1 || true

echo "[2/5] 플러그인 설치 (회선이 느리면 20~30분)"
docker run --rm -v "$PWD/home:/var/jenkins_home" jenkins/jenkins:lts-jdk17 \
  jenkins-plugin-cli --plugins workflow-aggregator configuration-as-code credentials-binding \
  -d /var/jenkins_home/plugins

echo "[3/5] 홈 소유권 정리"
docker run --rm -v "$PWD/home:/h" alpine:3.20 chown -R 1000:1000 /h

echo "[4/5] 기동"
docker compose up -d

PW="$(grep '^JENKINS_ADMIN_PASSWORD=' .env | cut -d= -f2-)"
for _ in $(seq 1 60); do
  CODE="$(curl -s -o /dev/null -w '%{http_code}' --noproxy '*' --max-time 5 http://127.0.0.1:8081/login || true)"
  [ "$CODE" = "200" ] && break
  sleep 5
done

echo "[5/5] 잡 확인"
curl -s --noproxy '*' -u "admin:${PW}" --max-time 10 \
  "http://127.0.0.1:8081/api/json?tree=jobs[name,url]" || true
echo
echo "완료: http://127.0.0.1:8081 (admin)"

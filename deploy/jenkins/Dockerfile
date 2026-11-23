FROM jenkins/jenkins:lts-jdk17

USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 ca-certificates git \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p /work && chown -R jenkins:jenkins /work
USER jenkins

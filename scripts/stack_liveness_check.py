#!/usr/bin/env python3
"""스택 컨테이너 상주 감시 — '컨테이너가 내려갔다'를 **결함 수**로 판정한다.

WHY(실측 2026-11-21 18:47 KST): WSL 재부팅 + `docker compose down` 이후 앱 컨테이너 14개가
사라진 상태에서 DQ 스냅샷은 (a) Prometheus 가 없어 600초 타임아웃(rc=99)으로 **판정 없이** 끝났고,
(b) 스택이 부분 복구된 19:02 스냅샷은 `rc=3` 이었지만 **원인은 착시 피처 61 하나뿐**이었다 —
news-analyzer·api-gateway·job-runner 가 죽어 있는데 DQ 는 그 사실을 말하지 않는다(뉴스 신선도
지표는 24시간 완충이 있어 당일은 정상으로 보인다). 즉 '수집·서빙 컨테이너가 없다'는 사실이
어떤 수집 신호에도 안 걸린다 — 크론 소실과 같은 함정(스케줄이 돈다를 전제)의 컨테이너 판이다.

판정: **핵심 서비스 중 실행 중이 아닌 개수**. 0 = 정상. docker CLI 를 못 쓰면 999(판정 불가 = 실패).
복구: `--heal` 이면 빠진 서비스를 `docker compose up -d --no-build` 로 되살린다(이미지가 없으면
그 사실을 그대로 보고하고 사람/엔지니어 판단으로 넘긴다 — 여기서 몇십 분짜리 빌드를 시작하지 않는다).

출력 계약: 마지막 수치가 결함 수다(researcher_cycle.eval_check 가 마지막 숫자를 읽는다).
"""
import argparse
import os
import subprocess
import sys

PROJ = os.environ.get("RES_PROJ", "/home/jhshi/analyist_dd")

# 데이터 경로·모니터링에 직결되는 서비스만 센다(compose service 이름 기준).
# jenkins 는 개발 도구라 제외 — 컨테이너 내부 좀비가 상시 조건이라 판정에 넣으면 잡음이 된다.
CORE_SERVICES = (
    "postgres",
    "redis",
    "prometheus",
    "postgres-exporter",
    "redis-exporter",
    "news-analyzer",
    "krx-collector",
    "yfinance-collector",
    "stock-vectorizer",
    "job-runner",
    "api-gateway",
    "strategy-agents",
    "xgboost-ml",
)

UNAVAILABLE = 999


def missing_services(running, core=CORE_SERVICES):
    """실행 중 서비스 집합 → 빠진 핵심 서비스 목록(정렬). 순수 함수(테스트 대상)."""
    return sorted(set(core) - set(running))


def parse_services(text):
    """`docker ps --format '{{.Label "com.docker.compose.service"}}'` 출력 → 서비스 집합."""
    out = set()
    for line in (text or "").splitlines():
        name = line.strip()
        if name:
            out.add(name)
    return out


def running_services(proj=PROJ):
    """docker ps 로 실행 중 서비스 집합. docker 를 못 쓰면 None(=판정 불가)."""
    try:
        p = subprocess.run(
            ["docker", "ps", "--format", '{{.Label "com.docker.compose.service"}}'],
            capture_output=True, text=True, timeout=60, cwd=proj)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    return parse_services(p.stdout)


def heal(missing, proj=PROJ):
    """빠진 서비스를 기동 시도(빌드는 하지 않는다). 실패 사유는 그대로 돌려준다."""
    if not missing:
        return ""
    try:
        p = subprocess.run(["docker", "compose", "up", "-d", "--no-build", *missing],
                           capture_output=True, text=True, timeout=300, cwd=proj)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"복구 실패: {exc}"
    if p.returncode != 0:
        tail = (p.stderr or p.stdout or "").strip().splitlines()
        return f"복구 실패(rc={p.returncode}): {tail[-1][:160] if tail else ''}"
    return "복구 시도 완료"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--heal", action="store_true", help="빠진 핵심 서비스를 기동 시도한다")
    ap.add_argument("--proj", default=PROJ)
    a = ap.parse_args(argv)

    running = running_services(a.proj)
    if running is None:
        print("docker CLI 사용 불가 — 컨테이너 상태 판정 불가(실패로 센다)")
        print(UNAVAILABLE)
        return 0

    missing = missing_services(running)
    print(f"핵심 컨테이너 실행 {len(set(CORE_SERVICES) - set(missing))}/{len(CORE_SERVICES)}")
    if missing:
        print("미가동: " + ", ".join(missing))

    if a.heal and missing:
        print(heal(missing, a.proj))
        running = running_services(a.proj) or set()
        missing = missing_services(running)
        if missing:
            print("복구 후에도 미가동: " + ", ".join(missing))

    print(f"결함 수(미가동 핵심 컨테이너) = {len(missing)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

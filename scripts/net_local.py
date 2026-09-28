"""호스트 스크립트용: 로컬/사설망 요청은 프록시를 타지 않게 한다.

WHY (실측 2026-09-28): 이 WSL 셸과 Hermes 에이전트 프로세스에는 `https_proxy=http://192.168.196.145:8080`
(휴대폰 프록시)가 들어 있고 `NO_PROXY` 에 localhost 가 없다. 그래서 `127.0.0.1:9090`(Prometheus) ·
`127.0.0.1:8090`(피드) 같은 **로컬 요청이 프록시로 새어나가 502** 를 받는다:

    # 프록시 env 그대로
    [데이터X] 살아있는 피처  없음 … ★ 위반 1: 모니터링 사각지대: 핵심 15/15개 미조회 (전체 nodata 20/20)
    # env 제거
    [·] 살아있는 피처 164 · 죽은 피처 35 · padding 0   (정상)

즉 'Prometheus 가 죽었다'가 아니라 **호스트 스크립트가 자기 프록시에 가로막힌 것**이었다.
트레이더에이전트는 같은 함정을 `trader_core/net.py` 로 고쳤다(루프백·RFC1918 만 프록시 제외).
호스트 스크립트도 같게 맞춘다 — 안 그러면 리서처/리뷰보드가 **없는 장애**를 보고하게 된다.

사용:
    from net_local import opener
    with opener(url).open(url, timeout=20) as r:
        ...
"""
from __future__ import annotations

import ipaddress
import urllib.parse
import urllib.request

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal"}


def is_local(url: str) -> bool:
    """루프백·사설망·링크로컬·도커 내부 이름이면 True."""
    host = (urllib.parse.urlsplit(url).hostname or "").strip("[]")
    if host in LOCAL_HOSTS:
        return True
    if host.endswith(".local") or host.endswith(".internal"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


def opener(url: str):
    """로컬 대상이면 프록시 비활성 opener, 아니면 기본 opener."""
    if is_local(url):
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener()

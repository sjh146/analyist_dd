"""pykrx 자격증명 가드 — KRX 웹 로그인(KRX_ID/KRX_PW)이 없을 때의 실패를 조용히 처리.

배경(2026-11-13 실측): 이 스택의 pykrx 는 지수/투자자/프로그램/공매도 조회 전에 KRX 웹
로그인을 시도한다. 자격증명이 없으면 응답이 로그인 실패 페이지로 오고, pykrx 내부가
``df.loc[ticker, "지수명"]`` 을 읽다 ``KeyError: '지수명'`` 으로 죽는다. 로그에는 스택만
남고 "자격증명이 없다"는 사실은 보이지 않아 원인을 찾기 어렵다.

그래서:
  * 자격증명이 없으면 수집을 **건너뛰고** 한 줄로 안내한다(스팸 방지: 1회만).
  * 호스트 쪽 대체 경로(krx_offline.sh — KRX OpenAPI 키, KIS 러너)가 같은 데이터를 채운다.
"""

import logging
import os

log = logging.getLogger(__name__)

_warned = False


def credentials_present() -> bool:
    """KRX 웹 로그인 자격증명이 환경에 있는지."""
    return bool(os.environ.get("KRX_ID") and os.environ.get("KRX_PW"))


def warn_missing_once() -> None:
    """자격증명 부재를 1회만 경고한다."""
    global _warned
    if _warned:
        return
    _warned = True
    log.warning(
        "KRX_ID/KRX_PW 미설정 — pykrx 기반 KRX 수집(투자자·프로그램·공매도·지수)을 건너뜁니다. "
        "이 데이터는 호스트 경로(krx_offline.sh 의 KRX OpenAPI, KIS 수집기)가 대체 수집합니다. "
        "컨테이너 경로를 살리려면 KRX 웹 계정을 KRX_ID/KRX_PW 로 주입하세요"
        "(KRX_API_KEY 는 OpenAPI 키로 별개입니다)."
    )


def skip_if_unconfigured() -> bool:
    """True 면 호출자가 수집을 건너뛰어야 한다."""
    if credentials_present():
        return False
    warn_missing_once()
    return True

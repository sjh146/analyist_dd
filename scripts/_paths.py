"""저장소 경로 해석 — 하드코딩된 ``/home/jhshi/analyist_dd`` 를 대체한다.

규칙
  * ``PROJ_DIR`` 환경변수가 있으면 그것을 쓴다(다른 머신·다른 체크아웃).
  * 없으면 이 파일의 위치에서 저장소 루트를 유도한다(``scripts/`` 의 부모).
  * 그 둘로도 안 되면 옛 운영 경로로 폴백한다(기존 크론이 깨지지 않게).

사용
    from _paths import proj, proj_dir
    FEED = proj("data", "feed", "screener_latest.json")
    os.chdir(proj_dir())

주의: 컨테이너 안에서는 ``/app`` 이 루트라 이 모듈을 쓰지 않는다(서비스 코드는 자체
설정을 따른다). 호스트 스크립트·크론 래퍼 전용이다.
"""

from __future__ import annotations

import os
from typing import Union

_LEGACY = "/home/jhshi/analyist_dd"


def proj_dir() -> str:
    """저장소 루트 절대경로."""
    override = os.environ.get("PROJ_DIR", "").strip()
    if override:
        return os.path.abspath(override)
    derived = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.isdir(os.path.join(derived, "scripts")):
        return derived
    return _LEGACY


def proj(*parts: Union[str, os.PathLike]) -> str:
    """저장소 루트 기준 경로."""
    return os.path.join(proj_dir(), *(os.fspath(p) for p in parts))

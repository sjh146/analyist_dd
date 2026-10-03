"""테스트용 `app` 패키지 해석 헬퍼.

WHY (2026-10-03 실측): `services/` 아래 **12개 서비스가 각각 `app/` 패키지**를 갖는다
(xgboost-ml · strategy-agents · job-runner · krx-collector …). 한 pytest 세션에서 두 서비스의
`app.*` 를 모두 import 하면 **먼저 import 한 쪽이 `sys.modules['app']` 를 선점**하고, 이후 모듈은
`No module named 'app.training'` / `'app.feature_engine'` 으로 수집 단계에서 죽는다.
(단독 실행은 통과하고 전체 실행만 실패해 원인 파악이 어렵다.)

사용법 — `app.*` 를 import 하기 **직전**에 한 줄:

    from _app_path import force_app
    force_app("xgboost-ml")
    from app.feature_engine.sns_features import SnsFeatures

`tests/__init__.py` 가 있어 tests 는 패키지이고, conftest 가 repo 루트를 sys.path 에 넣으므로
`from tests._app_path import force_app` 또는 `from _app_path import force_app` 둘 다 동작한다.
"""
import importlib
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def force_app(service: str, *, repo_root: str | None = None) -> str:
    """`app` 네임스페이스를 `services/<service>` 로 재바인딩하고 그 경로를 반환한다.

    - 캐시된 `app`/`app.*` 모듈을 버려 다른 서비스의 app 이 남아 있지 않게 한다.
    - 대상 서비스 경로를 sys.path 맨 앞에 둔다(다른 서비스 경로보다 우선).
    """
    root = repo_root or _REPO_ROOT
    path = os.path.join(root, "services", service)
    if not os.path.isdir(os.path.join(path, "app")):
        raise RuntimeError(f"services/{service}/app 이 없다 — 서비스 이름을 확인하라")
    for name in [m for m in list(sys.modules) if m == "app" or m.startswith("app.")]:
        del sys.modules[name]
    for other in [d for d in list(sys.path) if d.endswith(os.path.join("services", "app"))]:
        sys.path.remove(other)
    while path in sys.path:
        sys.path.remove(path)
    sys.path.insert(0, path)
    importlib.invalidate_caches()
    return path

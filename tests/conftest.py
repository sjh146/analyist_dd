"""
conftest.py — blocks broken ROS2 launch_testing pytest plugin.
The system-wide ROS2 plugin crashes pytest at collection time because it
declares a hook signature incompatible with this pytest version.
"""
import sys
import types
import os

# ── `app` 패키지 해석 고정 (2026-10-03) ────────────────────────────────────────────
# WHY: services/ 12곳이 각각 `app/` 패키지를 갖고 있어, 테스트 모듈이 제각각 sys.path 를
# 만지면 **다른 서비스의 app 이 먼저 잡혀** 이후 import 가 `No module named 'app.training'`
# 으로 깨진다(전체 수집에서만 발생 — 단독 실행은 통과해 원인 파악이 어렵다).
# conftest 는 테스트 모듈보다 **먼저** import 되므로 여기서 정답 경로를 최우선으로 꽂는다.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_XGB_ML = os.path.join(_REPO_ROOT, "services", "xgboost-ml")
for _p in (_XGB_ML, _REPO_ROOT):
    if _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)

# 수집 호출 정책(net_guard)을 테스트에서는 끈다.
# WHY: 가드는 호스트별 상태파일을 공유해 **프로세스 간** 간격을 강제한다. 테스트가 그 상태를
# 오염시키거나(가짜 호출이 실 호출수로 집계) 운영 지연(3s/콜)을 물려받으면 스위트가 느려진다.
# 하위 프로세스로 도는 러너까지 덮으려면 env 로 꺼야 한다(상속된다).
os.environ.setdefault("NET_GUARD_DISABLE", "1")

# Pre-block the broken entrypoint modules BEFORE pytest tries to load them.
# This must run at import time (conftest.py is imported early).
_BROKEN = [
    "launch_testing",
    "launch_testing.pytest",
    "launch_testing.pytest.hooks",
    "launch_testing_ros_pytest_entrypoint",
]
for mod_name in _BROKEN:
    if mod_name not in sys.modules:
        fake = types.ModuleType(mod_name)
        fake.__path__ = []  # make it a package so importlib doesn't recurse
        sys.modules[mod_name] = fake

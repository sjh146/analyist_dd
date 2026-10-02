"""
conftest.py — blocks broken ROS2 launch_testing pytest plugin.
The system-wide ROS2 plugin crashes pytest at collection time because it
declares a hook signature incompatible with this pytest version.
"""
import sys
import types
import os

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

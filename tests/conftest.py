"""
conftest.py — blocks broken ROS2 launch_testing pytest plugin.
The system-wide ROS2 plugin crashes pytest at collection time because it
declares a hook signature incompatible with this pytest version.
"""
import sys
import types

# Pre-block the broken entrypoint modules BEFORE pytest tries to load them.
# This must run at import time (conftest.py is imported early).
_BROKEN = [
    "launch_testing_ros_pytest_entrypoint",
]
for mod_name in _BROKEN:
    if mod_name not in sys.modules:
        fake = types.ModuleType(mod_name)
        fake.__path__ = []  # make it a package so importlib doesn't recurse
        sys.modules[mod_name] = fake

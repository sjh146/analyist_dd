"""preflight 의 경로 헬퍼 — WSL/Windows 양쪽에서 같은 스크립트가 돌아야 한다.

핵심 규칙: ``C:\\...`` 경로 문자열은 WSL 에서 ``/mnt/c/...`` 로 번역해야
``os.path.isfile`` 이 참이 된다(Windows 인터프리터 실행은 번역 없이도 된다).
"""

import importlib.util
import os
import unittest

MODULE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                           "tools", "preflight.py")


def load_module():
    spec = importlib.util.spec_from_file_location("trader_preflight", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


class PreflightPathTest(unittest.TestCase):
    def setUp(self):
        self.mod = load_module()

    def test_windows_path_is_translated_under_wsl(self):
        if os.name == "nt":
            self.assertEqual(self.mod.win_path(r"C:\Users\x\python.exe"), r"C:\Users\x\python.exe")
            return
        self.assertEqual(self.mod.win_path(r"C:\Users\x\python.exe"), "/mnt/c/Users/x/python.exe")

    def test_plain_paths_are_untouched(self):
        self.assertEqual(self.mod.win_path("/home/x/python3"), "/home/x/python3")
        self.assertEqual(self.mod.win_path("python.exe"), "python.exe")

    def test_tasklist_path_matches_platform(self):
        path = self.mod.tasklist_path()
        if os.name == "nt":
            self.assertTrue(path.endswith("tasklist.exe"))
        else:
            self.assertEqual(path, "/mnt/c/Windows/System32/tasklist.exe")

    def test_is_wsl_returns_bool(self):
        self.assertIsInstance(self.mod.is_wsl(), bool)


if __name__ == "__main__":
    unittest.main()

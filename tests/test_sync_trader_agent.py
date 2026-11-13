"""sync_trader_agent 의 비교/복사 로직 — 사본 두 개의 차이를 정확히 세는지.

이 스크립트는 라이브 매매 사본에 파일을 쓸 수 있으므로, '무엇을 복사할지' 판정을
테스트로 고정한다(벤더 샘플 제외, 추가·변경만, 삭제 없음).
"""

import importlib.util
import os
import tempfile
import unittest

MODULE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "scripts", "sync_trader_agent.py")


def load_module():
    spec = importlib.util.spec_from_file_location("sync_trader_agent", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


class CompareTest(unittest.TestCase):
    def setUp(self):
        self.mod = load_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.a = os.path.join(self.tmp.name, "repo")
        self.b = os.path.join(self.tmp.name, "live")
        for root in (self.a, self.b):
            os.makedirs(root)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, root, rel, text):
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def test_classifies_only_and_different(self):
        self.write(self.a, "same.py", "x")
        self.write(self.b, "same.py", "x")
        self.write(self.a, "only_repo.py", "r")
        self.write(self.b, "only_live.py", "l")
        self.write(self.a, "changed.py", "repo")
        self.write(self.b, "changed.py", "live")
        diff = self.mod.compare(self.a, self.b, {"same.py", "only_repo.py", "changed.py"},
                                {"same.py", "only_live.py", "changed.py"})
        self.assertEqual(diff["same"], 1)
        self.assertEqual(diff["only_a"], ["only_repo.py"])
        self.assertEqual(diff["only_b"], ["only_live.py"])
        self.assertEqual(diff["different"], ["changed.py"])

    def test_vendor_is_excluded_by_default(self):
        rel = "creon_extracted_v1/vendor_sample.py"
        self.write(self.a, rel, "vendor")
        diff = self.mod.compare(self.a, self.b, {rel}, set())
        self.assertEqual(diff["only_a"], [])
        diff_with = self.mod.compare(self.a, self.b, {rel}, set(), include_vendor=True)
        self.assertEqual(diff_with["only_a"], [rel])

    def test_copy_creates_dirs_and_never_deletes(self):
        self.write(self.a, "pkg/new.py", "new")
        self.write(self.b, "keep.py", "keep")
        copied = self.mod.copy_files(self.a, self.b, ["pkg/new.py"])
        self.assertEqual(copied, 1)
        self.assertTrue(os.path.isfile(os.path.join(self.b, "pkg", "new.py")))
        self.assertTrue(os.path.isfile(os.path.join(self.b, "keep.py")))

    def test_missing_source_is_skipped(self):
        copied = self.mod.copy_files(self.a, self.b, ["nope.py"])
        self.assertEqual(copied, 0)


if __name__ == "__main__":
    unittest.main()

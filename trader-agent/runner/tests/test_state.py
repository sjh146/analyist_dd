"""State-file round-trip, halt/resume flags and atomic-write tests."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from runner.state import StateStore, default_state


class StateStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="runner-state-")
        self.path = os.path.join(self._tmp, "loop_state.json")
        self.store = StateStore(self.path)

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_missing_file_reads_default(self) -> None:
        self.assertFalse(self.store.exists())
        state = self.store.read()
        self.assertFalse(state["loop"]["halt"])
        self.assertIsNone(state["daily"]["start_equity"])
        self.assertEqual(state["daily"]["entries"], 0)
        # read() never creates the file
        self.assertFalse(self.store.exists())

    def test_round_trip_preserves_nested_shape(self) -> None:
        state = self.store.read()
        state["loop"]["halt"] = True
        state["loop"]["halt_reason"] = "daily_loss_limit"
        state["daily"]["start_equity"] = 12345.0
        state["daily"]["entries"] = 2
        state["check"]["consecutive_failures"] = 3
        self.store.write(state)

        loaded = StateStore(self.path).read()
        self.assertTrue(loaded["loop"]["halt"])
        self.assertEqual(loaded["loop"]["halt_reason"], "daily_loss_limit")
        self.assertAlmostEqual(loaded["daily"]["start_equity"], 12345.0)
        self.assertEqual(loaded["daily"]["entries"], 2)
        self.assertEqual(loaded["check"]["consecutive_failures"], 3)
        # nested defaults survive partial files
        StateStore(self.path).write({"loop": {"halt": False}})
        again = StateStore(self.path).read()
        self.assertFalse(again["loop"]["halt"])
        self.assertEqual(again["daily"]["entries"], 0)

    def test_halt_resume_flag_toggle(self) -> None:
        state = self.store.read()
        self.assertFalse(state["loop"]["halt"])
        state["loop"]["halt"] = True
        state["loop"]["halt_reason"] = "manual halt"
        self.store.write(state)
        self.assertTrue(StateStore(self.path).read()["loop"]["halt"])
        state["loop"]["halt"] = False
        state["loop"]["halt_reason"] = ""
        self.store.write(state)
        self.assertFalse(StateStore(self.path).read()["loop"]["halt"])

    def test_write_is_atomic_and_leaves_no_tmp(self) -> None:
        for i in range(25):
            state = self.store.read()
            state["daily"]["entries"] = i
            self.store.write(state)
        leftovers = [
            name for name in os.listdir(self._tmp)
            if ".tmp." in name or name.endswith(".tmp")
        ]
        self.assertEqual(leftovers, [])
        with open(self.path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        self.assertEqual(payload["daily"]["entries"], 24)

    def test_default_state_is_deep_copied(self) -> None:
        first = default_state()
        second = default_state()
        first["daily"]["entries"] = 99
        self.assertEqual(second["daily"]["entries"], 0)


if __name__ == "__main__":
    unittest.main()

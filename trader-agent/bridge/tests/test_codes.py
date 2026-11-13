"""Unit tests for bridge.codes code normalisation helpers."""

from __future__ import annotations

import unittest

from bridge.codes import (
    InvalidCodeError,
    is_plain_code,
    is_trade_code,
    to_plain_code,
    to_trade_code,
)


class CodeNormalisationTest(unittest.TestCase):
    def test_plain_to_trade(self) -> None:
        self.assertEqual(to_trade_code("005930"), "A005930")

    def test_trade_to_trade_is_idempotent(self) -> None:
        self.assertEqual(to_trade_code("A005930"), "A005930")

    def test_trade_to_plain(self) -> None:
        self.assertEqual(to_plain_code("A005930"), "005930")

    def test_plain_to_plain_is_idempotent(self) -> None:
        self.assertEqual(to_plain_code("005930"), "005930")

    def test_whitespace_and_lowercase_tolerated(self) -> None:
        self.assertEqual(to_trade_code("  a005930 "), "A005930")
        self.assertEqual(to_plain_code(" a005930 "), "005930")

    def test_foreign_prefix_passthrough(self) -> None:
        # Non-stock prefixes (indices/futures like Q530031) are left untouched.
        self.assertEqual(to_trade_code("Q530031"), "Q530031")
        self.assertEqual(to_plain_code("Q530031"), "Q530031")

    def test_invalid_raises(self) -> None:
        for bad in ("", "12345", "1234567", "A12345", "ABCDEF", "A00593x"):
            with self.assertRaises(InvalidCodeError):
                to_trade_code(bad)
            with self.assertRaises(InvalidCodeError):
                to_plain_code(bad)

    def test_predicates(self) -> None:
        self.assertTrue(is_plain_code("005930"))
        self.assertFalse(is_plain_code("A005930"))
        self.assertTrue(is_trade_code("A005930"))
        self.assertFalse(is_trade_code("005930"))


if __name__ == "__main__":
    unittest.main()

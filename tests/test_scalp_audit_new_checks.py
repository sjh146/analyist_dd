"""scalp_audit 신규 감사 2건 — halt 중 보유, 청산 실패 반복 (2026-11-26 사고 대응)."""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("scalp_audit", ROOT / "scripts" / "scalp_audit.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_halt_with_positions_reports():
    state = {"loop": {"halt": True, "halt_reason": "check failures 3: bridge health: not connected"}}
    detail = mod.halt_with_positions(state, [{"code": "069510"}])
    assert "069510" in detail and "청산" in detail


def test_halt_without_positions_is_quiet():
    state = {"loop": {"halt": True, "halt_reason": "x"}}
    assert mod.halt_with_positions(state, []) == ""


def test_positions_without_halt_is_quiet():
    assert mod.halt_with_positions({"loop": {"halt": False}}, [{"code": "069510"}]) == ""


def test_exit_blocks_require_three_hits():
    lines = ["  - exit failed 069510: no quote", "  - exit failed 069510: no quote"]
    assert mod.parse_exit_blocks(lines) == {}
    lines.append("  - exit failed 069510: no quote")
    assert mod.parse_exit_blocks(lines) == {"069510": 3}


def test_exit_blocks_ignore_success_lines():
    lines = ["  - exit 069510 (max_hold)", "monitor: 069510: holding"] * 5
    assert mod.parse_exit_blocks(lines) == {}

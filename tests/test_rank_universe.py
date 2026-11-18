"""tests/test_rank_universe.py — 크레온 상승률 랭킹 → 유니버스 압축 파일 (6 tests).

docs/spec_rank_universe.md §4 계약:
① 코드 정규화(중복·A접두·비숫자 제거) ② 랭킹 순서 보존 ③ 브리지 예외 → ok:false + exit 0
④ ok:false 응답 → ok:false + codes:[] ⑤ 멱등(codes/items 동일) ⑥ --strict 실패 시 exit 1
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import rank_universe as ru  # noqa: E402


def _payload(rows, ok=True):
    return {"ok": ok, "rank": rows, "pages": 1, "ts": 1.0}


def test_normalize_code():
    assert ru.normalize_code("A003780") == "003780"
    assert ru.normalize_code(" 003780 ") == "003780"
    assert ru.normalize_code(3780) == "003780"
    assert ru.normalize_code("ABCDEF") == ""
    assert ru.normalize_code(None) == ""


def test_parse_rank_order_and_dedupe():
    payload = _payload([
        {"code": "003780", "name": "진양산업", "rank": 1, "diff": 1320, "volume": 10},
        {"code": "A003780", "name": "중복", "rank": 2, "diff": 1, "volume": 1},
        {"code": "186230", "name": "그린플러스", "rank": 3, "diff": 735, "volume": 20},
        {"code": "XXXXXX", "name": "불량", "rank": 4, "diff": 0, "volume": 0},
    ])
    items, codes = ru.parse_rank(payload)
    assert codes == ["003780", "186230"]          # 순서 보존 + 중복/불량 제거
    assert [i["rank"] for i in items] == [1, 3]


def test_bridge_exception_is_fail_open(tmp_path, monkeypatch):
    out = tmp_path / "u.json"

    def boom(url, timeout=20.0):
        raise OSError("connection refused")

    monkeypatch.setattr(ru, "fetch_rank", boom)
    rc = ru.main(["--out", str(out), "--quiet"])
    data = json.loads(out.read_text(encoding="utf-8"))
    assert rc == 0                     # fail-open: 크론 체인을 끊지 않는다
    assert data["ok"] is False and data["codes"] == []
    assert "connection refused" in data["reason"]


def test_ok_false_response(tmp_path, monkeypatch):
    out = tmp_path / "u.json"
    monkeypatch.setattr(ru, "fetch_rank",
                        lambda url, timeout=20.0: {"ok": False, "error": {"code": "invalid_input"}})
    rc = ru.main(["--out", str(out), "--quiet"])
    data = json.loads(out.read_text(encoding="utf-8"))
    assert rc == 0 and data["ok"] is False and data["n_codes"] == 0


def test_idempotent_codes(tmp_path, monkeypatch):
    payload = _payload([{"code": "003780", "name": "A", "rank": 1},
                        {"code": "186230", "name": "B", "rank": 2}])
    monkeypatch.setattr(ru, "fetch_rank", lambda url, timeout=20.0: payload)
    outs = []
    for name in ("a.json", "b.json"):
        p = tmp_path / name
        assert ru.main(["--out", str(p), "--quiet"]) == 0
        d = json.loads(p.read_text(encoding="utf-8"))
        outs.append((d["codes"], d["items"]))
    assert outs[0] == outs[1]


def test_strict_mode_exits_nonzero(tmp_path, monkeypatch):
    out = tmp_path / "u.json"
    monkeypatch.setattr(ru, "fetch_rank", lambda url, timeout=20.0: {"ok": True, "rank": []})
    assert ru.main(["--out", str(out), "--quiet", "--strict"]) == 1


def test_pct_bounds_are_integers():
    # 브리지 실측 요구(2026-11-18): pct_from=2.0 → invalid_input
    q = ru.build_query(2.0, 12.0, 7, "up", "0")
    assert "pct_from=2&" in q and "pct_to=12&" in q and "pages=7" in q

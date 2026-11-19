"""tests/test_champion_oos_sessions.py — OOS 세션 지표 정본화 (2 tests).

게이트 B축이 읽는 평면 키(avg_pct/t_stat/n_sessions)와 models 상세가 같은 값인지,
입력 스키마가 다르면 exit 2 로 명확히 실패하는지 고정한다.
"""
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import champion_oos_sessions as cos  # noqa: E402


def _src():
    return {
        "created_at": "2026-11-12T13:31:12",
        "sample": {"n_rows": 2251, "n_sessions": 113, "topk": 3, "exit": "next_open",
                   "fee_round_trip_pct": 0.21, "date_start": "2026-05-26",
                   "date_end": "2026-11-09"},
        "models": {
            "champion": {"train_end": "2026-10-01",
                         "oos": {"n_sessions": 25, "avg_pct": -0.8754, "t_stat": -2.51}},
            "champion_cand": {"train_end": "2026-10-28",
                              "oos": {"n_sessions": 8, "avg_pct": -0.4259, "t_stat": -0.66}},
        },
    }


def test_build_primary_and_flat_keys(tmp_path):
    src = tmp_path / "split.json"
    src.write_text(json.dumps(_src()), encoding="utf-8")
    out = tmp_path / "canon.json"
    assert cos.main(["--src", str(src), "--out", str(out), "--primary", "champion"]) == 0
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["models"]["champion"]["session_expectancy_pct"] == -0.8754
    assert d["avg_pct"] == -0.8754 and d["t_stat"] == -2.51 and d["n_sessions"] == 25
    assert d["primary"] == "champion"


def test_missing_models_key_exits_2(tmp_path):
    src = tmp_path / "bad.json"
    src.write_text(json.dumps({"created_at": "x"}), encoding="utf-8")
    assert cos.main(["--src", str(src), "--out", str(tmp_path / "o.json")]) == 2

#!/usr/bin/env python3
"""회귀 테스트: quant_scoreboard.no_improve — **'연속'과 '누적'을 구분**한다.

배경(실측 2026-09-28 22:41): 종전 구현은 무개선 카운터를 '측정 사이클 수 − 개선 사이클 수'
(누적)로 계산해 놓고 보고 문구에는 **"N사이클 연속 기준선 대비 +0.02 미달"** 이라고 썼다.
그날 저녁 CG20(q0.05 라벨 꼬리)이 로버스트 0.5642 vs 기준선 0.5406 = Δ+0.0236 으로 사전
문턱(+0.02)을 넘겼는데도, 누적값 33 이 그대로 남아 **"33사이클 연속 미달 — 새 레버 필요
(사람 승인 대상)"** 경보가 떴다 → 이미 나온 레버를 '없는 사람 단계'로 올릴 참이었다.

계약:
 - 꼬리(마지막 측정)가 개선이면 연속 미달 = 0, 경보 없음
 - 꼬리에 미달이 3개 연속이면 연속 3, 경보 있음
 - 누적 미달 개수(no_improve_cycles)는 정보로 남는다 — 연속과 다른 값임을 고정한다
 - rc!=0(소실) 사이클은 어느 쪽에도 세지 않는다(측정이 아니다)
"""
import importlib.util
import json
import os

import pytest

SCOREBOARD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "scripts", "quant_scoreboard.py")


def _load():
    spec = importlib.util.spec_from_file_location("quant_scoreboard_under_test", SCOREBOARD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rec(ts, rid, mean=None, rc=0):
    r = {"ts": ts, "id": rid, "rc": rc, "verdict": "신호있음" if mean else "노이즈"}
    if mean is None:
        r["parsed"] = {"error": "no summary"}
    else:
        r["parsed"] = {"per_exp": {"E": {"mean": mean, "std": 0.03, "folds": [mean]}}}
    return r


def _write(tmp_path, recs):
    p = os.path.join(str(tmp_path), "me_ledger.jsonl")
    with open(p, "w", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r) + "\n")
    return p


def test_tail_improvement_clears_alert(tmp_path):
    """마지막 사이클이 문턱을 넘겼으면 연속 미달 0 — 경보가 남으면 안 된다."""
    m = _load()
    base = m.BASELINE_ROBUST
    m.ME_LEDGER = _write(tmp_path, [
        _rec("2026-09-28T18:00:00+09:00", "CG16", base - 0.01),
        _rec("2026-09-28T18:10:00+09:00", "CG17", base - 0.005),
        _rec("2026-09-28T19:25:00+09:00", "CG20", base + m.SIGNAL_DELTA + 0.004),
    ])
    st = m.engineer_stanza()
    assert st["no_improve_streak"] == 0
    assert st["no_improve_cycles"] == 2          # 누적은 2 (정보)
    assert st["last_improve"]["id"] == "CG20"
    assert not [a for a in st["alerts"] if "새 레버" in a]


def test_three_consecutive_misses_alert(tmp_path):
    """꼬리에 미달 3연속이면 경보 — 누적 개수 문구가 아니라 연속을 쓴다."""
    m = _load()
    base = m.BASELINE_ROBUST
    m.ME_LEDGER = _write(tmp_path, [
        _rec("2026-09-28T18:00:00+09:00", "CG16", base + m.SIGNAL_DELTA + 0.004),
        _rec("2026-09-28T18:10:00+09:00", "CG17", base - 0.01),
        _rec("2026-09-28T18:20:00+09:00", "CG18", base - 0.02),
        _rec("2026-09-28T18:30:00+09:00", "CG19", base - 0.03),
    ])
    st = m.engineer_stanza()
    assert st["no_improve_streak"] == 3
    assert st["no_improve_cycles"] == 3
    assert [a for a in st["alerts"] if "새 레버" in a]


def test_lost_cycles_are_not_measured(tmp_path):
    """rc!=0(소실)은 측정이 아니다 — 연속 미달에 세면 경보가 헛돈다."""
    m = _load()
    base = m.BASELINE_ROBUST
    m.ME_LEDGER = _write(tmp_path, [
        _rec("2026-09-28T18:00:00+09:00", "U1", None, rc=124),
        _rec("2026-09-28T18:10:00+09:00", "U1", None, rc=137),
        _rec("2026-09-28T18:20:00+09:00", "CG17", base + m.SIGNAL_DELTA + 0.001),
    ])
    st = m.engineer_stanza()
    assert st["no_improve_streak"] == 0
    assert st["no_improve_cycles"] == 0
    assert st["measured_cycles"] == 1
    assert st["invalid_cycles"] == 2
    assert not [a for a in st["alerts"] if "새 레버" in a]


def test_no_ledger_no_crash(tmp_path):
    """원장이 비어도 죽지 않는다(첫 실행)."""
    m = _load()
    m.ME_LEDGER = _write(tmp_path, [])
    st = m.engineer_stanza()
    assert st["no_improve_streak"] == 0
    assert st["last_improve"] is None

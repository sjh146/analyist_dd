"""redeploy_gate 계약 회귀 — 정책 docs/REDEPLOY_THRESHOLD.md 의 6개 시나리오.

WHY (2026-11-19): 재배포 문턱은 '측정된 양(+) 엣지 없이는 배포하지 않는다'는 정책의
집행 수단이다. 게이트 판정이 뒤집히면 실계좌 배포 결정의 증거가 거짓이 되므로, 정책에
적힌 6개 시나리오를 픽스처로 고정한다:
  ① 표본 < 20 → BLOCK  ② 표본 ≥ 20 & 순수익 > 0 → live pass  ③ OOS 파일 없음 → oos
  pass=false·사유  ④ OOS 세션지표 >0 & t≥1 → PASS  ⑤ t<1 → BLOCK  ⑥ 저널 없음 → exit 2
검증 방식은 '산출물에서 직접'(자기신고 금지) — main() 의 종료 코드와 --json-out 파일
내용을 단언한다. 게이트는 저널을 /tmp 복사 후 mode=ro 로 읽으므로, 테스트는 모듈의
JOURNAL 상수를 픽스처 sqlite 로 monkeypatch 한다. 자기신고(dq_claim → postgres)는
REDEPLOY_GATE_NO_CLAIM=1 로 끈다(테스트는 DB 없이 돌아야 한다).
"""
import importlib.util
import json
import os
import sqlite3
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_gate():
    """scripts/redeploy_gate.py 를 고유 모듈명으로 로드한다(기존 테스트와 같은 관례)."""
    path = os.path.join(_REPO, "scripts", "redeploy_gate.py")
    spec = importlib.util.spec_from_file_location("redeploy_gate_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _fixture_journal(path, rows):
    """게이트가 조회하는 열(exit_ts·exit_reason·pnl)만 가진 최소 trades 테이블."""
    conn = sqlite3.connect(path)
    conn.execute("create table trades (id integer primary key, exit_ts text, "
                 "exit_reason text, pnl real)")
    for i, (exit_ts, reason, pnl) in enumerate(rows, 1):
        conn.execute("insert into trades (id, exit_ts, exit_reason, pnl) "
                     "values (?,?,?,?)", (i, exit_ts, reason, pnl))
    conn.commit()
    conn.close()


# 2026-11-19 실제 저널 3건(실측: n=3 · pnl 합 4,687.9996 → net_pnl 4688.0 · win_pct 66.7)
REAL_THREE = [
    ("2026-11-19T10:05:48.179095+09:00", "tick tp", 2850.0),
    ("2026-11-19T10:17:19.423248+09:00", "tick tp", 2800.0),
    ("2026-11-19T11:35:08.189875+09:00", "tick stop", -962.0004),
]


def _run(mod, monkeypatch, tmp_path, rows=None, session=None, oos_exists=True,
         since="2026-11-19", min_trades=20, journal_path=None):
    """게이트를 실행하고 (종료 코드, 산출물 dict)를 돌려준다."""
    monkeypatch.setenv("REDEPLOY_GATE_NO_CLAIM", "1")
    if journal_path is not None:
        monkeypatch.setattr(mod, "JOURNAL", journal_path)
    elif rows is not None:
        jp = tmp_path / "trade_journal.sqlite3"
        _fixture_journal(str(jp), rows)
        monkeypatch.setattr(mod, "JOURNAL", str(jp))
    oos = tmp_path / "oos_accrual.jsonl"
    if oos_exists:
        with open(oos, "w", encoding="utf-8") as f:
            f.write(json.dumps({"measured_at": "2026-11-18T22:16:47",
                                "model_dir": "app/models/champion",
                                "robust_auc": 0.5039,
                                "source": "cg352_champion_s0.json"},
                               ensure_ascii=False) + "\n")
    argv = ["--since", since, "--min-trades", str(min_trades),
            "--oos-file", str(oos),
            "--json-out", str(tmp_path / "gate.json"), "--quiet"]
    if session is not None:
        sp = tmp_path / "session.json"
        with open(sp, "w", encoding="utf-8") as f:
            json.dump(session, f, ensure_ascii=False)
        argv += ["--session-file", str(sp)]
    rc = mod.main(argv)
    art_path = tmp_path / "gate.json"
    art = json.load(open(art_path, encoding="utf-8")) if art_path.exists() else None
    return rc, art


# ① 표본 < 20 → BLOCK (정책 예시 출력과 같은 수치를 그대로 고정)
def test_sample_below_min_blocks(tmp_path, monkeypatch):
    mod = _load_gate()
    rc, art = _run(mod, monkeypatch, tmp_path, rows=REAL_THREE)
    assert rc == 1
    assert art["live"] == {"n": 3, "net_pnl": 4688.0, "win_pct": 66.7, "pass": False}
    assert art["oos"] == {"session_expectancy_pct": None, "t_stat": None,
                          "source": None, "pass": False, "reason": "측정 불가"}
    assert art["verdict"] == "BLOCK"
    assert "표본 3 < 20" in art["reasons"]
    assert "OOS 세션 지표 없음" in art["reasons"]


# ② 표본 ≥ 20 & 순수익 > 0 → live pass (OOS 는 측정 불가라 전체는 BLOCK)
def test_sample_20_net_positive_live_pass(tmp_path, monkeypatch):
    mod = _load_gate()
    rows = [("2026-11-19T10:00:00+09:00", "tick tp", 1000.0)] * 12 + \
           [("2026-11-19T10:30:00+09:00", "tick stop", -100.0)] * 8
    rc, art = _run(mod, monkeypatch, tmp_path, rows=rows)
    assert rc == 1
    assert art["live"]["n"] == 20
    assert art["live"]["net_pnl"] == 11200.0
    assert art["live"]["pass"] is True
    assert art["verdict"] == "BLOCK"  # oos 측정 불가 → 배포 금지


# ③ OOS 파일 없음 → oos pass=false·사유
def test_missing_oos_file_fails_oos(tmp_path, monkeypatch):
    mod = _load_gate()
    rows = [("2026-11-19T10:00:00+09:00", "tick tp", 1000.0)] * 20
    rc, art = _run(mod, monkeypatch, tmp_path, rows=rows, oos_exists=False)
    assert rc == 1
    assert art["oos"]["pass"] is False
    assert art["oos"]["reason"]
    assert "OOS accrual 파일 없음" in art["reasons"]


# ④ OOS 세션지표 >0 & t≥1 → PASS (실산출물 형식 c_net_expectancy.champion 으로 검증)
def test_oos_session_positive_t_ge_1_pass(tmp_path, monkeypatch):
    mod = _load_gate()
    rows = [("2026-11-19T10:00:00+09:00", "tick tp", 1000.0)] * 20
    session = {"c_net_expectancy": {"champion": {"avg_pct": 1.5, "t_stat": 2.0,
                                                 "n_sessions": 87}}}
    rc, art = _run(mod, monkeypatch, tmp_path, rows=rows, session=session)
    assert rc == 0
    assert art["verdict"] == "PASS"
    assert art["oos"]["pass"] is True
    assert art["oos"]["session_expectancy_pct"] == 1.5
    assert art["oos"]["t_stat"] == 2.0
    assert art["oos"]["source"].endswith("session.json")
    assert art["reasons"] == []


# ⑤ t<1 → BLOCK
def test_oos_t_below_1_blocks(tmp_path, monkeypatch):
    mod = _load_gate()
    rows = [("2026-11-19T10:00:00+09:00", "tick tp", 1000.0)] * 20
    session = {"avg_pct": 1.5, "t_stat": 0.5}
    rc, art = _run(mod, monkeypatch, tmp_path, rows=rows, session=session)
    assert rc == 1
    assert art["verdict"] == "BLOCK"
    assert art["oos"]["pass"] is False
    assert "t 0.50 < 1" in art["oos"]["reason"]
    assert "OOS t 0.50 < 1" in art["reasons"]


# ⑥ 저널 없음 → exit 2(입력 오류)
def test_missing_journal_exit2(tmp_path, monkeypatch):
    mod = _load_gate()
    rc, art = _run(mod, monkeypatch, tmp_path,
                   journal_path=str(tmp_path / "nope.sqlite3"))
    assert rc == 2
    assert art is None

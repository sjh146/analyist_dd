"""feed_feedback 단위 테스트 — docs/spec_feed_feedback.md 계약의 7개 검증.

WHY: 이 스크립트의 출력은 실계좌 손익 → 게이트 제안(min_edge_score)·개선 큐 등록으로
직결된다. 버킷 경계·기대값 계산·제안 규칙이 틀리면 **돈이 걸린 제안이 조용히 틀린 값**으로
나간다(문턱 상향 제안이 실제로는 손실 버킷을 가리키는 유형). 경계값(0-2/3/4/5/6-7,
30/60/90분)과 제안·등록 멱등을 잠근다. DB·실저널 접근 없음 — 전부 합성 입력.
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import feed_feedback as ff  # noqa: E402

KST = timezone(timedelta(hours=9))


def _mk(pnl, fees=0.0, notional=10000, pct=None, hold_min=30.0, edge_score=None,
        edge_factors=None, screener="daytrading", stop_model="fixed", hour=None):
    """_stats/_by_* 입력용 합성 거래. pct 생략 시 notional 에서 계산."""
    if pct is None:
        pct = round(pnl / notional * 100, 2)
    return {"id": None, "pnl": float(pnl), "fees": float(fees),
            "notional": notional, "pct": pct, "hold_min": hold_min,
            "edge_score": edge_score, "edge_factors": edge_factors,
            "screener": screener, "stop_model": stop_model, "hour": hour}


def test_01_stats_hand_computed_5_trades():
    """① 기대값·승률·비용 비중 — 합성 5건 손계산 값과 일치.

    손계산: pnl 합=100원, 3승/5 → 60%. 기대값=20원/건. pct 평균=(1.0-0.5+0.15-0.1+0.1)/5
    =0.13. 평균이익=(100+30+40)/3=56.67, 평균손실=(-50-20)/2=-35.
    비용 비중=100*(10+5+5+5+10)/|(100+10)+(-50+5)+(30+5)+(-20+5)+(40+10)|=3500/255=13.73.
    평균보유=(10+20+30+40+50)/5=30.
    """
    trades = [
        _mk(100.0, fees=10.0, notional=10000, hold_min=10.0),
        _mk(-50.0, fees=5.0, notional=10000, hold_min=20.0),
        _mk(30.0, fees=5.0, notional=20000, hold_min=30.0),
        _mk(-20.0, fees=5.0, notional=20000, hold_min=40.0),
        _mk(40.0, fees=10.0, notional=40000, hold_min=50.0),
    ]
    st = ff._stats(trades)
    assert st == {"n": 5, "win_pct": 60, "expectancy_krw": 20, "expectancy_pct": 0.13,
                  "avg_win": 56.67, "avg_loss": -35, "fee_share_pct": 13.73,
                  "avg_hold_min": 30}, st
    # 표본 없는 평균은 0이 아니라 null(결측을 0으로 메우지 않는다)
    assert ff._stats([_mk(100.0)])["avg_loss"] is None
    assert ff._stats([_mk(-100.0)])["avg_win"] is None


def test_02_edge_score_bucket_boundaries():
    """② edge_score 버킷 경계: 0-2/3/4/5/6-7/None. 범위 밖·비수치는 None(미측정)."""
    assert ff._edge_bucket(0) == "0-2"
    assert ff._edge_bucket(2) == "0-2"
    assert ff._edge_bucket(3) == "3"
    assert ff._edge_bucket(4) == "4"
    assert ff._edge_bucket(5) == "5"
    assert ff._edge_bucket(6) == "6-7"
    assert ff._edge_bucket(7) == "6-7"
    assert ff._edge_bucket(5.0) == "5"          # 저널은 float 로 기록된다(실측 5.0)
    assert ff._edge_bucket(None) == "None"
    assert ff._edge_bucket(8) == "None"         # 범위 밖
    assert ff._edge_bucket(-1) == "None"
    assert ff._edge_bucket("5") == "None"       # 문자열
    assert ff._edge_bucket(True) == "None"      # bool 은 int 아님


def test_03_hold_bucket_boundaries():
    """③ 보유 분 버킷: 30/60/90 경계(≤30/31-60/61-90/>90)."""
    assert ff._hold_bucket(0.0) == "≤30"
    assert ff._hold_bucket(30.0) == "≤30"
    assert ff._hold_bucket(30.1) == "31-60"
    assert ff._hold_bucket(60.0) == "31-60"
    assert ff._hold_bucket(60.1) == "61-90"
    assert ff._hold_bucket(90.0) == "61-90"
    assert ff._hold_bucket(90.1) == ">90"


def test_04_factor_on_off_delta():
    """④ edge_factors on/off 기대값 차이(delta_krw). 한쪽 표본 0이면 null + 사유."""
    trades = [
        _mk(100.0, edge_factors={"sr_breakout": True}),
        _mk(200.0, edge_factors={"sr_breakout": True, "trend_dir": True}),
        _mk(-50.0, edge_factors={"sr_breakout": False}),
        _mk(-100.0, edge_factors={"sr_breakout": False}),
        _mk(-150.0, edge_factors={"sr_breakout": False}),
    ]
    byf = ff._by_factor(trades)
    assert [x["factor"] for x in byf] == ["sr_breakout", "trend_dir"], byf
    sr = byf[0]
    assert sr["on"]["n"] == 2 and sr["on"]["expectancy_krw"] == 150, sr
    assert sr["off"]["n"] == 3 and sr["off"]["expectancy_krw"] == -100, sr
    assert sr["delta_krw"] == 250, sr
    # off 표본만 있으면 on 은 null + 사유(0으로 메우지 않는다)
    solo = ff._by_factor([_mk(-50.0, edge_factors={"vol_fit": False})])
    assert solo[0]["on"] is None and solo[0]["off"] is not None
    assert solo[0]["delta_krw"] is None and solo[0]["reason"]


def test_05_suggestion_rule():
    """⑤ 제안 규칙: n≥min-n 기대값 최대 버킷의 최소 점수 → min_edge_score 제안.

    동률/최고≤0/현행과 동일/표본부족이면 null + 사유(동률이면 현행 유지).
    """
    buckets = [
        {"bucket": "3", "min_score": 3, "n": 15, "expectancy_krw": -100},
        {"bucket": "4", "min_score": 4, "n": 15, "expectancy_krw": 50},
        {"bucket": "5", "min_score": 5, "n": 15, "expectancy_krw": 200},
    ]
    s, why, n = ff._suggest(buckets, 10, 4)
    assert s == 5 and n == 15 and "5" in why, (s, why, n)

    tie = [{"bucket": "4", "min_score": 4, "n": 15, "expectancy_krw": 100},
           {"bucket": "5", "min_score": 5, "n": 15, "expectancy_krw": 100}]
    s, why, _ = ff._suggest(tie, 10, 4)
    assert s is None and "동률" in why, why

    neg = [{"bucket": "4", "min_score": 4, "n": 15, "expectancy_krw": -10},
           {"bucket": "5", "min_score": 5, "n": 15, "expectancy_krw": -50}]
    s, why, _ = ff._suggest(neg, 10, 4)
    assert s is None and "음수" in why, why

    same = [{"bucket": "4", "min_score": 4, "n": 15, "expectancy_krw": 200},
            {"bucket": "5", "min_score": 5, "n": 15, "expectancy_krw": 50}]
    s, why, _ = ff._suggest(same, 10, 4)
    assert s is None and "현행 문턱" in why, why

    few = [{"bucket": "4", "min_score": 4, "n": 3, "expectancy_krw": 200}]
    s, why, n = ff._suggest(few, 10, 4)
    assert s is None and n is None and "표본 부족" in why, (s, why, n)
    # None 버킷(min_score 없음)은 제안 대상이 아니다
    none_b = [{"bucket": "None", "min_score": None, "n": 40, "expectancy_krw": -300},
              {"bucket": "5", "min_score": 5, "n": 15, "expectancy_krw": 200}]
    assert ff._suggest(none_b, 10, 4)[0] == 5


def test_06_register_idempotent(monkeypatch, tmp_path):
    """⑥ --register 멱등: 같은 부정 버킷 2회 실행 시 큐에 1건만(같은 제목은 건너뛴다)."""
    q = tmp_path / "queue.json"
    monkeypatch.setenv("IMPROVE_QUEUE", str(q))
    negs = [{"title": "feed_feedback 부정 버킷: edge_score=3", "why": "합성: 기대값 음수",
             "acceptance": "사람 승인 후 게이트 변경 + trader 테스트 통과", "priority": 2}]
    r1 = ff._register(negs)
    assert r1[0]["status"] == "added", r1
    r2 = ff._register(negs)
    assert r2[0]["status"] == "exists", r2
    d = json.loads(q.read_text(encoding="utf-8"))
    assert len(d["tasks"]) == 1, d
    assert d["tasks"][0]["title"] == negs[0]["title"]
    assert isinstance(d["tasks"][0]["priority"], int)  # 스펙 계약: 정수 priority


def _journal(tmp_path, rows):
    j = tmp_path / "journal.sqlite3"
    conn = sqlite3.connect(str(j))
    conn.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, ts TEXT, side TEXT, code TEXT, "
        "name TEXT, qty REAL, price REAL, order_type TEXT, order_resp_json TEXT, "
        "entry_reason TEXT, screener TEXT, hwm_price REAL, algo_json TEXT, stop_pct REAL, "
        "tp_pct REAL, kelly_f REAL, exit_ts TEXT, exit_price REAL, exit_reason TEXT, "
        "pnl REAL, fees REAL)"
    )
    conn.executemany(
        "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return j


def test_07_idempotent_bytes(monkeypatch, tmp_path):
    """⑦ 멱등: 같은 입력 → 같은 바이트(json/md/제안 파일 전부). 실행 시각 미기록 검증."""
    now = datetime(2026, 11, 18, 17, 0, tzinfo=KST)
    rows = [
        (1, "2026-11-14T10:00:00+09:00", "buy", "000001", "A", 10, 1000.0, "limit",
         None, None, "daytrading", None,
         json.dumps({"candidate": {"edge_score": 5, "edge_factors": {"sr_breakout": True}},
                     "stop": {"model": "fixed"}}), None, None, None,
         "2026-11-14T10:30:00+09:00", 1005.0, "monitor max_hold_30m", 50.0, 5.0),
        (2, "2026-11-14T11:00:00+09:00", "buy", "000002", "B", 5, 2000.0, "limit",
         None, None, "daytrading", None,
         json.dumps({"candidate": {"edge_score": 4, "edge_factors": {"sr_breakout": False}},
                     "stop": {"model": "atr"}}), None, None, None,
         "2026-11-14T11:35:00+09:00", 1990.0, "monitor max_hold_30m", -60.0, 10.0),
        # 비포지션('unfilled:')은 창 안이어도 제외된다(reentry_after_exit.py 관용구)
        (3, "2026-11-14T09:00:00+09:00", "buy", "000003", "C", 1, 3000.0, "limit",
         None, None, "swing", None, "{}", None, None, None,
         "2026-11-14T09:05:00+09:00", 3000.0, "unfilled: broker order", 0.0, 0.0),
        # 미청산(exit_ts NULL)은 창 대상이 아니다
        (4, "2026-11-14T09:30:00+09:00", "buy", "000004", "D", 1, 4000.0, "limit",
         None, None, "swing", None, "{}", None, None, None,
         None, None, None, None, None),
        # 창 밖(exit 2026-10-01 < cutoff 2026-11-04)도 제외
        (5, "2026-10-01T09:00:00+09:00", "buy", "000005", "E", 1, 5000.0, "limit",
         None, None, "swing", None, "{}", None, None, None,
         "2026-10-01T09:30:00+09:00", 5000.0, "tick stop", -100.0, 0.0),
    ]
    journal = _journal(tmp_path, rows)
    monkeypatch.setattr(ff, "_db_ctx_loader",
                        lambda trades: {"market": {}, "minute": {}, "error": None})
    args = SimpleNamespace(journal=str(journal), days=14, min_n=10,
                           json_out=str(tmp_path / "f.json"), md_out=str(tmp_path / "f.md"),
                           suggested_out=str(tmp_path / "s.json"), register=False,
                           quiet=True, current_edge_score=4)
    ff.run(args, now=now)
    b1 = {p: (tmp_path / p).read_bytes() for p in ("f.json", "f.md", "s.json")}
    ff.run(args, now=now)
    b2 = {p: (tmp_path / p).read_bytes() for p in ("f.json", "f.md", "s.json")}
    assert b1 == b2, {p: (a != c) for p, a, c in zip(b1, b1.values(), b2.values())}

    d = json.loads((tmp_path / "f.json").read_text(encoding="utf-8"))
    assert d["trades"] == 2, d["trades"]  # 3행(비포지션)·4행(미청산)·5행(창 밖) 제외
    assert [x["bucket"] for x in d["by_edge_score"]] == ["4", "5"]
    assert d["by_factor"][0]["factor"] == "sr_breakout"
    assert d["by_factor"][0]["on"]["n"] == 1 and d["by_factor"][0]["off"]["n"] == 1
    assert d["by_factor"][0]["delta_krw"] == 110.0  # on +50/건 - off -60/건
    assert d["suggestions"] == []  # 버킷 n=1 < min-n 10
    assert d["queued"] == []       # dry(register 없음)
    assert "ts" not in d and "generated_at" not in d  # 실행 시각 미기록
    sug = json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))
    assert sug["suggested"] is None and "표본 부족" in sug["reason"], sug

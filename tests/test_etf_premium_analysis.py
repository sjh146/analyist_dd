"""docs/spec_etf_premium_fix.md §3 회귀 테스트 3개 — DB·네트워크 없이 합성 입력으로 검증.

① 같은 ts 타입(datetime)에서 event_study +5min n>0 — DB timestamptz 초·마이크로초 지터를
   재현한다. 실측 2026-11-20: 09:00:02.450947 / 09:01:02.103983 / 09:05:01.716161 …
   수정 전에는 target = ep["start"]+5min 이 시작 ts 의 초·마이크로초를 보존해 어떤
   스냅샷 키와도 불일치 → 전부 skip → n=0 이었다(실측 리포트 +5min n=0). _minute_ts()
   정규화 후에는 같은 분의 스냅샷 키에 닿아 n>0.
② volume=0 스냅샷이 괴리 분석에서 제외 — 실측 2026-11-20 09:02 0193T0 price 4,255
   (전일 종가·미체결) vs nav 3,683 → +15.5308% 가짜 괴리 재현. 필터 후 by_code·episodes
   에 등장하지 않아야 한다(nav null 규칙도 함께 검증).
③ 개장 5분 이내(09:00≤t<09:05) 스냅샷 제외 — 09:05:00 경계는 포함되지 않음(제외 안 함).

아래 ④~⑥은 docs/spec_index_backfill.md §2 회귀 테스트(같은 파일에 추가):
④ load_index_change(index_code) — index_daily 실측 값으로 전일대비 수익률 계산.
   PostgreSQL %s 플레이스홀더를 ? 로 치환해 in-memory sqlite 로 실행(운영 DB 오염 금지 —
   test_index_backfill 의 UPSERT_SQL 관례). 실측 2026-11-20 KOSPI 4869.96/5137.89 → −5.2148.
⑤ 지수 행 없음(당일·전일·conn None) → (None, 사유) — 0 채움 금지.
⑥ build_report by_regime — panic(≤ −1.5%)/normal 분류: 패닉일이면 전 episode 가
   panic 쪽, 아니면 normal 쪽. 경계 −1.5 는 panic(≤ 포함). regime None 은 null + 사유.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
import etf_premium_analysis as epa  # noqa: E402

KST = timezone(timedelta(hours=9))


# ① 같은 ts 타입(datetime, 초·마이크로초 지터)에서 event_study +5min n>0
def test_event_study_horizon_under_ts_jitter():
    base = datetime(2026, 11, 20, 9, 0, tzinfo=KST)
    # 실측 초·마이크로초 분포(2026-11-20 DB 조회) — 크론 지터로 같은 분도 값이 다르다
    jitter = [(2, 450947), (2, 103983), (1, 756114), (2, 21373),
              (1, 395729), (1, 716161), (2, 359762), (1, 983959)]
    snaps = [{"ts": base + timedelta(minutes=i, seconds=s, microseconds=us),
              "code": "0193T0", "name": "KODEX SK하이닉스단일종목레버리지",
              "price": 100.0 + i, "nav": 99.0, "premium_pct": 1.0, "volume": 1000}
             for i, (s, us) in enumerate(jitter)]
    eps = epa.build_episodes(
        [(r["ts"], r["premium_pct"], r["code"], r["name"]) for r in snaps], 0.5)
    assert len(eps) == 1
    ev = epa.event_study(eps, snaps, [5])
    assert ev["+5min"]["n"] > 0                    # 수정 전: n=0 (전부 skip)
    assert ev["+5min"]["n"] == 1                   # 09:05:01.716161 → 분 정규화로 매칭
    assert ev["+5min"]["mean_pct"] == 5.0          # 105.0/100.0 − 1


# ② volume=0 스냅샷(개장 직후 미체결 아티팩트)이 괴리 분석에서 제외
def test_filter_excludes_zero_volume_snapshot():
    t0 = datetime(2026, 11, 20, 10, 0, tzinfo=KST)
    snaps = [
        # 실측 09:02 행 재현: 전일 종가 가격 + 부실 NAV → +15.5308% 가짜 괴리, volume 0
        {"ts": t0, "code": "0193T0", "name": "KODEX SK하이닉스단일종목레버리지",
         "price": 4255.0, "nav": 3683.0, "premium_pct": 15.5308, "volume": 0},
        {"ts": t0, "code": "114800", "name": "KODEX 인버스", "price": 1309.0,
         "nav": 1312.0, "premium_pct": -0.2287, "volume": 1000},
        {"ts": t0, "code": "252670", "name": "KODEX 200선물인버스2X", "price": 119.0,
         "nav": None, "premium_pct": None, "volume": 500},      # nav null 규칙 ①
    ]
    valid, dropped, _ = epa.filter_snapshots(snaps)
    # 2026-11-20: stale 규칙(spec_etf_stale_fix) 추가로 dropped_stale 키가 늘었다(값 0).
    assert dropped == {"dropped_no_nav": 1, "dropped_no_volume": 1,
                       "dropped_open_window": 0, "dropped_stale": 0,
                       "dropped_no_amount": 0}
    assert [r["code"] for r in valid] == ["114800"]

    rep = epa.build_report("2026-11-20", snaps, min_snapshots=1, threshold=0.5,
                           horizons=[5], regime=None, source_note="테스트")
    assert rep["n_snapshots"] == 1
    assert "0193T0" not in rep["by_code"]           # 가짜 15% 괴리 종목 제외
    assert all(ep["code"] != "0193T0" for ep in rep["episodes"])
    f = rep["filtered"]
    assert f["n_snaps_before"] == 3 and f["n_snaps_after"] == 1
    assert f["episodes_before"] == 1 and f["episodes_after"] == 0  # 가짜 에피소드만 사라짐
    assert "필터" in epa.report_to_md(rep)          # 리포터 상단 필터 사유 표기


# ③ 개장 5분 이내(09:00≤t<09:05) 스냅샷 제외 — 09:05:00 경계는 유지
def test_filter_excludes_open_window_snapshots():
    t_open = datetime(2026, 11, 20, 9, 2, tzinfo=KST)          # 개장 5분 이내
    t_ok = datetime(2026, 11, 20, 10, 0, tzinfo=KST)
    snaps = [
        {"ts": t_open, "code": "A1", "name": "A", "price": 100.0, "nav": 99.0,
         "premium_pct": 1.0, "volume": 1000},
        {"ts": t_ok, "code": "B2", "name": "B", "price": 100.0, "nav": 99.0,
         "premium_pct": 1.0, "volume": 1000},
    ]
    valid, dropped, _ = epa.filter_snapshots(snaps, skip_open_min=5)
    assert dropped["dropped_open_window"] == 1
    assert [r["code"] for r in valid] == ["B2"]

    # 경계: 09:05:00 은 제외 구간이 아니다(09:00~09:05 미만)
    t_edge = datetime(2026, 11, 20, 9, 5, tzinfo=KST)
    valid2, dropped2, _ = epa.filter_snapshots(
        [{"ts": t_edge, "code": "C3", "name": "C", "price": 1.0, "nav": 1.0,
          "premium_pct": 0.0, "volume": 1}], skip_open_min=5)
    assert dropped2["dropped_open_window"] == 0 and len(valid2) == 1


# ── spec_index_backfill §2 (--index-code) ────────────────────────────────
def _index_conn():
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE index_daily (index_code TEXT, trade_date TEXT, close REAL)")
    conn.executemany("INSERT INTO index_daily VALUES (?,?,?)", [
        ("KOSPI", "2026-11-18", 5067.44),
        ("KOSPI", "2026-11-19", 5137.89),
        ("KOSPI", "2026-11-20", 4869.96),
        ("KOSDAQ", "2026-11-20", 645.68)])          # 전일 행 없음(직전일 결측 케이스)
    return conn


def _sqlite_epa(monkeypatch):
    monkeypatch.setattr(epa, "INDEX_TODAY_SQL", epa.INDEX_TODAY_SQL.replace("%s", "?"))
    monkeypatch.setattr(epa, "INDEX_PREV_SQL", epa.INDEX_PREV_SQL.replace("%s", "?"))


# ④ index_daily 전일대비 수익률 — 실측 2026-11-20 KOSPI 4869.96 / 5137.89 → −5.2148
def test_load_index_change_from_index_daily(monkeypatch):
    conn = _index_conn()
    _sqlite_epa(monkeypatch)
    regime, reason = epa.load_index_change(conn, "2026-11-20", "KOSPI")
    assert regime == {"KOSPI": -5.2148}             # 실측 계산값(round 4자리)
    assert reason == ""                              # 지수 있음 → 사유 없음
    conn.close()


# ⑤ 지수 행 없음 → (None, 사유) — 0 채움 금지(스펙 §2: '지수 없으면 null + 사유')
def test_load_index_change_missing_rows_returns_reason(monkeypatch):
    conn = _index_conn()
    _sqlite_epa(monkeypatch)
    # 당일 행 없음(미래 날짜·다른 코드)
    regime, reason = epa.load_index_change(conn, "2026-11-21", "KOSPI")
    assert regime is None and "행 없음" in reason
    # 전일(직전 거래일) 행 없음 — KOSDAQ 은 당일 행만
    regime, reason = epa.load_index_change(conn, "2026-11-20", "KOSDAQ")
    assert regime is None and "직전" in reason
    conn.close()
    # conn None → 사유 기본값(NO_INDEX_REASON)
    regime, reason = epa.load_index_change(None, "2026-11-20", "KOSPI")
    assert regime is None and reason == epa.NO_INDEX_REASON


def _regime_snaps():
    t0 = datetime(2026, 11, 20, 10, 0, tzinfo=KST)
    return [{"ts": t0 + timedelta(minutes=i), "code": "114800", "name": "KODEX 인버스",
             "price": 100.0 + i, "nav": 99.0, "premium_pct": 1.0 + i * 0.1, "volume": 1000}
            for i in range(8)]                        # 1개 episode(시작 t0, +5분 스냅샷 존재)


# ⑥ by_regime panic/normal 분류 — panic = 전일대비 ≤ −1.5%(경계 포함), normal = 그 외
def test_by_regime_panic_normal_split():
    snaps = _regime_snaps()
    rep = epa.build_report("2026-11-20", snaps, min_snapshots=1, threshold=0.5,
                           horizons=[5], regime={"KOSPI": -5.2148},
                           regime_reason="", source_note="테스트")
    br = rep["by_regime"]
    assert br["regime"] == "available"
    assert br["is_panic_day"] is True                # 실측 −5.2148% → panic 일
    assert br["panic_threshold_pct"] == -1.5
    assert br["panic"]["+5min"]["n"] == 1            # 전 episode 가 panic 쪽
    assert br["normal"]["+5min"]["n"] == 0
    md = epa.report_to_md(rep)
    assert "is_panic_day: True" in md                # 리포트에 분류 표기

    rep2 = epa.build_report("2026-11-20", snaps, min_snapshots=1, threshold=0.5,
                            horizons=[5], regime={"KOSPI": -0.5},
                            regime_reason="", source_note="테스트")
    br2 = rep2["by_regime"]
    assert br2["is_panic_day"] is False              # −0.5% → normal 일
    assert br2["panic"]["+5min"]["n"] == 0
    assert br2["normal"]["+5min"]["n"] == 1

    # 경계: ret = −1.5 정확히 → panic(≤ 포함)
    rep3 = epa.build_report("2026-11-20", snaps, min_snapshots=1, threshold=0.5,
                            horizons=[5], regime={"KOSPI": -1.5},
                            regime_reason="", source_note="테스트")
    assert rep3["by_regime"]["is_panic_day"] is True

    # regime None → null + 사유(기존 동작 유지 — 0 채움 금지)
    rep4 = epa.build_report("2026-11-20", snaps, min_snapshots=1, threshold=0.5,
                            horizons=[5], regime=None, source_note="테스트")
    assert rep4["by_regime"] == {"regime": None, "reason": epa.NO_INDEX_REASON}


def _snap(ts_min, code="0194R0", price=4255.0, nav=3683.0, volume=5.0):
    from datetime import datetime, timedelta
    base = datetime(2026, 11, 20, 9, 5)
    return {"ts": base + timedelta(minutes=ts_min), "code": code, "name": "t",
            "price": price, "nav": nav,
            "premium_pct": (price / nav - 1.0) * 100.0, "volume": volume}


def test_stale_price_snapshots_are_dropped():
    """가격은 고정인데 NAV 가 움직이면 stale 로 제외(spec_etf_stale_fix 보정 규칙).

    2026-11-20 실측: 0194R0 이 09:00~09:1x price 3,555 고정·NAV 3,094→3,132→3,119 로
    움직여 +14% 가짜 괴리를 만들었다. 비유동 종목은 NAV 도 같이 멈추므로 그건 살린다.
    """
    from scripts.etf_premium_analysis import filter_snapshots
    snaps = [_snap(i, nav=3683.0 + i * 20.0) for i in range(6)]   # 가격 고정 + NAV 상승
    valid, dropped, note = filter_snapshots(snaps, min_volume=1, skip_open_min=0, stale_minutes=3)
    assert dropped["dropped_stale"] == 3          # 앞 3개는 창이 모자라 유지, 이후 3개 제외
    assert len(valid) == 3
    assert "stale=3" in note


def test_frozen_price_with_frozen_nav_is_kept():
    """가격·NAV 가 함께 멈춘 비유동 스냅샷은 제외하지 않는다(과차단 방지)."""
    from scripts.etf_premium_analysis import filter_snapshots
    snaps = [_snap(i) for i in range(6)]          # 가격·NAV 모두 고정
    valid, dropped, _ = filter_snapshots(snaps, min_volume=1, skip_open_min=0, stale_minutes=3)
    assert dropped["dropped_stale"] == 0
    assert len(valid) == 6


def test_moving_price_is_not_dropped():
    from scripts.etf_premium_analysis import filter_snapshots
    snaps = [_snap(i, price=4255.0 + i) for i in range(6)]
    valid, dropped, _ = filter_snapshots(snaps, min_volume=1, skip_open_min=0, stale_minutes=3)
    assert dropped["dropped_stale"] == 0
    assert len(valid) == 6


def test_stale_minutes_zero_disables_rule():
    from scripts.etf_premium_analysis import filter_snapshots
    snaps = [_snap(i) for i in range(6)]
    valid, dropped, _ = filter_snapshots(snaps, min_volume=1, skip_open_min=0, stale_minutes=0)
    assert dropped["dropped_stale"] == 0
    assert len(valid) == 6

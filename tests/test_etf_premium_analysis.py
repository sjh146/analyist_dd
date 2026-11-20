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
    assert dropped == {"dropped_no_nav": 1, "dropped_no_volume": 1,
                       "dropped_open_window": 0}
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

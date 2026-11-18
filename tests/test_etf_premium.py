"""docs/spec_etf_premium_monitor.md §4 계약 7개 — DB·네트워크 없이 합성 입력으로 검증.

① EUC-KR 응답 파싱(종목명 한글 정상)  ② 괴리율(nowVal 100/nav 99 → +1.0101%)
③ 레버리지/인버스 필터  ④ DB 적재 멱등(같은 ts·code 2회 → 1행)
⑤ episode 병합(3분 이내 재발 → 1건)  ⑥ 반감기(φ=0.9 → 약 6.6분)
⑦ 표본 부족 시 insufficient_sample(수치 없음)

④ 는 PostgreSQL %s 플레이스홀더 그대로 두고 in-memory sqlite 용으로 ? 치환만 해서
ON CONFLICT ... DO NOTHING 멱등 계약을 확인한다(운영 DB 오염 금지 — test_naver_minute_backfill
과 동일 관례). sqlite3.executemany 의 rowcount 는 전체 합산임을 실측 확인(3행 삽입 → 3,
재실행 → 0 — 2026-11-18). 그 외는 순수 함수(parse_response·premium_pct·filter_lev_inv·
store_rows·build_episodes·ar1_estimate·build_report)만 검증한다 — 실제 Naver/DB 호출 없음.
"""
import json
import os
import sys
from datetime import datetime, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
import etf_premium_collect as epc  # noqa: E402
import etf_premium_analysis as epa  # noqa: E402


# ① EUC-KR 응답 파싱 — 종목명 한글 정상(UTF-8 로 읽으면 깨지는 실측 형식)
def test_parse_response_euc_kr_names():
    payload = {"resultCode": "success", "result": {"etfItemList": [
        {"itemcode": "122630", "itemname": "KODEX 레버리지", "nowVal": 54400,
         "nav": 54606.0, "quant": 22739595, "amonut": 1230093, "marketSum": 36638}]}}
    raw = json.dumps(payload, ensure_ascii=False).encode("euc-kr")
    total, rows = epc.parse_response(raw)
    assert total == 1
    assert rows[0]["name"] == "KODEX 레버리지"      # EUC-KR 디코드 — 한글 정상
    assert rows[0]["code"] == "122630"
    assert rows[0]["premium_pct"] == round((54400 / 54606.0 - 1) * 100, 4)
    # 손상 응답/실패 코드 → 예외(호출부가 fail-open 으로 error 필드 기록)
    try:
        epc.parse_response(b"not json")
        raise AssertionError("파싱 실패가 예외가 아니다")
    except ValueError:
        pass
    try:
        epc.parse_response(json.dumps({"resultCode": "fail"}).encode("euc-kr"))
        raise AssertionError("resultCode!=success 가 예외가 아니다")
    except ValueError:
        pass


# ② 괴리율 계산 — (nowVal/nav − 1)×100, 소수 4자리
def test_premium_pct():
    assert epc.premium_pct(100, 99) == 1.0101         # +1.0101% (100/99-1 = 0.010101...)
    assert epc.premium_pct(99, 100) == -1.0
    assert epc.premium_pct(100, 0) is None            # nav=0 → null(0 채움 금지)
    assert epc.premium_pct(None, 99) is None          # 결측 → null


# ③ 레버리지/인버스 필터 — itemname 판정(스펙: stocks 테이블과 무관)
def test_filter_lev_inv():
    items = [
        {"itemname": "KODEX 레버리지"}, {"itemname": "TIGER 미국S&P500"},
        {"itemname": "KODEX 인버스"}, {"itemname": "KODEX 200"},
        {"itemname": "TIGER 인버스 미국S&P500"},
    ]
    out = epc.filter_lev_inv(items)
    assert [x["itemname"] for x in out] == \
        ["KODEX 레버리지", "KODEX 인버스", "TIGER 인버스 미국S&P500"]
    # 통합 경로: parse_response 는 전체 중 lev/inv 만 행으로 만든다
    payload = {"resultCode": "success", "result": {"etfItemList": [
        {"itemcode": "122630", "itemname": "KODEX 레버리지", "nowVal": 100,
         "nav": 99, "quant": 1, "amonut": 1},
        {"itemcode": "360750", "itemname": "TIGER 미국S&P500", "nowVal": 100,
         "nav": 99, "quant": 1, "amonut": 1}]}}
    total, rows = epc.parse_response(
        json.dumps(payload, ensure_ascii=False).encode("euc-kr"))
    assert total == 2 and len(rows) == 1 and rows[0]["code"] == "122630"


# ④ DB 적재 멱등 — 같은 (ts, code) 2회 적재 → 1행(ON CONFLICT DO NOTHING)
def test_store_rows_idempotent(monkeypatch):
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE etf_premium_snapshots (ts TEXT, trade_date TEXT, code TEXT,"
        " name TEXT, price REAL, nav REAL, premium_pct REAL, volume INTEGER,"
        " amount INTEGER, source TEXT, PRIMARY KEY (ts, code))")
    # 배치(PostgreSQL)는 %s 그대로 — in-memory sqlite 검증용으로 ? 로만 치환
    monkeypatch.setattr(epc, "UPSERT_SQL", epc.UPSERT_SQL.replace("%s", "?"))
    ts = datetime(2026, 11, 18, 10, 30, 0).astimezone()
    rows = [(ts, "2026-11-18", "122630", "KODEX 레버리지", 100.0, 99.0, 1.0101, 1, 1, epc.SOURCE),
            (ts, "2026-11-18", "114800", "KODEX 인버스", 3000.0, 2990.0, 0.3344, 2, 2, epc.SOURCE)]
    assert epc.store_rows(conn, rows) == 2          # 첫 적재 — 2행 신규
    assert epc.store_rows(conn, rows) == 0          # 같은 ts·code 재실행 → 신규 0(멱등)
    n = conn.execute("SELECT COUNT(*) FROM etf_premium_snapshots").fetchone()[0]
    assert n == 2
    conn.close()


# ⑤ episode 병합 — 같은 종목 3분 이내 재발 → 1건(3분 초과 공백 → 새 구간)
def test_episodes_merge_within_3min():
    t0 = datetime(2026, 11, 18, 10, 0)
    points = [
        (t0, 0.8, "A", "KODEX 레버리지"),
        (t0 + timedelta(minutes=1), 0.2, "A", "KODEX 레버리지"),    # 미달 — 비플래그
        (t0 + timedelta(minutes=3), -0.9, "A", "KODEX 레버리지"),   # 3분 이내 재발 → 병합
        (t0 + timedelta(minutes=10), 0.6, "A", "KODEX 레버리지"),   # 7분 공백 → 새 구간
    ]
    eps = epa.build_episodes(points, threshold=0.5)
    assert len(eps) == 2
    assert eps[0]["n_points"] == 2
    assert eps[0]["duration_min"] == 4                       # 10:00~10:03 양끝 포함 4분
    assert eps[0]["max_premium"] == -0.9                     # 부호 유지 최대괴리
    assert eps[0]["start_premium"] == 0.8
    assert eps[1]["duration_min"] == 1 and eps[1]["max_premium"] == 0.6


# ⑥ 반감기 — φ=0.9 → ln(0.5)/ln(0.9) ≈ 6.58분(약 6.6분)
def test_ar1_half_life_phi_09():
    values = [100.0 * (0.9 ** i) for i in range(8)]          # 노이즈 없는 AR(1)
    phi, half = epa.ar1_estimate(values)
    assert phi is not None and round(phi, 6) == 0.9          # OLS = 0.9 정확히
    assert abs(half - 6.5788) < 0.001                        # ln0.5/ln0.9 ≈ 6.5788…
    assert round(half, 1) == 6.6
    assert epa.ar1_estimate([1.0, 2.0]) == (None, None)      # 표본 < 3 → null
    phi2, half2 = epa.ar1_estimate([1.0, 2.0, 3.0, 4.0])     # φ=1
    assert phi2 == 1.0 and half2 is None                     # |φ|≥1 → null(스펙 §2.1)
    assert epa.ar1_estimate([5.0, 5.0, 5.0, 5.0]) == (None, None)   # Var=0 → null


# ⑦ 표본 부족 — insufficient_sample 반환(수치 키 없음, 0으로 메우지 않음)
def test_insufficient_sample():
    ts = datetime(2026, 11, 18, 10, 30).astimezone()
    snaps = [{"ts": ts, "code": "122630", "name": "KODEX 레버리지", "price": 100.0,
              "nav": 99.0, "premium_pct": 1.0101, "volume": 1000}]
    rep = epa.build_report("2026-11-18", snaps, min_snapshots=60, threshold=0.5,
                           horizons=[5, 15, 30, 60], regime=None, source_note="DB 0행")
    assert "insufficient_sample" in rep
    assert rep["insufficient_sample"]["n_snapshots"] == 1
    assert rep["insufficient_sample"]["min_snapshots"] == 60
    assert "by_code" not in rep and "episodes" not in rep      # 수치 없음
    assert "insufficient_sample" in epa.report_to_md(rep)      # md 도 동일

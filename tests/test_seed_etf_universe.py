"""seed_etf_universe 마스터 파서/upsert 단위 테스트 — DB·네트워크 무관 (호스트 pytest).

- 합성 고정폭 레코드로 EF(ETF)/EN(ETN) 파싱 검증
- cp949 한글명 디코딩, 잘못된 라인 무시
- 그룹코드 → instrument_type 매핑, 비대상(ST 등) 제외
- upsert 중복 처리: ON CONFLICT DO NOTHING (기존 instrument_type 덮어쓰기 금지)
"""
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import seed_etf_universe as seu


# ─── 합성 고정폭 레코드 빌더 ────────────────────────────────────────────
def _record(short="069500", name="KODEX 200", group="EF", std="KR7069500000",
            total_len=288):
    """실측 레이아웃(9/12/40/2)으로 고정폭 레코드 바이트 생성."""
    short_b = short.encode("latin-1").ljust(seu.SHORT_CODE_END - seu.SHORT_CODE_START)
    std_b = std.encode("latin-1").ljust(seu.STD_CODE_END - seu.STD_CODE_START)
    name_b = name.encode("cp949").ljust(seu.NAME_END - seu.NAME_START)
    group_b = group.encode("latin-1").ljust(seu.GROUP_END - seu.GROUP_START)
    rec = short_b + std_b + name_b + group_b
    if len(rec) < total_len:
        rec = rec + b" " * (total_len - len(rec))
    return rec[:total_len]


# ── 1. parse_record ────────────────────────────────────────────────────
class TestParseRecord:
    def test_etf_record(self):
        rec = _record(short="069500", name="KODEX 200", group="EF")
        r = seu.parse_record(rec)
        assert r["stock_code"] == "069500"
        assert r["stock_name"] == "KODEX 200"
        assert r["group"] == "EF"

    def test_etn_record(self):
        rec = _record(short="500001", name="TIGER 원유선물ETN", group="EN")
        r = seu.parse_record(rec)
        assert r["group"] == "EN"

    def test_korean_cp949_name(self):
        rec = _record(short="102110", name="TIGER 200", group="EF")
        r = seu.parse_record(rec)
        assert r["stock_name"] == "TIGER 200"

        rec2 = _record(short="122630", name="KODEX 레버리지", group="EF")
        r2 = seu.parse_record(rec2)
        assert r2["stock_name"] == "KODEX 레버리지"

    def test_std_code_parsed(self):
        rec = _record(short="0000D0", name="TIGER 엔비디아", group="EF",
                      std="KR70000D0009")
        r = seu.parse_record(rec)
        assert r["std_code"] == "KR70000D0009"

    def test_short_line_ignored(self):
        assert seu.parse_record(b"012345") is None
        assert seu.parse_record(b"") is None

    def test_invalid_cp949_ignored(self):
        # NAME 구간을 cp949로 디코딩할 수 없는 바이트로 채움
        bad = _record(short="069500", name="KODEX 200", group="EF")
        bad = bytearray(bad)
        bad[seu.NAME_START] = 0xFF
        bad[seu.NAME_START + 1] = 0xFE
        assert seu.parse_record(bytes(bad)) is None

    def test_empty_short_code_ignored(self):
        rec = _record(short="      ", name="이름", group="EF")
        assert seu.parse_record(rec) is None


# ── 2. parse_master ────────────────────────────────────────────────────
class TestParseMaster:
    def test_mixed_valid_and_invalid_lines(self):
        good_ef = _record(short="069500", name="KODEX 200", group="EF")
        good_en = _record(short="500001", name="ETN", group="EN")
        data = b"\n".join([good_ef, b"garbage-short", good_en, b""])
        recs = seu.parse_master(data, "KOSPI")
        assert len(recs) == 2
        assert all(r["market"] == "KOSPI" for r in recs)
        assert [r["stock_code"] for r in recs] == ["069500", "500001"]


# ── 3. filter_targets ──────────────────────────────────────────────────
class TestFilterTargets:
    def _recs(self):
        return [
            {"stock_code": "069500", "group": "EF", "stock_name": "KODEX 200", "market": "KOSPI"},
            {"stock_code": "500001", "group": "EN", "stock_name": "ETN", "market": "KOSPI"},
            {"stock_code": "005930", "group": "ST", "stock_name": "삼성전자", "market": "KOSPI"},
            {"stock_code": "000001", "group": "DR", "stock_name": "예탁", "market": "KOSPI"},
        ]

    def test_only_requested_types_included(self):
        out = seu.filter_targets(self._recs(), ["ETF"])
        assert [r["stock_code"] for r in out] == ["069500"]
        assert out[0]["instrument_type"] == "ETF"

    def test_etf_and_etn(self):
        out = seu.filter_targets(self._recs(), ["ETF", "ETN"])
        assert [r["stock_code"] for r in out] == ["069500", "500001"]
        assert [r["instrument_type"] for r in out] == ["ETF", "ETN"]

    def test_unknown_group_excluded(self):
        recs = self._recs() + [
            {"stock_code": "999999", "group": "BC", "stock_name": "펀드", "market": "KOSPI"},
        ]
        out = seu.filter_targets(recs, ["ETF", "ETN"])
        assert "999999" not in [r["stock_code"] for r in out]


# ── 4. upsert 중복 처리 ─────────────────────────────────────────────────
class TestUpsert:
    def test_sql_uses_on_conflict_do_nothing(self):
        # 회귀 방지: 기존 종목 instrument_type 을 덮어쓰면 안 된다.
        assert "ON CONFLICT (stock_code) DO NOTHING" in seu.UPSERT_SQL
        assert "DO UPDATE" not in seu.UPSERT_SQL

    def test_insert_increments_inserted(self, monkeypatch):
        class FakeCur:
            rowcount = 1
            def execute(self, sql, params):
                pass
            def close(self):
                pass
        class FakeConn:
            def cursor(self):
                return FakeCur()
            def commit(self):
                pass
            def close(self):
                pass

        monkeypatch.setattr(seu, "pg_connect", lambda: FakeConn())
        recs = [{"stock_code": "069500", "stock_name": "KODEX 200",
                 "market": "KOSPI", "instrument_type": "ETF"}]
        result = seu.upsert_stocks(recs, dry_run=False)
        assert result == {"inserted": 1, "skipped": 0}

    def test_conflict_skips_existing(self, monkeypatch):
        # rowcount=0 → ON CONFLICT DO NOTHING 으로 아무 것도 안 들어감 → skipped
        class FakeCur:
            rowcount = 0
            def execute(self, sql, params):
                pass
            def close(self):
                pass
        class FakeConn:
            def cursor(self):
                return FakeCur()
            def commit(self):
                pass
            def close(self):
                pass

        monkeypatch.setattr(seu, "pg_connect", lambda: FakeConn())
        recs = [{"stock_code": "069500", "stock_name": "KODEX 200",
                 "market": "KOSPI", "instrument_type": "ETF"}]
        result = seu.upsert_stocks(recs, dry_run=False)
        assert result == {"inserted": 0, "skipped": 1}

    def test_dry_run_no_db(self, monkeypatch):
        called = {"v": False}
        def boom():
            called["v"] = True
            raise AssertionError("dry-run 은 DB에 접근하면 안 됨")
        monkeypatch.setattr(seu, "pg_connect", boom)
        recs = [{"stock_code": "069500", "stock_name": "KODEX 200",
                 "market": "KOSPI", "instrument_type": "ETF"}]
        result = seu.upsert_stocks(recs, dry_run=True)
        assert result["inserted"] == 1
        assert called["v"] is False

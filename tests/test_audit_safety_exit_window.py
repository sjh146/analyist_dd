"""audit_safety — audit_exit_window 통합 회귀 테스트.

WHY (2026-10-04): preopen 안전 감사가 09:15 틱(audit_exit_window)의 최근 창 밖 청산을
위험 항목으로 싣는지 잠근다. 실측 근거: data/reports/audit/exit_window.json(ts
2026-10-02T22:13:38)은 n_out_of_window=3 · −1,814원 — 이 값이 08:25 preopen 경보로
올라가야 한다. 두 경우를 잠근다: ① 창 밖 청산 있음 → alert ② 없음 → alert 없음(회귀 방지).
"""
import datetime as dt
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import audit_safety as mod  # noqa: E402

TODAY = dt.datetime.now().isoformat(timespec="seconds")


def _rec(ts, sells):
    """audit_exit_window 가 쓰는 JSON 모양(data/reports/audit/exit_window.json)."""
    n_out = sum(1 for s in sells if s.get("in_window") is False)
    return {"ts": ts, "days": 10, "window": "09:00-09:10", "n_sells": len(sells),
            "n_out_of_window": n_out,
            "out_of_window_krw": -1814.0 if n_out else 0.0,
            "sells": sells}


def _out_sell(**kw):
    base = {"ts": "2026-09-30T09:26:48.540127+09:00", "code": "094860", "qty": 20.0,
            "price": 1515.0, "screener": "close", "exit_reason": "reconcile: broker flat",
            "entry_ts": "2026-09-29T15:03:41", "date": "2026-09-30", "hm": "09:26",
            "in_window": False, "open_price": 1598.0, "vs_open_krw": -1660.0}
    base.update(kw)
    return base


def _in_sell():
    return _out_sell(hm="09:05", in_window=True, vs_open_krw=0.0)


class TestExitWindowParse:
    def test_out_of_window_produces_alert(self):
        info, alert = mod.exit_window_parse(_rec(TODAY, [_out_sell(), _in_sell()]),
                                            remaining_positions=2)
        assert info["n_sells"] == 2
        assert info["n_out_of_window"] == 1
        assert info["out_of_window_krw"] == -1660.0
        assert info["remaining_positions"] == 2
        assert info["stale"] is False
        assert alert is not None
        assert alert["check"] == "exit_out_of_window_recent"
        assert "-1,660" in alert["detail"]
        assert "2026-09-30" in alert["detail"] and "09:26" in alert["detail"]
        assert "잔존 포지션 2건" in alert["detail"]

    def test_all_in_window_no_alert(self):
        info, alert = mod.exit_window_parse(_rec(TODAY, [_in_sell()]))
        assert info["n_out_of_window"] == 0
        assert info["out_of_window_krw"] == 0.0
        assert alert is None

    def test_no_sells_no_alert(self):
        info, alert = mod.exit_window_parse(_rec(TODAY, []))
        assert info["n_sells"] == 0 and info["n_out_of_window"] == 0
        assert alert is None

    def test_row_without_in_window_key_is_ignored(self):
        # in_window 키가 없는 행(옛 형식)은 창 밖으로 추정하지 않는다 — 추측 금지 규칙
        row = _out_sell()
        row.pop("in_window")
        info, alert = mod.exit_window_parse(_rec(TODAY, [row]))
        assert info["n_out_of_window"] == 0
        assert alert is None

    def test_stale_json_no_alert(self):
        old = (dt.datetime.now()
               - dt.timedelta(days=mod.EXIT_WINDOW_MAX_AGE_DAYS + 1)).isoformat(timespec="seconds")
        info, alert = mod.exit_window_parse(_rec(old, [_out_sell()]))
        assert info["stale"] is True
        assert alert is None

    def test_bad_rec_returns_none(self):
        assert mod.exit_window_parse(None) == (None, None)
        assert mod.exit_window_parse({"sells": []}) == (None, None)  # ts 없음


class TestExitWindowReceive:
    def test_receive_reads_file(self, tmp_path):
        p = tmp_path / "exit_window.json"
        p.write_text(json.dumps(_rec(TODAY, [_out_sell()])), encoding="utf-8")
        rec, err = mod.exit_window_receive(path=str(p))
        assert err is None
        assert rec["n_sells"] == 1

    def test_receive_missing_file(self, tmp_path):
        rec, err = mod.exit_window_receive(path=str(tmp_path / "nope.json"))
        assert rec is None and err

    def test_receive_bad_json(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        rec, err = mod.exit_window_receive(path=str(p))
        assert rec is None and err


class TestMainWiring:
    """main() 이 실제로 alerts/info 에 통합 결과를 싣는지 — 외부 접점은 전부 monkeypatch."""

    def _patch_env(self, monkeypatch, tmp_path, rec):
        monkeypatch.setattr(mod, "OUT_DIR", str(tmp_path))
        monkeypatch.setattr(mod, "TA", str(tmp_path))          # kill_switch.txt 는 없는 것으로
        monkeypatch.setattr(mod, "exit_window_receive", lambda path=None: (rec, None))
        monkeypatch.setattr(mod, "_exit_window_claim", lambda **kw: "mocked-ok")
        monkeypatch.setattr(mod, "bridge", lambda p: {"ok": True, "connected": True})
        monkeypatch.setattr(mod, "processes", lambda: {"bridge": 1, "loop": 1})
        monkeypatch.setattr(mod, "config_limits", lambda: {})
        monkeypatch.setattr(mod, "journal_stats",
                            lambda: {"n": 3, "open": 0, "fees_sum": 1.0, "pnl_sum": 0.0})

    def _safety_json(self, tmp_path):
        import glob
        files = sorted(glob.glob(os.path.join(str(tmp_path), "safety_*.json")))
        assert files
        return json.load(open(files[-1], encoding="utf-8"))

    def test_main_appends_alert_when_out_of_window(self, monkeypatch, tmp_path):
        self._patch_env(monkeypatch, tmp_path, _rec(TODAY, [_out_sell()]))
        assert mod.main() == 2            # 사람 단계 없음 · 경보는 창 밖 청산뿐
        d = self._safety_json(tmp_path)
        assert any(a["check"] == "exit_out_of_window_recent" for a in d["alerts"])
        assert d["info"]["exit_window"]["n_out_of_window"] == 1
        assert d["info"]["exit_window"]["claim"] == "mocked-ok"

    def test_main_no_alert_when_all_in_window(self, monkeypatch, tmp_path):
        self._patch_env(monkeypatch, tmp_path, _rec(TODAY, [_in_sell()]))
        assert mod.main() == 0            # 경보·사람 단계 없음
        d = self._safety_json(tmp_path)
        assert not any(a["check"] == "exit_out_of_window_recent" for a in d["alerts"])
        assert d["info"]["exit_window"]["n_out_of_window"] == 0
        assert d["info"]["exit_window"]["claim"] == "mocked-ok"

    def test_main_survives_missing_exit_window_json(self, monkeypatch, tmp_path):
        # 소스가 없어도 감사는 계속된다(감사가 감사에 깨지지 않는다)
        self._patch_env(monkeypatch, tmp_path, None)
        monkeypatch.setattr(mod, "exit_window_receive", lambda path=None: (None, "no file"))
        assert mod.main() == 0
        d = self._safety_json(tmp_path)
        assert "error" in d["info"]["exit_window"]
        assert "claim" in d["info"]["exit_window"]

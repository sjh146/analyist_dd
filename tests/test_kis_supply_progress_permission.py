"""kis_supply_backfill 진행파일 권한 회귀 테스트 (DB·네트워크 없음).

왜 필요한가 (실측 2026-11-19 19:10): 진행파일 `data/kis/supply_progress_<from>_<to>.txt` 는
root 크론(16:20, /etc/cron.d)과 사용자 크론(19:10, jhshi)의 **공유 자원**이다. root 가 먼저
만들면 644·root 소유가 되어 jhshi 실행의 append 가 PermissionError 로 죽고, 그 예외가
종목 루프의 generic except 에 걸려 **데이터는 적재됐는데도 '실패'로 세어지고** 진행이
기록되지 않아 같은 창을 매 실행 재수집한다(당일 ok=250 fail=250, 호출 500 중 250건 낭비).

계약: ① 진행 기록은 어떤 경우에도 예외를 올리지 않는다(수집을 깨면 안 된다)
      ② 쓰기 불가면 uid 폴백 파일로 기록하고 load_done 이 그것을 합쳐 읽는다
      ③ root 가 만들 때는 디렉터리 소유자에게 넘겨 다음 사용자 크론이 쓸 수 있게 한다

주의: 전역 `builtins.open`/`os.chmod` 를 monkeypatch 하면 테스트 자신의 파일 IO(tmp_path)까지
오염된다 — shim 객체로 `ks.open`·`ks.os` 만 바꾼다(스킬 실측 함정).
"""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import kis_supply_backfill as ks  # noqa: E402

_REAL_OPEN = open


def _os_shim(**over):
    """os 모듈 대체 shim — 지정한 속성만 덮어쓰고 나머지는 원본 위임."""
    shim = types.SimpleNamespace(**over)

    class _Shim:
        def __getattr__(self, name):
            if name in shim.__dict__:
                return shim.__dict__[name]
            return getattr(os, name)

    return _Shim()


def _deny(*_a, **_kw):
    raise PermissionError(1, "not owner")


def test_save_and_load_roundtrip(tmp_path):
    p = str(tmp_path / "supply_progress_20260101_20260102.txt")
    assert ks.save_done(p, "005930") is True
    assert ks.save_done(p, "000660") is True
    assert ks.load_done(p) == {"005930", "000660"}


def test_load_done_missing_file_is_empty(tmp_path):
    assert ks.load_done(str(tmp_path / "nope.txt")) == set()


def test_save_done_permission_error_does_not_raise_uses_uid_fallback(tmp_path, monkeypatch):
    """다른 사용자(root) 소유 진행파일 → 예외 없이 uid 폴백에 기록."""
    p = str(tmp_path / "supply_progress_20260101_20260102.txt")

    def fake_open(path, *a, **kw):
        if str(path) == p:
            raise PermissionError(13, "Permission denied", p)
        return _REAL_OPEN(path, *a, **kw)

    monkeypatch.setattr(ks, "open", fake_open, raising=False)
    monkeypatch.setattr(ks, "os", _os_shim(chmod=_deny), raising=False)
    monkeypatch.setattr(ks, "_PROGRESS_WARNED", False)

    assert ks.save_done(p, "005930") is True          # 예외 없이 True
    fb = f"{p}.uid{os.getuid()}"
    assert os.path.exists(fb)
    with _REAL_OPEN(fb) as f:
        assert f.read().strip() == "005930"


def test_load_done_merges_fallback(tmp_path):
    p = str(tmp_path / "supply_progress_20260101_20260102.txt")
    with _REAL_OPEN(p, "w") as f:
        f.write("005930\n")
    with _REAL_OPEN(f"{p}.uid{os.getuid()}", "w") as f:
        f.write("000660\n")
    assert ks.load_done(p) == {"005930", "000660"}


def test_load_done_tolerates_unreadable_file(tmp_path, monkeypatch):
    """읽기 불가 파일 하나가 load_done 을 죽이면 수집 전체가 멈춘다."""
    p = str(tmp_path / "supply_progress_20260101_20260102.txt")
    with _REAL_OPEN(p, "w") as f:
        f.write("005930\n")

    def fake_open(path, *a, **kw):
        if str(path) == p:
            raise PermissionError(13, "Permission denied", p)
        return _REAL_OPEN(path, *a, **kw)

    monkeypatch.setattr(ks, "open", fake_open, raising=False)
    assert ks.load_done(p) == set()          # 예외 없이 빈 집합(폴백도 없음)


def test_root_hands_progress_file_to_dir_owner(tmp_path, monkeypatch):
    """root 가 만들면 디렉터리 소유자로 chown → 사용자 크론(19:10)이 이어 쓸 수 있다."""
    p = str(tmp_path / "supply_progress_20260101_20260102.txt")
    calls = []
    monkeypatch.setattr(ks, "os", _os_shim(
        getuid=lambda: 0,
        chown=lambda path, uid, gid: calls.append((path, uid, gid)),
        chmod=lambda path, mode: calls.append((path, mode))), raising=False)

    assert ks.save_done(p, "005930") is True
    st = os.stat(tmp_path)
    assert (p, st.st_uid, st.st_gid) in calls
    assert (p, 0o664) in calls


def test_save_done_unwritable_dir_still_returns_false(tmp_path, monkeypatch):
    """정말 기록 불가면 False 로 알리되 예외는 올리지 않는다."""
    p = str(tmp_path / "sub" / "x.txt")

    def fake_open(path, *a, **kw):
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(ks, "open", fake_open, raising=False)
    monkeypatch.setattr(ks, "os", _os_shim(chmod=_deny), raising=False)
    monkeypatch.setattr(ks, "_PROGRESS_WARNED", False)
    assert ks.save_done(p, "005930") is False

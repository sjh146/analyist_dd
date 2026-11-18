"""test_rebuild_panel — 스펙 docs/spec_panel_rebuild.md §3 의 계약 5개(신규 스크립트 검증).

WHY: 패널 재빌드 스크립트가 실수하기 쉬운 방향은 두 가지다.
 ① 대조군(panel_prod200.npz·panel_420.npz)을 새 파일로 덮어 비교 자체를 무효화하는 사고
 ② 호스트에서 traceback 으로 죽어 재빌드가 '조용히 안 돌아가는' 실패(파이썬 의존성 부재)
그래서 여기서는 스모크 빌드 없이(DB·컨테이너 불필요) **가드와 계약**만 검증한다:
 기준선 경로 거부(exit 2) · 필수 인자 누락(exit 2) · dry-run 은 빌드 호출 금지(monkeypatch) ·
 sidecar 스키마(codes_sha1·rows·dates) · --end-date 형식 검증(exit 2).
빌드 자체의 검증은 스펙 §2 의 컨테이너 스모크(docker exec stock_xgboost_ml ...)가 담당한다.
기존 테스트 기대값은 건드리지 않는다(신규 파일).
"""
import hashlib
import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPT = os.path.join(_ROOT, "scripts", "rebuild_panel.py")
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import rebuild_panel as rp  # noqa: E402


def _run(*argv):
    """스크립트를 별도 프로세스로 실행(exit code 계약 검증). 모듈 레벨이 표준 라이브러리뿐이라
    호스트 파이썬이면 어디서든 돌아야 한다."""
    return subprocess.run([sys.executable, _SCRIPT, *argv],
                          capture_output=True, text=True, timeout=60)


def test_baseline_panel_path_rejected(tmp_path):
    """① 기존 기준선 경로(panel_prod200.npz) 지정 → exit 2 (대조군 보존, 스펙 §1/§4)."""
    r = _run("--panel-out", str(tmp_path / "panel_prod200.npz"),
             "--end-date", "2026-11-17")
    assert r.returncode == 2, f"stderr={r.stderr}"
    assert "panel_prod200" in r.stderr


def test_panel_out_required():
    """② --panel-out 누락 → exit 2 (argparse required)."""
    r = _run("--end-date", "2026-11-17")
    assert r.returncode == 2, f"stderr={r.stderr}"


def test_dry_run_does_not_call_build(monkeypatch, capsys):
    """③ --dry-run 은 빌드를 호출하지 않는다(monkeypatch 로 _build 호출 감시).

    _build 는 wf_wave.build_panel 의 유일한 호출 지점이라, 이 함수가 안 불렸으면
    빌드는 확실히 안 돌았다. 드라이 런은 인자·경로·컨테이너 실행 명령만 출력해야 한다.
    """
    calls = []

    def bomb(*a, **k):
        calls.append((a, k))
        raise AssertionError("dry-run 이 빌드를 호출했다")

    monkeypatch.setattr(rp, "_build", bomb)
    rc = rp.main(["--panel-out", "tmp_panel_rebuild_test.npz", "--days", "60",
                  "--limit", "8", "--end-date", "2026-11-17", "--dry-run"])
    assert rc == 0
    assert calls == [], "dry-run 은 _build(=wf_wave.build_panel)를 호출하면 안 된다"
    out = capsys.readouterr().out
    assert "docker exec" in out, "드라이 런은 컨테이너 실행 명령을 안내해야 한다"


def test_sidecar_report_keys():
    """④ sidecar(=build_report)에 codes_sha1·rows·dates 키가 있다. dates=[first,last].

    codes_sha1 은 정렬된 고유 종목코드의 sha1 이라(행 순서 무관) 값까지 결정적으로 검증한다.
    """
    rep = rp.build_report(days=60, end_date="2026-11-17", universe="prod",
                          universe_seed=0, limit=8, rows=123,
                          first_date="2026-09-01", last_date="2026-11-17",
                          n_features=213, codes=["005930", "000660", "005930"],
                          panel_path="/app/app/models/wf/panel_test.npz")
    assert set(rep) >= {"codes_sha1", "rows", "dates"}
    assert rep["rows"] == 123
    assert rep["dates"] == ["2026-09-01", "2026-11-17"]
    assert rep["codes_sha1"] == hashlib.sha1("000660,005930".encode("utf-8")).hexdigest()


def test_end_date_format_rejected(tmp_path):
    """⑤ --end-date 형식(YYYY-MM-DD)이 틀리면 exit 2."""
    for bad in ("2026/11/17", "20261117", "2026-02-30"):
        r = _run("--panel-out", str(tmp_path / "panel_ok_rebuild.npz"),
                 "--end-date", bad)
        assert r.returncode == 2, f"bad={bad!r} rc={r.returncode} stderr={r.stderr}"

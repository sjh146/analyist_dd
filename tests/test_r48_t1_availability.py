"""R48 프로브의 **판정 계약** 고정 — 마지막 stdout 줄이 가용 마켓 수여야 한다.

WHY (실측 2026-11-25 00:47): 종전 마지막 줄이 `... 문턱 마켓당 500행` 이었고
`researcher_cycle.eval_check` 는 `nums[-1]` 을 판정값으로 쓰므로 **문턱 500** 을 집어
'500 >= 2 → 충족' 으로 오판했다(실제 가용 마켓 0). 같은 실행이 가용 0 을 rc=1 로 내보내
구동기에는 '실패'로 적혔다 — 미발행은 정상 측정 결과인데도.

계약: ① 마지막 줄 단독 = 가용 마켓 수 정수, ② 측정이 끝났으면 rc=0,
③ 키 미설정처럼 **측정 자체가 불가**할 때만 rc≠0.
"""
import importlib
import io
import os
import sys
from contextlib import redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))


def _run(monkeypatch_rows, key="dummy"):
    mod = importlib.import_module("r48_krx_t1_availability")
    importlib.reload(mod)
    import krx_daily as k

    class FakeK:
        MARKETS = k.MARKETS
        HOLIDAY_PATH = k.HOLIDAY_PATH
        resolve_base = staticmethod(lambda key: "http://dbg")
        load_json = staticmethod(lambda p, d: [])
        fetch_day = staticmethod(lambda base, key, path, d: monkeypatch_rows(path))

    mod.k = FakeK
    if key is None:
        os.environ.pop("KRX_API_KEY", None)
    else:
        os.environ["KRX_API_KEY"] = key
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = mod.main()
    return rc, buf.getvalue()


def test_last_line_is_available_market_count_at_dawn(monkeypatch):
    """00:47 실측: 두 마켓 모두 0행 → 마지막 줄 '0', rc=0 (실패 아님)."""
    rc, out = _run(lambda path: [], key="dummy")
    lines = [ln for ln in out.strip().splitlines() if ln.strip()]
    assert lines[-1] == "0"
    assert rc == 0
    assert '"KOSPI": 0' in out and '"KOSDAQ": 0' in out


def test_available_markets_are_counted(monkeypatch):
    """두 마켓 다 500행 이상이면 마지막 줄 '2'."""
    rows = [{"a": i} for i in range(600)]
    rc, out = _run(lambda path: rows, key="dummy")
    lines = [ln for ln in out.strip().splitlines() if ln.strip()]
    assert lines[-1] == "2"
    assert rc == 0


def test_missing_key_is_a_real_failure(monkeypatch):
    """키가 없으면 판단 불가 → rc≠0 (측정 실패와 미발행을 구분한다)."""
    for var in ("KRX_API_KEY",):
        os.environ.pop(var, None)
    rc, out = _run(lambda path: [], key=None)
    assert rc != 0
    assert "KRX_API_KEY 미설정" in out

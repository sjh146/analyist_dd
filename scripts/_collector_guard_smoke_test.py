#!/usr/bin/env python3
"""수집기 × net_guard 통합 스모크 테스트 (네트워크 불필요 — HTTP 계층 대역).

각 러너의 fetch 경로에 배선한 가드가 실제로 동작하는지 검증한다:
  krx_daily.fetch_day        : 정상 1콜 → 상태 기록 / 403 HTML → KrxBlocked + 호스트 차단
  macro_backfill.http_get    : 정상 / HTML(WAF) → 차단 / 429 ×2 → 백오프 재시도 후 성공
  naver_daily_backfill.fetch : 403 → 차단 기록 (requests 대역)
  kis_program_trading.call   : 403 → 차단 기록 (KIS 앱키 키 공유)
  dart_disclosure_backfill   : 분류기(_transient/_blocked_looking) + 가드 존재
종료코드 0 = 전부 통과.
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.error

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="colguard")
os.environ["NET_GUARD_STATE_DIR"] = TMP
os.environ["NET_GUARD_EVENTS"] = os.path.join(TMP, "events.jsonl")
os.environ["PROJ_DIR"] = REPO
os.environ["KRX_API_KEY"] = "dummy"
os.environ["KIS_APP_KEY"] = "APPKEY-TEST"
os.environ["DART_API_KEY"] = "dummy"
sys.path.insert(0, os.path.join(REPO, "scripts"))

import net_guard  # noqa: E402
import krx_daily  # noqa: E402
import macro_backfill  # noqa: E402
import naver_daily_backfill  # noqa: E402
import kis_program_trading_collect as kptc  # noqa: E402
import dart_disclosure_backfill as dart  # noqa: E402

FAILS: list = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def state(key):
    return net_guard.load_state(key)


class FakeResp:
    def __init__(self, body: bytes, status=200):
        self._body, self.status, self.headers = body, status, {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@contextlib.contextmanager
def fake_urlopen(fn):
    orig = macro_backfill.urllib.request.urlopen
    macro_backfill.urllib.request.urlopen = fn
    try:
        yield
    finally:
        macro_backfill.urllib.request.urlopen = orig


def http_error(code, body=b"", headers=None):
    err = urllib.error.HTTPError("http://x", code, "err", headers or {}, None)
    err.read = lambda: body                      # type: ignore[method-assign]
    return err


def main() -> int:
    print("=== 1. krx_daily: 정상 1콜이 호스트 상태에 기록 ===")
    krx_daily.urllib.request.urlopen = lambda req, timeout=30: FakeResp(
        json.dumps({"OutBlock_1": [{"ISU_CD": "005930"}]}).encode())
    rows = krx_daily.fetch_day("http://fake/svc/apis", "k", "sto/stk_bydd_trd", "20261001")
    check("정상 응답 파싱", rows and rows[0]["ISU_CD"] == "005930", f"rows={rows}")
    check("krx 키에 호출 1건 기록", int(state("krx").get("calls", 0)) >= 1,
          f"calls={state('krx').get('calls')}")

    print("\n=== 2. krx_daily: 403 HTML → KrxBlocked + 호스트 차단(다른 프로세스도 멈춘다) ===")
    krx_daily.urllib.request.urlopen = lambda req, timeout=30: (_ for _ in ()).throw(
        http_error(403, b"<html>blocked</html>"))
    blocked = False
    try:
        krx_daily.fetch_day("http://fake/svc/apis", "k", "sto/stk_bydd_q", "20261001")
    except krx_daily.KrxBlocked as e:
        blocked = True
        print(f"      KrxBlocked: {e}")
    check("403 → KrxBlocked", blocked)
    st = state("krx")
    check("호스트 차단 기록", float(st.get("blocked_until", 0)) > time.time(),
          f"reason={st.get('blocked_reason', '')[:60]}")
    nxt = None
    try:
        krx_daily.fetch_day("http://fake/svc/apis", "k", "sto/stk_bydd_trd", "20261002")
    except krx_daily.KrxBlocked as e:
        nxt = str(e)
    check("다음 호출은 HTTP 없이 정책 중단", nxt is not None and "정책 중단" in nxt, f"{nxt}")
    net_guard.Guard("krx").clear("테스트")

    print("\n=== 3. macro_backfill.http_get: 정상 / HTML / 429 백오프 ===")
    with fake_urlopen(lambda req, timeout=30: FakeResp(b'{"ok":1}')):
        body = macro_backfill.http_get("https://ecos.bok.or.kr/api/x")
    check("정상 응답 통과", body == b'{"ok":1}', f"{body}")
    calls = {"n": 0}

    def html_then_ok(req, timeout=30):
        calls["n"] += 1
        return FakeResp(b"<html>WAF</html>")

    macro_backfill.HTTP_RETRIES = 2
    with fake_urlopen(html_then_ok):
        err = None
        try:
            macro_backfill.http_get("https://ecos.bok.or.kr/api/y")
        except Exception as e:  # noqa: BLE001
            err = e
    check("HTML 응답은 차단으로 처리", err is not None and ("차단" in str(err) or "HTML" in str(err)),
          f"{err}")
    check("ecos 키에 차단 기록", float(state("ecos").get("blocked_until", 0)) > time.time())
    net_guard.Guard("ecos").clear("테스트")

    seq = {"n": 0}
    t0 = time.time()
    macro_backfill.HTTP_RETRIES = 3        # 429 2회 → 3번째에 성공

    def flaky(req, timeout=30):
        seq["n"] += 1
        if seq["n"] < 3:
            raise http_error(429, b'{"e":"slow down"}')
        return FakeResp(b'{"ok":1}')

    with fake_urlopen(flaky):
        body = macro_backfill.http_get("https://ecos.bok.or.kr/api/z")
    took = time.time() - t0
    check("429 ×2 → 백오프 후 성공", body == b'{"ok":1}' and seq["n"] == 3, f"n={seq['n']}")
    check("백오프가 실제로 대기했다(≥2s)", took >= 2.0, f"{took:.2f}s")

    print("\n=== 4. naver_daily_backfill: 403 → 차단 (requests 대역) ===")
    class Req:
        def __init__(self, status):
            self.status_code, self.text, self.headers = status, "<html>blocked</html>", {}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise naver_daily_backfill.requests.HTTPError(str(self.status_code))

    naver_daily_backfill.requests.get = lambda *a, **k: Req(403)
    err = None
    try:
        naver_daily_backfill.fetch_day("005930", "20260901", "20261001", retries=1)
    except Exception as e:  # noqa: BLE001
        err = e
    check("403 → 실패로 올라온다", err is not None, f"{type(err).__name__}")
    check("naver 키에 차단 기록", float(state("naver").get("blocked_until", 0)) > time.time())
    net_guard.Guard("naver").clear("테스트")

    print("\n=== 5. kis_program_trading: 자체 curl 도 같은 앱키 키를 쓴다 ===")
    kptc.curl = lambda args, timeout=30: (403, "<html>blocked</html>")
    err = None
    try:
        kptc.call("APPKEY-TEST", "S", "http://x", "tok", "/p", "TR", {})
    except Exception as e:  # noqa: BLE001
        err = e
    key = net_guard.kis_key("APPKEY-TEST")
    check("403 → KisError", err is not None, f"{type(err).__name__}: {err}")
    check("KIS 앱키 키에 차단 기록", float(state(key).get("blocked_until", 0)) > time.time(),
          f"key={key}")
    net_guard.Guard(key).clear("테스트")

    print("\n=== 6. 잔존 배선 점검 ===")
    check("dart 가드 로드", dart._guard() is not None)
    check("dart transient 분류(429)", dart._transient(dart.DartHttpError(429, "x")))
    check("dart 차단 분류(HTML)", dart._blocked_looking(dart.DartHttpError(403, "<html>")))
    check("dart 영구오류는 재시도 안 함(404)", not dart._transient(dart.DartHttpError(404, "x")))
    check("krx_daily 가드 로드", krx_daily._guard() is not None)
    check("krx_derivatives/valuation 도 같은 'krx' 키 사용",
          "krx" in open(os.path.join(REPO, "scripts/krx_derivatives_collect.py"),
                        encoding="utf-8").read()
          and 'guard("krx"' in open(os.path.join(REPO, "scripts/refresh_valuation_ratios.py"),
                                    encoding="utf-8").read())

    print("\n=== 정리 ===")
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{'ALL PASS' if not FAILS else 'FAILED: ' + ', '.join(FAILS)}")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())

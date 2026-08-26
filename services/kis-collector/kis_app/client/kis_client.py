"""KIS REST API 클라이언트 — curl 기반, 보수적 호출 제한.

- 토큰: :class:`TokenManager` 가 24h 캐시(메모리+파일) 후 재사용.
  발급 1분당 1회(EGW00133)이므로 만료 임박/401 시에만 재발급.
- HTTP: subprocess curl (urllib/python-requests는 WAF 403 — 2026-08-25 실측).
- 재시도: 일시적 오류(429/5xx/EGW rate-limit)만 지수 백오프.
  스키마 오류(OPSQ2001)는 설정 버그 — 즉시 실패.
- dry_run: 실제 HTTP 없이 빈 응답(envelope)만 돌려주어 흐름 점검.
"""
from __future__ import annotations

import json
import logging
import os
import random
import subprocess
import time
from urllib.parse import urlencode

logger = logging.getLogger("kis_collector.client")

TOKEN_ENDPOINT = "/oauth2/tokenP"
DAILY_QUOTE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
MINUTE_QUOTE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice"

TOKEN_GRANT_TYPE = "client_credentials"
TOKEN_EXPIRES_IN = 86400      # 24h (문서 기준)
TOKEN_MIN_TTL = 300           # 남은 수명 < 5분 → 재발급 판단

# 일시적/재시도 가능 오류 코드 (호출 빈도 제한·서버 오류)
RATE_LIMIT_CODES = {"EGW00133", "EGW00123", "EGW00124", "EGW00225",
                    "OPSQ0029", "OPSQ0015", "OPSQ0011"}
# 토큰 관련 오류 (401 포함) → 무효화 후 재발급 → 1회 재시도
TOKEN_ERROR_CODES = {"EGW00115", "EGW00116", "EGW00117"}
# 입력 필드 스키마 오류 → 설정 버그, 재시도 없이 raise
SCHEMA_ERROR_CODES = {"OPSQ2001"}

_DRY_RUN_TOKEN = "dry-run-token"
_DRY_RUN_BODY = '{"rt_cd":"0","msg_cd":"0","msg1":"dry-run"}'


class KisApiError(Exception):
    """KIS 응답 레벨 오류 (rt_cd != 0). 분류 속성 제공."""

    def __init__(self, msg_cd, msg1, rt_cd="1", http_status=200):
        super().__init__(f"[{msg_cd}] {msg1} (rt_cd={rt_cd}, http={http_status})")
        self.msg_cd = str(msg_cd or "")
        self.msg1 = str(msg1 or "")
        self.rt_cd = str(rt_cd or "")
        self.http_status = int(http_status or 200)

    @property
    def rate_limited(self):
        return (self.msg_cd in RATE_LIMIT_CODES
                or self.http_status in (429, 500, 502, 503, 504))

    @property
    def token_error(self):
        return self.msg_cd in TOKEN_ERROR_CODES or self.http_status == 401

    @property
    def schema_error(self):
        return self.msg_cd in SCHEMA_ERROR_CODES


class KisTransportError(Exception):
    """curl 실행 레벨 오류 (exit != 0, 타임아웃 등)."""


def default_curl_runner(args, timeout=30):
    """curl 실행 → (http_status:int, body:str).

    ``--write-out %{http_code}`` 로 상태코드를 함께 수신 (HTTP 오류도 본문이
    JSON이므로 curl exit 0임). 테스트에서 이 runner를 주입해 모킹한다.
    """
    cmd = ["curl", "--silent", "--show-error", "--location",
           "--max-time", str(int(timeout)), "--compressed",
           "--write-out", "\n__HTTP_STATUS__%{http_code}"]
    cmd += list(args)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=int(timeout) + 10)
    except subprocess.TimeoutExpired as e:
        raise KisTransportError(f"curl timeout ({timeout}s)") from e
    if result.returncode != 0:
        raise KisTransportError(
            f"curl exit={result.returncode}: {result.stderr.strip()[:300]}")
    body, _, marker = result.stdout.rpartition("\n__HTTP_STATUS__")
    try:
        status = int(marker.strip()) if marker.strip() else 0
    except ValueError:
        status = 0
    return status, body


class TokenManager:
    """KIS 접근 토큰 캐시 (메모리 + 파일). 1분 1회 발급 제한 대응."""

    def __init__(self, appkey, appsecret, base_url, *, token_path=None,
                 min_ttl=TOKEN_MIN_TTL, rate_limit_sleep=60.0, max_retries=2,
                 sleep_fn=time.sleep, curl_runner=None, dry_run=False):
        self._appkey = appkey
        self._appsecret = appsecret
        self._base_url = base_url.rstrip("/")
        self._token_path = token_path
        self._min_ttl = min_ttl
        self._rate_limit_sleep = rate_limit_sleep
        self._max_retries = max_retries
        self._sleep = sleep_fn
        self._curl_runner = curl_runner or default_curl_runner
        self._dry_run = dry_run
        self._token = None
        self._expire_at = 0.0

    # ── 공개 API ───────────────────────────────────────────────────────
    def get_token(self) -> str:
        """유효 캐시가 있으면 반환, 아니면 발급."""
        if self._valid():
            return self._token
        return self._issue()

    def invalidate(self):
        """401 등으로 토큰이 무효해졌을 때 캐시 폐기 (다음 호출에서 재발급)."""
        self._token = None
        self._expire_at = 0.0
        self._remove_file()
        logger.info("토큰 캐시 무효화 (재발급 대기)")

    @property
    def token_expire_at(self) -> float:
        return self._expire_at

    # ── 캐시 ───────────────────────────────────────────────────────────
    def _valid(self) -> bool:
        if not self._token:
            return False
        return (self._expire_at - time.time()) > self._min_ttl

    def _load_file(self):
        if not self._token_path or not os.path.exists(self._token_path):
            return
        try:
            with open(self._token_path, encoding="utf-8") as f:
                data = json.load(f)
            token = data.get("access_token")
            if token and data.get("expire_at", 0) > time.time():
                self._token = token
                self._expire_at = float(data["expire_at"])
                logger.info("파일 캐시에서 토큰 재사용 (expire_at=%s)",
                            time.strftime("%Y-%m-%d %H:%M:%S",
                                          time.localtime(self._expire_at)))
        except Exception as e:  # 캐시 손상 시 그냥 재발급
            logger.warning("토큰 파일 캐시 로드 실패 (재발급): %s", e)

    def _save_file(self):
        if not self._token_path:
            return
        try:
            os.makedirs(os.path.dirname(self._token_path), exist_ok=True)
            with open(self._token_path, "w", encoding="utf-8") as f:
                json.dump({"access_token": self._token,
                           "expire_at": self._expire_at}, f)
        except OSError as e:
            logger.warning("토큰 파일 캐시 저장 실패: %s", e)

    def _remove_file(self):
        if self._token_path and os.path.exists(self._token_path):
            try:
                os.remove(self._token_path)
            except OSError as e:
                logger.warning("토큰 파일 캐시 삭제 실패: %s", e)

    # ── 발급 ───────────────────────────────────────────────────────────
    def _issue(self) -> str:
        if self._dry_run:
            self._token = _DRY_RUN_TOKEN
            self._expire_at = time.time() + TOKEN_EXPIRES_IN
            logger.info("dry-run: 토큰 발급 생략")
            return self._token

        self._load_file()
        if self._valid():
            return self._token

        url = self._base_url + TOKEN_ENDPOINT
        body = json.dumps({"grant_type": TOKEN_GRANT_TYPE,
                           "appkey": self._appkey,
                           "appsecret": self._appsecret})
        args = ["--request", "POST", "--url", url,
                "--header", "Content-Type: application/json",
                "--data", body]

        last_err = None
        for attempt in range(self._max_retries + 1):
            status, out = self._curl_runner(args, timeout=30)
            try:
                data = json.loads(out) if out else {}
            except json.JSONDecodeError as e:
                raise KisApiError("EGW-PARSE",
                                  f"토큰 응답이 JSON 아님 (http={status})",
                                  http_status=status) from e

            token = data.get("access_token")
            if token:
                self._token = token
                self._expire_at = (time.time()
                                   + int(data.get("expires_in", TOKEN_EXPIRES_IN)))
                self._save_file()
                return token

            err = KisApiError(data.get("msg_cd") or "EGW-UNKNOWN",
                              data.get("msg1") or data.get("message") or "unknown",
                              rt_cd=data.get("rt_cd", "1"), http_status=status)
            last_err = err
            if err.msg_cd in RATE_LIMIT_CODES and attempt < self._max_retries:
                # EGW00133 (1분 1회) — 대기 후 재시도 (테스트에선 sleep 주입으로 단축)
                logger.warning("토큰 발급 제한(%s) — %.0fs 대기 후 재시도 (%d/%d)",
                               err.msg_cd, self._rate_limit_sleep,
                               attempt + 1, self._max_retries + 1)
                self._sleep(self._rate_limit_sleep)
                continue
            raise err
        raise last_err  # 재시도 소진


class KisClient:
    """KIS 시세 API 클라이언트 (일봉/분봉).

    모든 시세 호출은 호출 간 ``delay + jitter`` 만큼 대기 (분당 제한 대비 여유).
    """

    def __init__(self, appkey, appsecret, base_url,
                 daily_tr_id="HHSTC16921500", minute_tr_id="HHSTC16922500",
                 *, delay=3.0, jitter=0.5, retry_max=5, retry_base_delay=2.0,
                 token_rate_limit_sleep=60.0, token_max_retries=2,
                 token_path=None, http_timeout=30, sleep_fn=time.sleep,
                 curl_runner=None, dry_run=False):
        self._appkey = appkey
        self._appsecret = appsecret
        self._base_url = base_url.rstrip("/")
        self._daily_tr_id = daily_tr_id
        self._minute_tr_id = minute_tr_id
        self._delay = max(0.0, float(delay))
        self._jitter = max(0.0, float(jitter))
        self._retry_max = int(retry_max)
        self._retry_base_delay = float(retry_base_delay)
        self._http_timeout = int(http_timeout)
        self._sleep = sleep_fn
        self._dry_run = dry_run
        self._curl_runner = (curl_runner or default_curl_runner)
        if dry_run:
            self._curl_runner = lambda args, timeout=30: (200, _DRY_RUN_BODY)
        self.tokens = TokenManager(
            appkey, appsecret, base_url, token_path=token_path,
            rate_limit_sleep=token_rate_limit_sleep,
            max_retries=token_max_retries, sleep_fn=sleep_fn,
            curl_runner=self._curl_runner, dry_run=dry_run)

    # ── 공개 API ───────────────────────────────────────────────────────
    def get_daily_chart(self, symbol, excd, date_from, date_to, count=1):
        """일봉: inquire-daily-itemchartprice (실전 tr_id FHKST03010100) → 전체 응답 dict."""
        params = {
            "FID_COND_MRKT_DIV_CODE": excd,        # J=KOSPI, K=KOSDAQ
            "FID_INPUT_ISCD": str(symbol),
            "FID_INPUT_DATE_1": str(date_from),
            "FID_INPUT_DATE_2": str(date_to),
            "FID_PERIOD_DIV_CODE": "D",
            "FID_ORG_ADJ_PRC": "0",
        }
        return self._quotes(DAILY_QUOTE_PATH, self._daily_tr_id, params)

    def get_minute_chart(self, symbol, excd, input_hour, period_div="0",
                         fid_cnt=100, include_past="Y"):
        """분봉 1페이지: inquire-time-itemchartprice (실전 tr_id FHKST03010200) → 전체 응답 dict.

        ``input_hour``(HHMMSS) 기준 시간 내림차순 응답. 페이지네이션은
        collectors.minute_collector 가 담당.
        """
        params = {
            "FID_COND_MRKT_DIV_CODE": excd,        # J=KOSPI, K=KOSDAQ
            "FID_INPUT_ISCD": str(symbol),
            "FID_INPUT_HOUR_1": str(input_hour),
            "FID_PW_DATA_INCU_YN": str(include_past),
            "FID_ETC_CLS_CODE": "",
        }
        return self._quotes(MINUTE_QUOTE_PATH, self._minute_tr_id, params)

    # ── 내부 ───────────────────────────────────────────────────────────
    def _sleep_before_call(self):
        """호출 간 보수적 대기 (기본 3s + jitter 0~0.5s)."""
        if self._delay > 0:
            self._sleep(self._delay + random.uniform(0, self._jitter))

    def _quotes(self, path, tr_id, params):
        self._sleep_before_call()
        return self._call_with_retry(lambda: self._request(path, tr_id, params))

    def _request(self, path, tr_id, params, token=None):
        token = token or self.tokens.get_token()
        url = self._base_url + path + "?" + urlencode(params)
        headers = {
            "authorization": f"Bearer {token}",
            "appkey": self._appkey,
            "appsecret": self._appsecret,
            "tr_id": tr_id,
            "content-type": "application/json; charset=utf-8",
        }
        args = ["--request", "GET", "--url", url]
        for name, value in headers.items():
            args += ["--header", f"{name}: {value}"]

        status, body = self._curl_runner(args, timeout=self._http_timeout)
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError as e:
            raise KisApiError("EGW-PARSE",
                              f"시세 응답이 JSON 아님 (http={status})",
                              http_status=status) from e

        # 토큰 무효 → 재발급 후 1회 재시도
        err_hint = KisApiError(data.get("msg_cd") or "",
                               data.get("msg1") or "", rt_cd=data.get("rt_cd", "0"),
                               http_status=status)
        if status == 401 or err_hint.token_error:
            logger.warning("토큰 만료 감지(%s) — 재발급 후 1회 재시도",
                           err_hint.msg_cd or status)
            self.tokens.invalidate()
            new_token = self.tokens.get_token()
            return self._request(path, tr_id, params, token=new_token)

        if data.get("rt_cd") == "0" or data.get("msg_cd") == "0":
            return data

        raise KisApiError(data.get("msg_cd") or "EGW-UNKNOWN",
                          data.get("msg1") or "unknown",
                          rt_cd=data.get("rt_cd", "1"), http_status=status)

    def _call_with_retry(self, fn):
        last_err = None
        for attempt in range(self._retry_max + 1):
            if attempt:
                backoff = self._retry_base_delay * (2 ** (attempt - 1))
                logger.info("재시도 %d/%d — %.1fs 후 (백오프)",
                            attempt, self._retry_max, backoff)
                self._sleep(backoff)
            try:
                return fn()
            except KisApiError as e:
                last_err = e
                if e.schema_error:
                    logger.error("스키마/필드 오류(%s) — 설정 버그, 재시도 중단: %s",
                                 e.msg_cd, e)
                    raise
                if not (e.rate_limited or e.token_error):
                    logger.error("비일시적 오류(%s) — 재시도 중단: %s", e.msg_cd, e)
                    raise
                logger.warning("일시적 오류(%s) — 재시도 대기 (attempt %d/%d)",
                               e.msg_cd, attempt + 1, self._retry_max + 1)
            except KisTransportError as e:
                last_err = e
                if attempt >= self._retry_max:
                    raise
                logger.warning("전송 오류 — 재시도 대기 (attempt %d/%d): %s",
                               attempt + 1, self._retry_max + 1, e)
        raise last_err

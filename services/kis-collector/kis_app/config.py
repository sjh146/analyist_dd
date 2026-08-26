"""KIS collector configuration.

⚠️ .env 파일을 직접 읽지 않는다 — 모든 값은 런타임 환경변수(os.getenv)에서만
취득한다. (docker-compose env_file → 프로세스 env 주입, 지시서 제약 3)
"""
import os


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


class Config:
    # ── PostgreSQL ─────────────────────────────────────────────────────
    POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
    POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
    POSTGRES_DB = os.getenv("POSTGRES_DB", "stock_trading")
    POSTGRES_USER = os.getenv("POSTGRES_USER", "stock_user")
    POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")

    # ── KIS 자격증명 (런타임 env 전용 — 파일에 하드코딩 금지) ─────────────
    KIS_APP_KEY = os.getenv("KIS_APP_KEY", "")
    KIS_APP_SECRET = os.getenv("KIS_APP_SECRET", "")
    # 실전 계좌 "7399634001" → CANO=73996340, ACNT_PRDT_CD=01 (시세 API엔 불필요,
    # 향후 주문/잔고 연계용으로 보관)
    KIS_ACCOUNT_NO = os.getenv("KIS_ACCOUNT_NO", "")

    # ── KIS API ────────────────────────────────────────────────────────
    KIS_BASE_URL = os.getenv(
        "KIS_BASE_URL", "https://openapi.koreainvestment.com:9443")
    # 실전 tr_id (공식 가이드 기준 — Hermes 실측 확정 항목, PLAN §9)
    KIS_DAILY_TR_ID = os.getenv("KIS_DAILY_TR_ID", "FHKST03010100")
    KIS_MINUTE_TR_ID = os.getenv("KIS_MINUTE_TR_ID", "FHKST03010200")

    # ── 보수적 호출 제한 ────────────────────────────────────────────────
    KIS_REQUEST_DELAY = float(os.getenv("KIS_REQUEST_DELAY", "3.0"))
    KIS_REQUEST_JITTER = float(os.getenv("KIS_REQUEST_JITTER", "0.5"))
    KIS_RETRY_MAX = int(os.getenv("KIS_RETRY_MAX", "5"))
    KIS_RETRY_BASE_DELAY = float(os.getenv("KIS_RETRY_BASE_DELAY", "2.0"))
    KIS_TOKEN_RATE_LIMIT_SLEEP = float(os.getenv("KIS_TOKEN_RATE_LIMIT_SLEEP", "60.0"))
    KIS_TOKEN_MAX_RETRIES = int(os.getenv("KIS_TOKEN_MAX_RETRIES", "2"))
    KIS_HTTP_TIMEOUT = int(os.getenv("KIS_HTTP_TIMEOUT", "30"))

    # 토큰 캐시 파일 (JWT만 저장 — appkey/secret 절대 저장 안 함)
    KIS_TOKEN_PATH = os.getenv("KIS_TOKEN_PATH", "data/kis/token_cache.json")

    # ── 수집 범위 ───────────────────────────────────────────────────────
    KIS_MINUTE_FID_CNT = int(os.getenv("KIS_MINUTE_FID_CNT", "100"))
    KIS_MINUTE_MAX_PAGES = int(os.getenv("KIS_MINUTE_MAX_PAGES", "10"))

    # ── dry-run: 실제 HTTP/DB 호출 없이 흐름 점검 ───────────────────────
    KIS_DRY_RUN = _env_bool("KIS_DRY_RUN", False)

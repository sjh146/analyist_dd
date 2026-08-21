"""
내부 API (M4) — cmall-api 전용. /internal/*
- 외부에서 직접 접근 금지 (cmall-api 단일 관문 원칙)
- 인증: X-Internal-Api-Key == INTERNAL_API_KEY (env), 미설정 시 fail-closed(503)
- 분석 실행: 기존 파이프라인 데이터(예측/감성/시세)를 합성한 온디맨드 리포트
"""
import logging
import os
import uuid
import urllib.request
import urllib.error
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# fail-closed: INTERNAL_API_KEY 미설정이면 내부 API 자체를 503으로 거부
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "")
INTERNAL_CONFIGURED = bool(INTERNAL_API_KEY)

# job-runner (온디맨드 전략 실행기) — 미설정 시 잡 타입 요청은 503
JOB_RUNNER_URL = os.getenv("JOB_RUNNER_URL", "")

if not INTERNAL_CONFIGURED:
    logger.warning("INTERNAL_API_KEY not set — /internal/* will be disabled (fail-closed)")


async def verify_internal_key(x_internal_api_key: str = Header(None)):
    if not INTERNAL_CONFIGURED:
        raise HTTPException(status_code=503, detail="internal API not configured")
    if not x_internal_api_key or x_internal_api_key != INTERNAL_API_KEY:
        raise HTTPException(status_code=401, detail="invalid internal key")


internal_router = APIRouter(prefix="/internal", dependencies=[Depends(verify_internal_key)])


class AnalysisRequest(BaseModel):
    symbol: str
    request_type: str = "stock_report"


class JobRunRequest(BaseModel):
    """온디맨드 잡 트리거 바디 (stock_report는 symbol 필요)."""
    symbol: Optional[str] = None


# cmall Go가 허용하는 분석 요청 타입 (백엔드 allowlist와 동일)
ALLOWED_ANALYSIS_TYPES = {"stock_report", "swing_screener", "backtest", "factor_report", "close_screener"}


def _job_runner_request(method: str, path: str, payload=None, timeout: int = 10):
    """job-runner HTTP 호출 (내부망, X-Internal-Api-Key). 실패 시 502."""
    url = JOB_RUNNER_URL + path
    data = None
    if payload is not None:
        import json as _json
        data = _json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json", "X-Internal-Api-Key": INTERNAL_API_KEY},
    )
    import json as _json
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return _json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"job-runner returned {e.code}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"job-runner unavailable: {type(e).__name__}")


def _pg_conn():
    """Get PostgreSQL connection (main.py와 동일 패턴)."""
    from app.main import state, config  # 지연 import — 순환 참조 회피

    if state.pg_conn is None or state.pg_conn.closed:
        try:
            import psycopg2

            state.pg_conn = psycopg2.connect(
                host=config.POSTGRES_HOST, port=config.POSTGRES_PORT,
                dbname=config.POSTGRES_DB, user=config.POSTGRES_USER,
                password=config.POSTGRES_PASSWORD,
            )
        except Exception as e:  # pragma: no cover
            logger.error(f"DB connection failed: {e}")
            return None
    return state.pg_conn


def _query(sql: str, params: tuple, limit: int = 50) -> list:
    """쿼리 헬퍼 — 실패 시 빈 리스트 (리포트 부분 실패 허용)."""
    conn = _pg_conn()
    if not conn:
        return []
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.close()
        return rows
    except Exception as e:
        logger.error("query failed: %s", e, exc_info=True)
        return []


def _build_stock_report(symbol: str) -> dict:
    """온디맨드 주식 분석 리포트 (예측 + 감성 + 최근 시세 합성)."""
    symbol = symbol.strip().upper()
    if not symbol:
        raise HTTPException(status_code=400, detail="symbol required")

    # ① 최근 ML 예측
    preds = _query(
        """
        SELECT prediction_date, model_version, predicted_direction, predicted_change_pct, confidence
        FROM ml_predictions WHERE stock_code = %s
        ORDER BY prediction_date DESC LIMIT 3
        """,
        (symbol,),
    )
    # ② 최근 감성
    senti = _query(
        """
        SELECT sentiment_date, sentiment_score, sentiment_label
        FROM stock_sentiment WHERE stock_code = %s
        ORDER BY sentiment_date DESC LIMIT 5
        """,
        (symbol,),
    )
    # ③ 최근 시세 (마지막 5일)
    mkt = _query(
        """
        SELECT trade_date, open_price, high_price, low_price, close_price, volume
        FROM market_data WHERE stock_code = %s
        ORDER BY trade_date DESC LIMIT 5
        """,
        (symbol,),
    )

    predictions = [
        {
            "date": r[0].isoformat() if r[0] else None,
            "model": r[1],
            "direction": r[2],
            "change_pct": float(r[3]) if r[3] is not None else 0,
            "confidence": float(r[4]) if r[4] is not None else 0,
        }
        for r in preds
    ]
    sentiment = [
        {
            "date": r[0].isoformat() if r[0] else None,
            "score": float(r[1]) if r[1] is not None else 0,
            "label": r[2],
        }
        for r in senti
    ]
    market = [
        {
            "trade_date": r[0].isoformat() if r[0] else None,
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "volume": int(r[5]),
        }
        for r in mkt
    ]

    # 리포트 요약 (기존 파이프라인 데이터 기반 — 신규 ML 실행 없음)
    latest_pred = predictions[0] if predictions else None
    latest_senti = sentiment[0] if sentiment else None
    latest_close = market[0]["close"] if market else None

    summary = {
        "symbol": symbol,
        "request_type": req.request_type,
        "verdict": (
            latest_pred.get("direction") if latest_pred else "no_data"
        ),
        "confidence": latest_pred.get("confidence") if latest_pred else None,
        "sentiment_label": latest_senti.get("label") if latest_senti else None,
        "last_close": latest_close,
        "data_sources": ["ml_predictions", "stock_sentiment", "market_data"],
    }

    request_id = str(uuid.uuid4())
    return {
        "summary": summary,
        "predictions": predictions,
        "sentiment": sentiment,
        "market_data": market,
    }


@internal_router.post("/analysis")
async def run_analysis(req: AnalysisRequest):
    """온디맨드 분석 리포트 (기존 동기 경로 — stock_report)."""
    report = _build_stock_report(req.symbol)
    request_id = str(uuid.uuid4())
    return {
        "request_id": request_id,
        "status": "done",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        **report,
    }


@internal_router.get("/signals/{symbol}")
async def get_signals(symbol: str):
    """최신 트레이딩 신호 (예측 기반)."""
    symbol = symbol.strip().upper()
    rows = _query(
        """
        SELECT prediction_date, predicted_direction, predicted_change_pct, confidence
        FROM ml_predictions WHERE stock_code = %s
        ORDER BY prediction_date DESC LIMIT 5
        """,
        (symbol,),
    )
    signals = [
        {
            "signal_id": f"sig-{r[0].isoformat()}-{symbol}",
            "date": r[0].isoformat() if r[0] else None,
            "action": "buy" if r[1] == "up" else ("sell" if r[1] == "down" else "hold"),
            "direction": r[1],
            "change_pct": float(r[2]) if r[2] is not None else 0,
            "confidence": float(r[3]) if r[3] is not None else 0,
            "strategy_name": "ml-ensemble",
        }
        for r in rows
    ]
    return {"symbol": symbol, "signals": signals}


# ── 리포트 산출물 상품 (M6: 스윙 스크리너 / 백테스트 / 강환국 팩터) ─────────
# 배치 파이프라인이 생성한 JSON을 HTTP로 노출한다. (읽기 전용, 파일 경로 비노출)

import json as _json

# 보고서 경로: 컨테이너 마운트 기준 (docker-compose에서 ./reports → /app/reports 등)
REPORTS_DIR = os.getenv("REPORTS_DIR", "/app/reports")
FACTOR_REPORTS_DIR = os.getenv("FACTOR_REPORTS_DIR", "/app/factor_reports")

# 경로 허용 목록 — 임의 파일 읽기 방지 (CWE-22)
_REPORT_FILES = {
    "swing_screener": os.path.join(REPORTS_DIR, "swing_latest.json"),
    "backtest": os.path.join(REPORTS_DIR, "backtest_result.json"),
    "factor_report": os.path.join(FACTOR_REPORTS_DIR, "factor_strategies_result.json"),
    "close_screener": os.path.join(REPORTS_DIR, "close_latest.json"),
}


def _read_report(name: str):
    """허용 목록 내 리포트 JSON 로드. 실패 시 None."""
    path = _REPORT_FILES.get(name)
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return _json.load(f)
    except (OSError, _json.JSONDecodeError) as e:
        logger.warning("report %s read failed: %s", name, e)
        return None


@internal_router.get("/swing-screener")
async def get_swing_screener():
    """스윙종목 스크리너 최신 결과 (swing_latest.json)."""
    data = _read_report("swing_screener")
    if data is None:
        raise HTTPException(status_code=404, detail="swing screener report not available")
    return {"report": "swing_screener", "data": data}


@internal_router.get("/backtest")
async def get_backtest():
    """모델 백테스트 결과 (backtest_result.json)."""
    data = _read_report("backtest")
    if data is None:
        raise HTTPException(status_code=404, detail="backtest report not available")
    return {"report": "backtest", "data": data}


@internal_router.get("/factor-report")
async def get_factor_report():
    """강환국 투자팩터 5종 결과 (factor_strategies_result.json)."""
    data = _read_report("factor_report")
    if data is None:
        raise HTTPException(status_code=404, detail="factor report not available")
    return {"report": "factor_report", "data": data}


# ── 온디맨드 분석 잡 (M6: 결제 후 전략 실행 — job-runner 위임) ──────────────

@internal_router.post("/analysis/{request_type}")
async def run_analysis_job(request_type: str, req: JobRunRequest):
    """비동기 분석 잡 트리거.

    - stock_report: DB 합성 (즉시 done, result 포함)
    - backtest / swing_screener / factor_report: job-runner에 위임 (queued → 폴링)
    """
    if request_type not in ALLOWED_ANALYSIS_TYPES:
        raise HTTPException(status_code=400, detail="unsupported request type")

    if request_type == "stock_report":
        symbol = (req.symbol or "").strip().upper()
        if not symbol:
            raise HTTPException(status_code=400, detail="symbol required for stock_report")
        report = _build_stock_report(symbol)
        return {
            "request_id": str(uuid.uuid4()),
            "status": "done",
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "result": report,
        }

    if not JOB_RUNNER_URL:
        raise HTTPException(status_code=503, detail="job-runner not configured")
    resp = _job_runner_request("POST", "/run", {"request_type": request_type, "symbol": req.symbol})
    return {"request_id": resp["request_id"], "status": resp.get("status", "queued")}


@internal_router.get("/analysis/{request_id}")
async def get_analysis_job(request_id: str):
    """비동기 분석 잡 상태 조회 (job-runner 프록시)."""
    if not JOB_RUNNER_URL:
        raise HTTPException(status_code=503, detail="job-runner not configured")
    job = _job_runner_request("GET", f"/jobs/{request_id}")
    return {
        "request_id": request_id,
        "status": job.get("status"),
        "result": job.get("result"),
        "error": job.get("error"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
    }

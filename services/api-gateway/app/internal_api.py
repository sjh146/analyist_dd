"""
내부 API (M4) — cmall-api 전용. /internal/*
- 외부에서 직접 접근 금지 (cmall-api 단일 관문 원칙)
- 인증: X-Internal-Api-Key == INTERNAL_API_KEY (env), 미설정 시 fail-closed(503)
- 분석 실행: 기존 파이프라인 데이터(예측/감성/시세)를 합성한 온디맨드 리포트
"""
import logging
import os
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# fail-closed: INTERNAL_API_KEY 미설정이면 내부 API 자체를 503으로 거부
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "")
INTERNAL_CONFIGURED = bool(INTERNAL_API_KEY)

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


@internal_router.post("/analysis")
async def run_analysis(req: AnalysisRequest):
    """온디맨드 분석 리포트 (예측 + 감성 + 최근 시세 합성)."""
    symbol = req.symbol.strip().upper()
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
        "request_id": request_id,
        "status": "done",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "summary": summary,
        "predictions": predictions,
        "sentiment": sentiment,
        "market_data": market,
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

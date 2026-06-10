"""
API Gateway - FastAPI REST API
Provides HTTP endpoints for all system components.
"""

import json
import logging
from typing import Optional, List
from datetime import date, datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.config import Config

logging.basicConfig(level="INFO")
logger = logging.getLogger(__name__)
config = Config()

# Pydantic models
class Stock(BaseModel):
    stock_code: str
    stock_name: str
    market: str
    sector: Optional[str] = None

class MarketData(BaseModel):
    trade_date: date
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: int

class SimilarStock(BaseModel):
    stock_code: str
    stock_name: str
    similarity: float

class Prediction(BaseModel):
    stock_code: str
    prediction_date: str
    predicted_direction: str
    confidence: float

class TradeSignal(BaseModel):
    signal_id: str
    action: str
    stock_code: str
    quantity: int
    strategy_name: str
    reason: str
    confidence: float
    timestamp: str

class HealthStatus(BaseModel):
    status: str
    services: dict
    timestamp: str


# Application state
class AppState:
    def __init__(self):
        self.pg_conn = None
        self.neo4j_driver = None
        self.redis_client = None

state = AppState()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    logger.info("API Gateway starting...")
    yield
    # Shutdown
    logger.info("API Gateway shutting down...")


app = FastAPI(
    title="주식 자동매매 시스템 API",
    description="Korean Stock Auto Trading System REST API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _pg_conn():
    """Get PostgreSQL connection."""
    if state.pg_conn is None or state.pg_conn.closed:
        try:
            import psycopg2
            state.pg_conn = psycopg2.connect(
                host=config.POSTGRES_HOST, port=config.POSTGRES_PORT,
                dbname=config.POSTGRES_DB, user=config.POSTGRES_USER,
                password=config.POSTGRES_PASSWORD,
            )
        except Exception as e:
            logger.error(f"DB connection failed: {e}")
            return None
    return state.pg_conn


# =================== HEALTH ENDPOINTS ===================


@app.get("/health")
async def health_check():
    """Basic health check."""
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


@app.get("/api/v1/status", response_model=HealthStatus)
async def system_status():
    """System status check."""
    services = {"api": "ok", "postgres": "unknown", "redis": "unknown"}

    # Check PostgreSQL
    conn = _pg_conn()
    if conn:
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.close()
            services["postgres"] = "ok"
        except:
            services["postgres"] = "error"

    return HealthStatus(
        status="running",
        services=services,
        timestamp=datetime.now().isoformat(),
    )


# =================== STOCK ENDPOINTS ===================


@app.get("/api/v1/stocks", response_model=List[Stock])
async def list_stocks(
    market: Optional[str] = Query(None, description="KOSPI or KOSDAQ"),
    sector: Optional[str] = Query(None, description="Sector filter"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
):
    """List all stocks with optional filters."""
    conn = _pg_conn()
    if not conn:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        cur = conn.cursor()
        query = "SELECT stock_code, stock_name, market, sector FROM stocks WHERE 1=1"
        params = []

        if market:
            params.append(market)
            query += f" AND market = %s"
        if sector:
            params.append(sector)
            query += f" AND sector = %s"

        query += " ORDER BY market_cap DESC NULLS LAST LIMIT %s OFFSET %s"
        params.extend([limit, skip])

        cur.execute(query, params)
        rows = cur.fetchall()
        cur.close()
        return [Stock(stock_code=r[0], stock_name=r[1], market=r[2], sector=r[3]) for r in rows]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/stocks/{stock_code}")
async def get_stock(stock_code: str):
    """Get stock details."""
    conn = _pg_conn()
    if not conn:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT stock_code, stock_name, market, sector, industry, market_cap FROM stocks WHERE stock_code = %s",
            (stock_code,),
        )
        row = cur.fetchone()
        cur.close()
        if not row:
            raise HTTPException(status_code=404, detail="Stock not found")
        return {
            "stock_code": row[0], "stock_name": row[1], "market": row[2],
            "sector": row[3], "industry": row[4], "market_cap": row[5],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/stocks/{stock_code}/market-data", response_model=List[MarketData])
async def get_market_data(
    stock_code: str,
    days: int = Query(30, ge=1, le=365),
):
    """Get market data for a stock."""
    conn = _pg_conn()
    if not conn:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT trade_date, open_price, high_price, low_price, close_price, volume
            FROM market_data
            WHERE stock_code = %s
            ORDER BY trade_date DESC
            LIMIT %s
            """,
            (stock_code, days),
        )
        rows = cur.fetchall()
        cur.close()
        return [
            MarketData(
                trade_date=r[0], open_price=float(r[1]), high_price=float(r[2]),
                low_price=float(r[3]), close_price=float(r[4]), volume=int(r[5]),
            )
            for r in rows
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/stocks/{stock_code}/sentiment")
async def get_stock_sentiment(stock_code: str, days: int = 30):
    """Get sentiment data for a stock."""
    conn = _pg_conn()
    if not conn:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT analysis_date, avg_sentiment, sentiment_count,
                   positive_count, negative_count, neutral_count
            FROM stock_sentiment
            WHERE stock_code = %s
            ORDER BY analysis_date DESC
            LIMIT %s
            """,
            (stock_code, days),
        )
        rows = cur.fetchall()
        cur.close()
        return [
            {
                "date": r[0].isoformat(),
                "avg_sentiment": float(r[1]),
                "count": r[2],
                "positive": r[3],
                "negative": r[4],
                "neutral": r[5],
            }
            for r in rows
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =================== VECTOR SEARCH ENDPOINTS ===================


@app.get("/api/v1/vectors/similar/{stock_code}", response_model=List[SimilarStock])
async def find_similar_stocks(
    stock_code: str,
    top_k: int = Query(10, ge=1, le=50),
    vector_type: str = Query("combined", regex="^(price_pattern|fundamental|sentiment|combined)$"),
):
    """Find similar stocks using vector similarity."""
    conn = _pg_conn()
    if not conn:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT sv.stock_code, s.stock_name,
                   1 - (sv.embedding <=> (
                       SELECT embedding FROM stock_vectors
                       WHERE stock_code = %s AND vector_type = %s
                   )) as similarity
            FROM stock_vectors sv
            JOIN stocks s ON sv.stock_code = s.stock_code
            WHERE sv.vector_type = %s AND sv.stock_code != %s
            ORDER BY similarity DESC
            LIMIT %s
            """,
            (stock_code, vector_type, vector_type, stock_code, top_k),
        )
        rows = cur.fetchall()
        cur.close()
        return [
            SimilarStock(stock_code=r[0], stock_name=r[1], similarity=float(r[2]))
            for r in rows
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =================== PREDICTION ENDPOINTS ===================


@app.get("/api/v1/predictions/{stock_code}")
async def get_predictions(stock_code: str, days: int = 7):
    """Get ML predictions for a stock."""
    conn = _pg_conn()
    if not conn:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT prediction_date, model_version, predicted_direction,
                   predicted_change_pct, confidence
            FROM ml_predictions
            WHERE stock_code = %s
            ORDER BY prediction_date DESC
            LIMIT %s
            """,
            (stock_code, days),
        )
        rows = cur.fetchall()
        cur.close()
        return [
            {
                "date": r[0].isoformat(),
                "model": r[1],
                "direction": r[2],
                "change_pct": float(r[3]) if r[3] else 0,
                "confidence": float(r[4]),
            }
            for r in rows
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/predictions/top")
async def get_top_predictions(
    top_n: int = Query(10, ge=1, le=50),
    direction: Optional[str] = Query(None, regex="^(up|down)$"),
):
    """Get top predictions by confidence."""
    conn = _pg_conn()
    if not conn:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        cur = conn.cursor()
        query = """
            SELECT m.stock_code, s.stock_name, m.predicted_direction,
                   m.confidence, m.prediction_date
            FROM ml_predictions m
            JOIN stocks s ON m.stock_code = s.stock_code
            WHERE m.prediction_date = (SELECT MAX(prediction_date) FROM ml_predictions)
        """
        params = []
        if direction:
            query += " AND m.predicted_direction = %s"
            params.append(direction)
        query += " ORDER BY m.confidence DESC LIMIT %s"
        params.append(top_n)

        cur.execute(query, params)
        rows = cur.fetchall()
        cur.close()
        return [
            {
                "stock_code": r[0], "stock_name": r[1],
                "direction": r[2], "confidence": float(r[3]),
                "date": r[4].isoformat(),
            }
            for r in rows
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =================== TRADING ENDPOINTS ===================


@app.get("/api/v1/trading/orders")
async def get_orders(
    status: Optional[str] = Query(None, regex="^(pending|submitted|filled|cancelled)$"),
    limit: int = Query(50, ge=1, le=200),
):
    """Get trade orders."""
    conn = _pg_conn()
    if not conn:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        cur = conn.cursor()
        query = """
            SELECT id, stock_code, order_type, quantity, price, order_status,
                   strategy_name, created_at
            FROM trade_orders
        """
        params = []
        if status:
            query += " WHERE order_status = %s"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT %s"
        params.append(limit)

        cur.execute(query, params)
        rows = cur.fetchall()
        cur.close()
        return [
            {
                "id": r[0], "stock_code": r[1], "order_type": r[2],
                "quantity": r[3], "price": float(r[4]) if r[4] else 0,
                "status": r[5], "strategy": r[6],
                "created_at": r[7].isoformat(),
            }
            for r in rows
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/trading/positions")
async def get_positions():
    """Get current positions."""
    conn = _pg_conn()
    if not conn:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT p.stock_code, p.quantity, p.avg_buy_price, p.current_price,
                   p.unrealized_pnl, p.realized_pnl
            FROM positions p
            JOIN stocks s ON p.stock_code = s.stock_code
            WHERE p.quantity > 0
            ORDER BY p.updated_at DESC
        """)
        rows = cur.fetchall()
        cur.close()
        return [
            {
                "stock_code": r[0], "quantity": r[1],
                "avg_buy_price": float(r[2]) if r[2] else 0,
                "current_price": float(r[3]) if r[3] else 0,
                "unrealized_pnl": float(r[4]) if r[4] else 0,
                "realized_pnl": float(r[5]) if r[5] else 0,
            }
            for r in rows
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =================== STRATEGY ENDPOINTS ===================


@app.get("/api/v1/strategies")
async def get_strategies():
    """Get all trading strategies."""
    conn = _pg_conn()
    if not conn:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT strategy_name, strategy_type, parameters, is_active FROM strategy_config"
        )
        rows = cur.fetchall()
        cur.close()
        return [
            {
                "name": r[0], "type": r[1],
                "parameters": r[2], "active": r[3],
            }
            for r in rows
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =================== DASHBOARD ENDPOINTS ===================


@app.get("/api/v1/dashboard/summary")
async def get_dashboard_summary():
    """Get dashboard summary."""
    conn = _pg_conn()
    if not conn:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        cur = conn.cursor()

        # Total stocks
        cur.execute("SELECT COUNT(*) FROM stocks")
        total_stocks = cur.fetchone()[0]

        # Total predictions today
        cur.execute(
            "SELECT COUNT(*) FROM ml_predictions WHERE prediction_date = CURRENT_DATE"
        )
        today_predictions = cur.fetchone()[0]

        # Open positions
        cur.execute("SELECT COUNT(*) FROM positions WHERE quantity > 0")
        open_positions = cur.fetchone()[0]

        # Pending orders
        cur.execute(
            "SELECT COUNT(*) FROM trade_orders WHERE order_status = 'pending'"
        )
        pending_orders = cur.fetchone()[0]

        cur.close()

        return {
            "total_stocks": total_stocks,
            "today_predictions": today_predictions,
            "open_positions": open_positions,
            "pending_orders": pending_orders,
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

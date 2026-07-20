import logging
from datetime import datetime, date

logger = logging.getLogger(__name__)

US_TICKERS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA",
    "JPM", "V", "JNJ", "WMT", "PG", "UNH", "HD", "MA", "BAC",
    "DIS", "NFLX", "ADBE", "CRM", "INTC", "AMD", "QCOM", "TXN",
    "CSCO", "ORCL", "IBM",
]

KOREAN_QUARTERS = [
    ("Q4", 1, 31),
    ("Q1", 4, 30),
    ("Q2", 7, 31),
    ("Q3", 10, 31),
]


class EarningsCollector:
    def collect(self) -> list[dict]:
        events = []
        events.extend(self._collect_us())
        events.extend(self._collect_korea())
        return events

    def _collect_us(self) -> list[dict]:
        events = []
        for ticker in US_TICKERS:
            try:
                ev = self._try_yfinance(ticker)
                if ev:
                    events.append(ev)
            except Exception:
                pass
        if not events:
            logger.warning("yfinance failed for all US tickers, using fallback")
            events = self._us_fallback()
        return events

    def _try_yfinance(self, ticker: str) -> dict | None:
        import yfinance as yf

        cal = yf.Ticker(ticker).calendar
        if cal and "Earnings Date" in cal:
            ed = cal["Earnings Date"]
            ed_str = ed.strftime("%Y-%m-%d") if hasattr(ed, "strftime") else str(ed)
            return {
                "event_date": ed_str,
                "country": "US",
                "category": "earnings",
                "title": f"{ticker} Earnings",
                "importance": "high",
                "source": "yfinance",
            }
        return None

    def _us_fallback(self) -> list[dict]:
        events = []
        now = datetime.now()
        for ticker in US_TICKERS:
            for month_day in [(1, 15), (4, 15), (7, 15), (10, 15)]:
                try:
                    y = now.year
                    d = date(y, month_day[0], month_day[1])
                    if d < now.date():
                        d = date(y + 1, month_day[0], month_day[1])
                    events.append({
                        "event_date": d.isoformat(),
                        "country": "US",
                        "category": "earnings",
                        "title": f"{ticker} Earnings (estimated)",
                        "importance": "high",
                        "source": "fallback_estimate",
                    })
                except Exception:
                    pass
        return events

    def _collect_korea(self) -> list[dict]:
        now = datetime.now()
        events = []
        for quarter_label, q_month, q_day in KOREAN_QUARTERS:
            for y in [now.year, now.year + 1]:
                try:
                    d = date(y, q_month, q_day)
                    if d < now.date():
                        continue
                    events.append({
                        "event_date": d.isoformat(),
                        "country": "KR",
                        "category": "earnings",
                        "title": f"Korea {quarter_label} Earnings Season",
                        "importance": "high",
                        "source": "korea_quarterly_schedule",
                    })
                except Exception:
                    pass
        return events


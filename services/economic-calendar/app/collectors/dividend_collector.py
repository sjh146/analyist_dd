import logging
from datetime import datetime, date

logger = logging.getLogger(__name__)

MAJOR_DIVIDENDS = [
    ("AAPL", "2026-02-14", "US"),
    ("AAPL", "2026-05-15", "US"),
    ("AAPL", "2026-08-14", "US"),
    ("AAPL", "2026-11-13", "US"),
    ("MSFT", "2026-02-19", "US"),
    ("MSFT", "2026-05-21", "US"),
    ("MSFT", "2026-08-20", "US"),
    ("MSFT", "2026-11-19", "US"),
    ("JPM", "2026-01-05", "US"),
    ("JPM", "2026-04-06", "US"),
    ("JPM", "2026-07-06", "US"),
    ("JPM", "2026-10-05", "US"),
    ("V", "2026-02-14", "US"),
    ("V", "2026-05-15", "US"),
    ("V", "2026-08-14", "US"),
    ("V", "2026-11-13", "US"),
    ("005930", "2026-04-15", "KR"),
    ("005930", "2026-09-30", "KR"),
    ("000660", "2026-04-15", "KR"),
    ("000660", "2026-09-30", "KR"),
]


class DividendCollector:
    def collect(self) -> list[dict]:
        events = []
        try:
            kr_events = self._try_pykrx()
            events.extend(kr_events)
        except Exception:
            logger.warning("pykrx failed, using static dividend data")
        events.extend(self._static_fallback())
        return events

    def _try_pykrx(self) -> list[dict]:
        try:
            from pykrx import stock
            from pykrx import bond
        except ImportError:
            raise ImportError("pykrx not installed")

        now = datetime.now()
        events = []
        for ticker in ["005930", "000660", "035420", "051910", "068270"]:
            try:
                df = stock.get_market_fundamental_by_date(
                    now.strftime("%Y%m%d"),
                    now.strftime("%Y%m%d"),
                    ticker,
                )
                if df is not None and not df.empty:
                    dps = df.iloc[-1].get("DPS", 0)
                    if dps and dps > 0:
                        events.append({
                            "event_date": now.strftime("%Y-%m-%d"),
                            "country": "KR",
                            "category": "dividend",
                            "title": f"{ticker} DPS: {dps:,}",
                            "importance": "medium",
                            "source": "pykrx",
                        })
            except Exception:
                pass
        return events

    def _static_fallback(self) -> list[dict]:
        events = []
        for ticker, ex_date_str, country in MAJOR_DIVIDENDS:
            try:
                d = date.fromisoformat(ex_date_str)
                if d >= datetime.now().date():
                    events.append({
                        "event_date": ex_date_str,
                        "country": country,
                        "category": "dividend",
                        "title": f"{ticker} Ex-Dividend Date",
                        "importance": "medium",
                        "source": "static_fallback",
                    })
            except Exception:
                pass
        return events

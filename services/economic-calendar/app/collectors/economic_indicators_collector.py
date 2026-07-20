import logging
from datetime import datetime, date, timedelta

logger = logging.getLogger(__name__)


US_GDP_MONTHS = [1, 4, 7, 10]  # Advance release quarters
KR_GDP_MONTHS = [1, 4, 7, 10]


class EconomicIndicatorsCollector:
    def collect(self) -> list[dict]:
        events = []
        events.extend(self._collect_us_gdp())
        events.extend(self._collect_us_monthly())
        events.extend(self._collect_fomc_minutes())
        events.extend(self._collect_kr_indicators())
        return events

    def _collect_us_gdp(self) -> list[dict]:
        now = datetime.now()
        events = []
        for m in US_GDP_MONTHS:
            for y in [now.year, now.year + 1]:
                try:
                    d = date(y, m, 30)
                    if d < now.date():
                        continue
                    events.append({
                        "event_date": d.isoformat(),
                        "country": "US",
                        "category": "gdp",
                        "title": f"US GDP Advance (Q{(m // 3) + 1})" if m < 10 else "US GDP Advance (Q4)",
                        "importance": "high",
                        "source": "static_schedule",
                    })
                except Exception:
                    pass
        return events

    def _collect_us_monthly(self) -> list[dict]:
        now = datetime.now()
        indicators = [
            ("pce", "US PCE Price Index", "high"),
            ("cpi", "US CPI", "high"),
            ("nonfarm_payrolls", "US Nonfarm Payrolls", "high"),
            ("lei", "US Leading Economic Index (LEI)", "medium"),
        ]
        events = []
        for m in range(1, 13):
            for y in [now.year, now.year + 1]:
                try:
                    d = date(y, m, min(28, 15 + (m % 3) * 5))
                    if d < now.date():
                        continue
                    for cat, title, importance in indicators:
                        events.append({
                            "event_date": d.isoformat(),
                            "country": "US",
                            "category": cat,
                            "title": title,
                            "importance": importance,
                            "source": "static_schedule",
                        })
                except Exception:
                    pass
        return events

    def _collect_fomc_minutes(self) -> list[dict]:
        try:
            from app.collectors.fomc_collector import FOMC_MEETINGS_2026
        except ImportError:
            from collectors.fomc_collector import FOMC_MEETINGS_2026

        events = []
        for day1, day2 in FOMC_MEETINGS_2026:
            try:
                d1 = date.fromisoformat(day1)
                minutes_date = d1 + timedelta(weeks=3)
                if minutes_date >= datetime.now().date():
                    events.append({
                        "event_date": minutes_date.isoformat(),
                        "country": "US",
                        "category": "fed",
                        "title": f"FOMC Minutes ({day1[:7]})",
                        "importance": "high",
                        "source": "static_schedule",
                    })
            except Exception:
                pass
        return events

    def _collect_kr_indicators(self) -> list[dict]:
        now = datetime.now()
        events = []
        for m in range(1, 13):
            for y in [now.year, now.year + 1]:
                try:
                    d = date(y, m, min(28, 25))
                    if d < now.date():
                        continue
                    events.append({
                        "event_date": d.isoformat(),
                        "country": "KR",
                        "category": "gdp",
                        "title": f"Korea GDP (Q{(m - 1) // 3 + 1})" if m in KR_GDP_MONTHS else None,
                        "importance": "high",
                        "source": "static_schedule",
                    })
                    events.append({
                        "event_date": d.isoformat(),
                        "country": "KR",
                        "category": "cpi",
                        "title": "Korea CPI",
                        "importance": "high",
                        "source": "static_schedule",
                    })
                    events.append({
                        "event_date": d.isoformat(),
                        "country": "KR",
                        "category": "trade_balance",
                        "title": "Korea Trade Balance",
                        "importance": "medium",
                        "source": "static_schedule",
                    })
                    events.append({
                        "event_date": d.isoformat(),
                        "country": "KR",
                        "category": "industrial_production",
                        "title": "Korea Industrial Production",
                        "importance": "medium",
                        "source": "static_schedule",
                    })
                except Exception:
                    pass
        result = [e for e in events if e["title"] is not None]
        return result

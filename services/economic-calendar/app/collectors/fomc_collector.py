from datetime import date

FOMC_MEETINGS_2026 = [
    ("2026-01-28", "2026-01-29"),
    ("2026-03-18", "2026-03-19"),
    ("2026-05-06", "2026-05-07"),
    ("2026-06-17", "2026-06-18"),
    ("2026-07-29", "2026-07-30"),
    ("2026-09-16", "2026-09-17"),
    ("2026-11-04", "2026-11-05"),
    ("2026-12-15", "2026-12-16"),
]


class FOMCCollector:
    def collect(self) -> list[dict]:
        events = []
        for day1, day2 in FOMC_MEETINGS_2026:
            events.append({
                "event_date": day1,
                "country": "US",
                "category": "fed",
                "title": f"FOMC Meeting Day 1 ({day1})",
                "importance": "high",
                "source": "fomc_schedule",
            })
            events.append({
                "event_date": day2,
                "country": "US",
                "category": "fed",
                "title": f"FOMC Rate Decision ({day2})",
                "importance": "high",
                "source": "fomc_schedule",
            })
        return events

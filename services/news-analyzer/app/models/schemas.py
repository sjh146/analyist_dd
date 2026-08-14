"""
Data schemas for News/SNS Analyzer.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass
class Article:
    """News article or SNS post."""

    source: str
    title: str
    content: Optional[str] = None
    url: Optional[str] = None
    published_at: Optional[datetime] = None


@dataclass
class AnalysisResult:
    """Result of DeepSeek analysis."""

    authenticity_score: float
    authenticity_label: str  # real, fake, uncertain
    sentiment_score: float  # -1.0 to 1.0
    sentiment_label: str  # positive, negative, neutral
    confidence: float  # 0.0 to 1.0
    related_stocks: List[str] = field(default_factory=list)
    related_sectors: List[str] = field(default_factory=list)
    reasoning: Optional[str] = None


# 이벤트 타입 taxonomy 화이트리스트 (20개 고정)
EVENT_TAXONOMY = [
    "실적발표",
    "배당",
    "유상증자·감자",
    "CB·BW",
    "M&A",
    "지분변동",
    "수주",
    "신제품",
    "특허",
    "규제",
    "소송",
    "부도·상폐·거래정지",
    "리콜",
    "자사주",
    "임원변경",
    "파트너십",
    "거시경제",
    "시장지수·유동성",
    "자연재해",
    "기타",
]

# time_range 화이트리스트
TIME_RANGE_TAXONOMY = ["1d", "3d", "1w", "1m", "영구"]


@dataclass
class StructuredNews:
    """구조화 이벤트 추출 결과 (기사 → 정형 JSON 1개).

    파서 검증 모델: 모든 필드는 화이트리스트/범위 클램프를 거친 값만 담는다.
    """

    stock_code: Optional[str] = None
    event_type: str = "기타"
    themes: List[str] = field(default_factory=list)
    sentiment_score: float = 0.0  # -1.0 to 1.0
    importance: float = 0.5  # 0.0 to 1.0
    novelty: float = 0.5  # 0.0 to 1.0
    time_range: str = "1w"
    core_event_text: str = ""


@dataclass
class StockSentiment:
    """Aggregated sentiment for a stock on a given date."""

    stock_code: str
    date: datetime.date
    avg_sentiment: float = 0.0
    sentiment_count: int = 0
    positive_count: int = 0
    negative_count: int = 0
    neutral_count: int = 0
    news_count: int = 0
    sns_count: int = 0

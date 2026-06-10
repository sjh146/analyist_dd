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

"""
DeepSeek LLM Analyzer
Analyzes news articles for authenticity and sentiment using DeepSeek API.
"""

import json
import logging
from typing import Dict
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential
from app.models.schemas import Article, AnalysisResult

logger = logging.getLogger(__name__)


class DeepSeekAnalyzer:
    """Analyzes news articles using DeepSeek's LLM API."""

    def __init__(self, api_key: str):
        if not api_key:
            logger.warning("No DeepSeek API key provided. Analysis will be simulated.")
        self.client = OpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com",
        ) if api_key else None
        self._simulate = not bool(api_key)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    async def analyze_article(self, article: Article) -> AnalysisResult:
        """
        Analyze a single article for authenticity and sentiment.
        
        Args:
            article: Article to analyze
        
        Returns:
            AnalysisResult with scores and labels
        """
        if self._simulate:
            return self._simulate_analysis(article)

        return await self._call_deepseek_api(article)

    async def analyze_batch(
        self, articles: list
    ) -> Dict[str, AnalysisResult]:
        """Analyze multiple articles and return dict keyed by URL."""
        results = {}
        for article in articles:
            try:
                result = await self.analyze_article(article)
                results[article.url] = result
            except Exception as e:
                logger.error(f"Failed to analyze {article.title[:50]}: {e}")
                results[article.url] = AnalysisResult(
                    authenticity_score=0.5,
                    authenticity_label="uncertain",
                    sentiment_score=0.0,
                    sentiment_label="neutral",
                    confidence=0.0,
                    related_stocks=[],
                    related_sectors=[],
                )
        return results

    async def _call_deepseek_api(self, article: Article) -> AnalysisResult:
        """Call DeepSeek API with a structured prompt."""
        prompt = f"""당신은 한국 주식 시장 전문 분석가입니다. 아래 뉴스 기사를 분석해주세요.

제목: {article.title}
내용: {article.content[:2000]}

다음 JSON 형식으로만 응답해주세요:
{{
    "authenticity_score": 0.0~1.0 (기사의 진실성 점수),
    "authenticity_label": "real" 또는 "fake" 또는 "uncertain",
    "sentiment_score": -1.0~1.0 (긍정/부정 점수),
    "sentiment_label": "positive" 또는 "negative" 또는 "neutral",
    "confidence": 0.0~1.0 (분석 신뢰도),
    "related_stocks": ["종목코드1", "종목코드2"],
    "related_sectors": ["섹터명1", "섹터명2"],
    "reasoning": "분석 이유 (한글로 간략히)"
}}"""

        response = self.client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {
                    "role": "system",
                    "content": "당신은 한국 주식 시장 전문 분석가입니다. JSON 형식으로만 응답하세요.",
                },
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=500,
        )

        content = response.choices[0].message.content
        return self._parse_response(content)

    def _parse_response(self, content: str) -> AnalysisResult:
        """Parse DeepSeek API response into AnalysisResult."""
        try:
            data = json.loads(content)
            return AnalysisResult(
                authenticity_score=float(data.get("authenticity_score", 0.5)),
                authenticity_label=data.get(
                    "authenticity_label", "uncertain"
                ),
                sentiment_score=float(data.get("sentiment_score", 0.0)),
                sentiment_label=data.get("sentiment_label", "neutral"),
                confidence=float(data.get("confidence", 0.5)),
                related_stocks=data.get("related_stocks", []),
                related_sectors=data.get("related_sectors", []),
            )
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.error(f"Failed to parse API response: {e}")
            return AnalysisResult(
                authenticity_score=0.5,
                authenticity_label="uncertain",
                sentiment_score=0.0,
                sentiment_label="neutral",
                confidence=0.0,
                related_stocks=[],
                related_sectors=[],
            )

    def _simulate_analysis(self, article: Article) -> AnalysisResult:
        """Simulate analysis when no API key is configured."""
        import random

        sentiment_score = random.uniform(-0.5, 0.5)
        sentiment_label = (
            "positive"
            if sentiment_score > 0.2
            else "negative" if sentiment_score < -0.2
            else "neutral"
        )

        return AnalysisResult(
            authenticity_score=random.uniform(0.6, 1.0),
            authenticity_label="real",
            sentiment_score=sentiment_score,
            sentiment_label=sentiment_label,
            confidence=random.uniform(0.5, 0.9),
            related_stocks=[],
            related_sectors=[],
        )

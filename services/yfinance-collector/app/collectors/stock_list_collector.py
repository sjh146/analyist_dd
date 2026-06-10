"""
Stock List Collector
Maintains the list of KOSPI and KOSDAQ stocks to collect.
"""

from typing import List, Dict


class StockListCollector:
    """Provides stock list for data collection."""

    # Top KOSPI stocks
    KOSPI_STOCKS = [
        {"code": "005930", "name": "삼성전자", "market": "KOSPI", "sector": "반도체"},
        {"code": "000660", "name": "SK하이닉스", "market": "KOSPI", "sector": "반도체"},
        {"code": "207940", "name": "삼성바이오로직스", "market": "KOSPI", "sector": "바이오"},
        {"code": "005935", "name": "삼성전자우", "market": "KOSPI", "sector": "반도체"},
        {"code": "373220", "name": "LG에너지솔루션", "market": "KOSPI", "sector": "2차전지"},
        {"code": "000270", "name": "기아", "market": "KOSPI", "sector": "자동차"},
        {"code": "005380", "name": "현대차", "market": "KOSPI", "sector": "자동차"},
        {"code": "068270", "name": "셀트리온", "market": "KOSPI", "sector": "바이오"},
        {"code": "105560", "name": "KB금융", "market": "KOSPI", "sector": "금융"},
        {"code": "055550", "name": "신한지주", "market": "KOSPI", "sector": "금융"},
        {"code": "003670", "name": "포스코퓨처엠", "market": "KOSPI", "sector": "2차전지"},
        {"code": "035420", "name": "NAVER", "market": "KOSPI", "sector": "IT"},
        {"code": "035720", "name": "카카오", "market": "KOSPI", "sector": "IT"},
        {"code": "051910", "name": "LG화학", "market": "KOSPI", "sector": "화학"},
        {"code": "006400", "name": "삼성SDI", "market": "KOSPI", "sector": "2차전지"},
        {"code": "012330", "name": "현대모비스", "market": "KOSPI", "sector": "자동차"},
        {"code": "028260", "name": "삼성물산", "market": "KOSPI", "sector": "건설"},
        {"code": "086790", "name": "하나금융지주", "market": "KOSPI", "sector": "금융"},
        {"code": "016360", "name": "삼성증권", "market": "KOSPI", "sector": "금융"},
        {"code": "003550", "name": "LG", "market": "KOSPI", "sector": "지주사"},
        {"code": "066570", "name": "LG전자", "market": "KOSPI", "sector": "가전"},
        {"code": "032830", "name": "삼성생명", "market": "KOSPI", "sector": "금융"},
        {"code": "000810", "name": "삼성화재", "market": "KOSPI", "sector": "금융"},
        {"code": "015760", "name": "한국전력", "market": "KOSPI", "sector": "에너지"},
        {"code": "033780", "name": "KT&G", "market": "KOSPI", "sector": "소비재"},
        {"code": "017670", "name": "SK텔레콤", "market": "KOSPI", "sector": "통신"},
        {"code": "030200", "name": "KT", "market": "KOSPI", "sector": "통신"},
        {"code": "034730", "name": "SK", "market": "KOSPI", "sector": "지주사"},
        {"code": "096770", "name": "SK이노베이션", "market": "KOSPI", "sector": "에너지"},
        {"code": "361610", "name": "SK아이이테크놀로지", "market": "KOSPI", "sector": "2차전지"},
    ]

    # Top KOSDAQ stocks
    KOSDAQ_STOCKS = [
        {"code": "196170", "name": "알테오젠", "market": "KOSDAQ", "sector": "바이오"},
        {"code": "247540", "name": "에코프로비엠", "market": "KOSDAQ", "sector": "2차전지"},
        {"code": "086520", "name": "에코프로", "market": "KOSDAQ", "sector": "2차전지"},
        {"code": "091990", "name": "셀트리온헬스케어", "market": "KOSDAQ", "sector": "바이오"},
        {"code": "403870", "name": "HPSP", "market": "KOSDAQ", "sector": "반도체"},
        {"code": "293490", "name": "카카오게임즈", "market": "KOSDAQ", "sector": "게임"},
        {"code": "263750", "name": "펄어비스", "market": "KOSDAQ", "sector": "게임"},
        {"code": "035760", "name": "CJ ENM", "market": "KOSDAQ", "sector": "미디어"},
        {"code": "112040", "name": "위메이드", "market": "KOSDAQ", "sector": "게임"},
        {"code": "348370", "name": "엔켐", "market": "KOSDAQ", "sector": "2차전지"},
        {"code": "214150", "name": "클래시스", "market": "KOSDAQ", "sector": "의료기기"},
        {"code": "277810", "name": "레인보우로보틱스", "market": "KOSDAQ", "sector": "로봇"},
    ]

    def get_all_stocks(self) -> List[Dict]:
        """Get full stock list."""
        return self.KOSPI_STOCKS + self.KOSDAQ_STOCKS

    def get_stocks_by_market(self, market: str) -> List[Dict]:
        """Get stocks by market type."""
        if market == "KOSPI":
            return self.KOSPI_STOCKS
        elif market == "KOSDAQ":
            return self.KOSDAQ_STOCKS
        return []

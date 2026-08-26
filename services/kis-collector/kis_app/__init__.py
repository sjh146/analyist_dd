"""KIS 데이터 수집기 (kis-collector) — 한국투자증권 OpenAPI 기반 일봉/분봉 수집.

KRX 오픈API 차단(2026-08-24~) 대체. 설계 원칙:
- 실제 HTTP는 subprocess curl (urllib/requests는 WAF 403).
- 토큰은 24h 캐시 후 재사용 (발급 1분당 1회 제한 EGW00133).
- 호출 간 딜레이/재시도는 보수적 기본값.
- 테스트는 전부 모킹 — 이 패키지 자체는 실제 KIS API를 호출하지 않는다.
"""

__version__ = "0.1.0"

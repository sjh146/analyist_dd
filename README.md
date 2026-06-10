# 📈 주식 자동매매 시스템

## 개요
DeepSeek API 기반 AI 뉴스/SNS 분석 → 감정분석 → pgvector/Neo4j 저장 → XGBoost ML → Creon API 매매

## 시스템 아키텍처

```ascii
[Linux Server - Docker]
  News/SNS Analyzer (DeepSeek API)
  yfinance Collector (KOSPI/KOSDAQ)
  Stock Vectorizer (pgvector)
  XGBoost ML (Feature Engineering + Prediction)
  Strategy Agents (테마/사이클/쌍둥이 매매)
  PostgreSQL + pgvector
  Neo4j Graph DB
  Redis (Message Queue)
       │
       │ Proxmox Bridge Network ────────────────────────┐
       ▼                                                │
[Windows VM]                                            │
  Trade Executor (Python 32-bit)                        │
  Creon API (매매체결)                                   │
  Redis Client (Bridge 통신) ◄───────────────────────────┘
```

## 실행 방법

### 1. Linux Docker 서비스 실행
```bash
# 환경 변수 설정
cp .env.example .env
# .env 파일에 API 키, 비밀번호 설정

# Docker 컨테이너 실행
docker-compose up -d

# 상태 확인
docker-compose ps
```

### 2. Windows VM 설정
```bash
# Windows VM에서 실행
python -m pip install -r services/trade-executor/requirements.txt
python services/trade-executor/main.py
```

## 필수 환경변수
| 변수 | 설명 | 예시 |
|------|------|------|
| DEEPSEEK_API_KEY | DeepSeek API 키 | sk-... |
| POSTGRES_PASSWORD | PostgreSQL 비밀번호 | ... |
| NEO4J_PASSWORD | Neo4j 비밀번호 | ... |
| REDIS_PASSWORD | Redis 비밀번호 | ... |
| BRIDGE_VM_IP | Windows VM IP | 192.168.1.101 |

## 서비스 구성

| 서비스 | 포트 | 설명 |
|--------|------|------|
| PostgreSQL | 5432 | 시장 데이터, 감정분석 |
| Neo4j | 7474, 7687 | 그래프 관계 |
| Redis | 6379 | 메시지 큐 |
| API Gateway | 8000 | REST API |

## 프로젝트 구조
```
/ ─── docker-compose.yml
 ├── .env
 ├── init-scripts/       # DB 초기화 스크립트
 ├── services/
 │   ├── news-analyzer/    # 뉴스/SNS 분석
 │   ├── yfinance-collector/ # 시장 데이터 수집
 │   ├── stock-vectorizer/ # 종목 벡터화
 │   ├── xgboost-ml/      # ML 예측
 │   ├── strategy-agents/  # 매매 전략
 │   ├── trade-executor/   # Creon API 매매
 │   └── api-gateway/     # REST API
 ├── config/             # 전략/뉴스 설정
 └── models/             # 저장된 ML 모델
```

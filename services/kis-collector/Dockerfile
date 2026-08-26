FROM python:3.11-slim

WORKDIR /app

# urllib/python-requests는 KIS WAF에서 403 — subprocess curl로 호출 (브라우저급 TLS)
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --default-timeout=120 -r requirements.txt

COPY . .

# 장 마감 후 1회 실행 (cron 등록은 Hermes 담당 — docker-compose command는 예시)
CMD ["python", "-m", "app.main", "--job", "all"]

#!/usr/bin/env python3
"""테스트 시그널 발행 — mock 실행 루프 E2E 검증용.

전략 시그널 발행과 동일한 형식(HMAC 서명 포함)으로 trading:signals 스트림에 XADD.
사용법: python3 scripts/test_mock_signal.py [buy|sell]
"""
import hashlib
import hmac
import json
import os
import sys
from datetime import datetime

# .env 로드
with open(os.path.expanduser("~/analyist_dd/.env"), encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v)

import redis

action = sys.argv[1] if len(sys.argv) > 1 else "buy"

signal = {
    "action": action,
    "stock_code": "005930",
    "quantity": 10,
    "price": 70000,
    "order_type": "limit",
    "strategy_name": "mock_test",
    "signal_id": f"sig_{datetime.now().strftime('%Y%m%d%H%M%S')}",
    "timestamp": datetime.now().isoformat(),
    "batch_type": "signal",
}

secret = os.environ.get("TRADE_SIGNAL_SECRET", "")
if secret:
    canonical = "&".join(f"{k}={v}" for k, v in sorted(signal.items()))
    signal["sig"] = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()

r = redis.Redis(host="127.0.0.1", port=int(os.environ.get("REDIS_PORT", "6379")),
                password=os.environ.get("REDIS_PASSWORD", ""), decode_responses=True)
payload = {"data": json.dumps(signal, ensure_ascii=False)}
msg_id = r.xadd("trading:signals", payload)
print(f"발행 OK: {msg_id} | {action} 005930 x10 @70000")
print(f"시그널: {json.dumps(signal, ensure_ascii=False)}")

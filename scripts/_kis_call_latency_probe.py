#!/usr/bin/env python3
"""KIS 일봉 1콜 지연 실측 — 백필 ETA 산정용(읽기 전용)."""
import os
import sys
import time

sys.path.insert(0, "/home/jhshi/analyist_dd/services/kis-collector")
sys.path.insert(0, "/home/jhshi/analyist_dd")

from kis_app.client.kis_client import KisClient  # noqa: E402

cfg = {}
for line in open("/home/jhshi/analyist_dd/.env", encoding="utf-8", errors="ignore"):
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.strip().split("=", 1)
        cfg[k.strip()] = v.strip()

t0 = time.time()
client = KisClient(
    cfg.get("KIS_APP_KEY", ""), cfg.get("KIS_APP_SECRET", ""),
    cfg.get("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443"),
    daily_tr_id=cfg.get("KIS_DAILY_TR_ID", "FHKST03010100"),
    delay=0.0, jitter=0.0,
)
print("init %.2fs" % (time.time() - t0), flush=True)

for code in ("005930", "000660", "035420"):
    t = time.time()
    try:
        r = client.get_daily_chart(code, "J", "20261009", "20261109")
        n = len((r or {}).get("output2") or [])
        print("%s %.2fs rows=%d" % (code, time.time() - t, n), flush=True)
    except Exception as e:  # noqa: BLE001
        print("%s %.2fs FAIL %s" % (code, time.time() - t, str(e)[:120]), flush=True)

#!/usr/bin/env python3
"""개장 직후 코스닥 등락률 게이트 — 조건 미달이면 단타 후보 생성을 막는다.

용도: 급락 다음날 갭업이 밀리는 경우를 피하려고, 09:0x 시점 코스닥이
전일 종가 대비 기준치(기본 +0.5%) 이상일 때만 단타를 허용한다.
KIS 국내업종 현재지수(FHPUP02100000)의 전일대비율을 쓴다.

종료코드: 0=허용, 1=차단(조건 미달), 2=판정 불가(호출 실패 → 차단으로 취급)
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

BASE = "https://openapi.koreainvestment.com:9443"
PATH = "/uapi/domestic-stock/v1/quotations/inquire-index-price"
TR_ID = "FHPUP02100000"
KOSDAQ_CODE = "1001"


def load_env(path: str) -> dict:
    env = {}
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return env
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        env[key.strip()] = val.strip().strip('"').strip("'")
    return env


def cached_token(env: dict) -> str:
    if env.get("KIS_ACCESS_TOKEN"):
        return env["KIS_ACCESS_TOKEN"]
    path = env.get("KIS_TOKEN_PATH") or "data/kis/token_cache.json"
    try:
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(data.get("access_token") or "")


def decide(change_pct: float, threshold: float) -> bool:
    return change_pct is not None and float(change_pct) >= float(threshold)


def fetch_change(env: dict, timeout: float = 15.0) -> tuple[float | None, float | None]:
    token = cached_token(env)
    if not token:
        return None, None
    url = (BASE + PATH + "?FID_COND_MRKT_DIV_CODE=U&FID_INPUT_ISCD=" + KOSDAQ_CODE)
    req = urllib.request.Request(url, headers={
        "authorization": "Bearer " + token,
        "appkey": env.get("KIS_APP_KEY", ""),
        "appsecret": env.get("KIS_APP_SECRET", ""),
        "tr_id": TR_ID,
        "custype": "P",
    })
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    output = payload.get("output") or {}
    level = output.get("bstp_nmix_prpr")
    change = output.get("bstp_nmix_prdy_ctrt")
    try:
        return float(change), float(level)
    except (TypeError, ValueError):
        return None, None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="코스닥 개장 게이트")
    ap.add_argument("--env-file", default="/home/jhshi/analyist_dd/.env")
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--json-out", default="")
    args = ap.parse_args(argv)

    env = load_env(args.env_file) if os.path.exists(args.env_file) else load_env(".env")
    env.update({k: v for k, v in os.environ.items() if k.startswith("KIS_")})
    try:
        change, level = fetch_change(env)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(json.dumps({"allow": False, "reason": "fetch_failed", "error": str(exc)[:80]},
                         ensure_ascii=False))
        return 2
    if change is None:
        print(json.dumps({"allow": False, "reason": "no_change_value"}, ensure_ascii=False))
        return 2
    allow = decide(change, args.threshold)
    result = {"allow": allow, "kosdaq_change_pct": round(change, 2),
              "kosdaq_level": level, "threshold": args.threshold}
    if args.json_out:
        pathlib.Path(args.json_out).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if allow else 1


if __name__ == "__main__":
    raise SystemExit(main())

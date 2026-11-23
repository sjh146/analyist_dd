#!/usr/bin/env python3
"""고객 등록: 토큰 발급(1회 표시) + customers.json 갱신 + 고객 PC 용 config 출력.

사용: python3 services/feed_relay/add_customer.py --id pilot1 --channel stable \
        --base-url https://xxx.trycloudflare.com --app-dir "C:\\TraderAgent\\app"
토큰은 저장되지 않는다(sha256 만 보관). 출력된 값을 고객 PC config.json 에 넣는다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets


def hashlib_hmac(master: str, customer_id: str) -> str:
    return __import__("hmac").new(master.encode("utf-8"),
                                  ("release:" + customer_id).encode("utf-8"),
                                  __import__("hashlib").sha256).hexdigest()


def load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"customers": []}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="고객 등록")
    ap.add_argument("--id", required=True)
    ap.add_argument("--channel", default="stable", choices=("stable", "beta"))
    ap.add_argument("--max-daily-trades", type=int, default=10)
    ap.add_argument("--max-position-pct", type=float, default=30.0)
    ap.add_argument("--root", default=os.path.join("data", "feed_relay"))
    ap.add_argument("--base-url", default="")
    ap.add_argument("--app-dir", default=r"C:\TraderAgent\app")
    args = ap.parse_args(argv)

    path = os.path.join(args.root, "customers.json")
    data = load(path)
    rows = [r for r in data.get("customers") or [] if str(r.get("id")) != args.id]
    token = secrets.token_urlsafe(24)
    rows.append({
        "id": args.id,
        "token_sha256": hashlib.sha256(token.encode("utf-8")).hexdigest(),
        "channel": args.channel,
        "limits": {"max_daily_trades": args.max_daily_trades,
                   "max_position_pct": args.max_position_pct},
        "created_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone(__import__("datetime").timedelta(hours=9))
        ).isoformat(timespec="seconds"),
    })
    data["customers"] = sorted(rows, key=lambda r: str(r.get("id")))
    os.makedirs(args.root, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)

    release_key = ""
    key_path = os.path.join(args.root, "release_key.txt")
    try:
        with open(key_path, encoding="utf-8") as fh:
            master = fh.read().strip()
    except OSError:
        master = ""
    if master:
        release_key = hashlib_hmac(master, args.id)

    cfg = {
        "base_url": args.base_url or "https://CHANGE-ME",
        "customer_id": args.id,
        "token": token,
        "channel": args.channel,
        "release_key": release_key,
        "feed_schema_version": 1,
        "app_dir": args.app_dir,
        "feed_out": os.path.join(os.path.dirname(args.app_dir), "data", "feed", "screener_latest.json"),
        "log_dir": os.path.join(os.path.dirname(args.app_dir), "logs"),
    }
    print(json.dumps({"customer": args.id, "token": token, "config": cfg,
                      "note": "토큰은 지금만 표시된다"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

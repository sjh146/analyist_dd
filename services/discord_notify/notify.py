#!/usr/bin/env python3
"""고객 Discord 알림 — 장전 체크리스트·피드 상태·수신 확인·마감 요약.

전송 방식 두 가지:
  webhook : 채널 웹훅 URL 하나로 전송(가장 간단, 봇 불필요)
  bot     : 봇 토큰 + 채널 ID (스레드·DM 확장 가능)

사용:
  python3 services/discord_notify/notify.py --config config.json morning
  python3 services/discord_notify/notify.py --config config.json ack
  python3 services/discord_notify/notify.py --config config.json close
  python3 services/discord_notify/notify.py --config config.json test --text "연결 확인"

실패해도 예외를 밖으로 던지지 않고 종료코드로 알린다(크론에서 조용히 죽지 않게 로그).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

API = "https://discord.com/api/v10"
MAX_LEN = 1900


def now_kst() -> datetime:
    return datetime.now(timezone(timedelta(hours=9)))


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8-sig") as fh:
        return json.load(fh)


def post(cfg: dict, content: str, timeout: float = 15.0) -> dict:
    text = content if len(content) <= MAX_LEN else content[:MAX_LEN - 3] + "..."
    payload = json.dumps({"content": text, "allowed_mentions": {"parse": []}}).encode("utf-8")
    mode = str(cfg.get("mode") or "webhook")
    if mode == "webhook":
        url = cfg.get("webhook_url") or ""
        headers = {"Content-Type": "application/json"}
    else:
        token = cfg.get("bot_token") or ""
        channel = cfg.get("channel_id") or ""
        url = "{0}/channels/{1}/messages".format(API, channel)
        headers = {"Content-Type": "application/json", "Authorization": "Bot " + token}
    if not (url.startswith("https://") or "127.0.0.1" in url or "localhost" in url):
        raise ValueError("전송 URL 미설정(mode={0})".format(mode))
    req = urllib.request.Request(url, data=payload, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return {"ok": True, "status": resp.status}


def parse_line(line: str):
    try:
        return json.loads(line)
    except ValueError:
        return None


def load_json(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def feed_summary(feed_repo: str) -> dict:
    meta = load_json(os.path.join(feed_repo, "deploy", "meta.json")) or {}
    latest = load_json(os.path.join(feed_repo, "deploy", "latest.json")) or {}
    regime = latest.get("regime") or {}
    published = str(meta.get("published_at") or "")
    today = now_kst().strftime("%Y-%m-%d")
    return {
        "published_at": published,
        "generated_at": meta.get("generated_at") or latest.get("generated_at"),
        "candidates": meta.get("candidates") or {},
        "regime": regime.get("state"),
        "kosdaq": regime.get("kosdaq_level"),
        "kospi": regime.get("kospi_level"),
        "market_up": regime.get("market_up"),
        "is_today": published[:10] == today,
    }


def ack_state(relay_root: str, customers: list[str]) -> dict:
    day = now_kst().strftime("%Y%m%d")
    path = os.path.join(relay_root, "ack", day + ".jsonl")
    done = set()
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                row = parse_line(line)
                if row and row.get("customer_id"):
                    done.add(str(row["customer_id"]))
    except OSError:
        done = set()
    tele = set()
    for p in glob.glob(os.path.join(relay_root, "telemetry", "*", day + ".json")):
        tele.add(os.path.basename(os.path.dirname(p)))
    return {"acked": sorted(done), "missing": [c for c in customers if c not in done],
            "telemetry": sorted(tele)}


def morning_message(feed: dict, cfg: dict) -> str:
    if not feed["is_today"]:
        return ("**[08:30] 피드 미발행 경고**\n"
                "오늘 추론 피드가 아직 게시되지 않았습니다. PC 를 켜지 마시고 공지를 기다려 주세요.\n"
                "발행되면 이 채널에 다시 알립니다. (최근 발행: {0})").format(feed["published_at"] or "없음")
    counts = feed["candidates"]
    total = sum(counts.values()) if counts else 0
    per = " · ".join("{0} {1}".format(k, v) for k, v in counts.items())
    regime = feed["regime"] or "미상"
    kosdaq = ("KOSDAQ {0}".format(feed["kosdaq"])) if feed["kosdaq"] else "지수 미상"
    gen = str(feed["generated_at"] or "")
    gen_hm = gen[11:16] if len(gen) >= 16 else gen
    lines = [
        "**[08:30] 장전 체크 — 오늘 준비하세요**",
        "",
        "피드: {0} 발행 · 후보 {1}건 ({2})".format(gen_hm, total, per),
        "국면: {0} · {1}".format(regime, kosdaq),
        "",
    ]
    if str(regime) == "risk_off":
        lines += ["⚠️ 오늘은 하락 국면(risk_off): 진입 축소·보수 운용으로 설정되어 있습니다.", ""]
    lines += [
        "**지금 하실 일 (3단계)**",
        "1. PC 전원을 켜 주세요 (대기 상태 유지)",
        "2. 크레온 PLUS 에 로그인해 주세요 (자동 로그인 불가)",
        "3. 08:45 자동 동기화 후 이 채널의 ✅ 상태를 확인해 주세요",
        "",
        "3단계가 끝나야 오늘 매매가 시작됩니다. 완료 후 이 메시지에 ✅ 반응을 남겨 주세요.",
        "문제가 있으면 #질문 채널에 PC 이름과 함께 적어 주세요.",
    ]
    return "\n".join(lines)


def ack_message(state: dict) -> str:
    acked = state["acked"]
    missing = state["missing"]
    lines = ["**[08:55] 수신 확인**", "",
             "완료: {0}".format(", ".join(acked) if acked else "없음")]
    if missing:
        lines += ["미완료: {0}".format(", ".join(missing)),
                  "",
                  "미완료 고객은 오늘 매매가 시작되지 않습니다. PC 전원·크레온 로그인·네트워크를 확인해 주세요."]
    else:
        lines += ["전원 정상. 09:00 부터 매매가 진행됩니다."]
    return "\n".join(lines)


def close_message(feed: dict, state: dict) -> str:
    return "\n".join([
        "**[15:40] 마감 요약**",
        "",
        "오늘 후보: {0}건".format(sum((feed.get("candidates") or {}).values())),
        "수신 완료: {0}".format(", ".join(state["acked"]) if state["acked"] else "없음"),
        "텔레메트리 수신: {0}".format(", ".join(state["telemetry"]) if state["telemetry"] else "없음"),
        "",
        "체결·손익은 각 PC 리포트에서 확인하실 수 있습니다.",
        "내일 08:30 에 다시 안내드립니다.",
    ])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="고객 Discord 알림")
    ap.add_argument("--config", default=os.path.join(os.path.dirname(__file__), "config.json"))
    ap.add_argument("command", choices=("morning", "ack", "close", "test"))
    ap.add_argument("--text", default="")
    args = ap.parse_args(argv)
    try:
        cfg = load_config(args.config)
    except (OSError, ValueError) as exc:
        print("config 없음/오류: {0}".format(exc), file=sys.stderr)
        return 2
    feed = feed_summary(cfg.get("feed_repo") or "")
    state = ack_state(cfg.get("relay_root") or "", list(cfg.get("customers") or []))
    if args.command == "morning":
        body = morning_message(feed, cfg)
    elif args.command == "ack":
        body = ack_message(state)
    elif args.command == "close":
        body = close_message(feed, state)
    else:
        body = args.text or "연결 확인: analyze_dd 알림 정상"
    try:
        out = post(cfg, body)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print("전송 실패: {0}".format(exc), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "command": args.command, "chars": len(body), **out}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

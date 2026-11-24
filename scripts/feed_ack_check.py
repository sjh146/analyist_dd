#!/usr/bin/env python3
"""고객 피드 수신 확인(ack) 검사 — 릴레이 ack 파일 대 고객 목록 판정 (읽기 전용).

판정: 릴레이가 게시한 피드(generated_at)에 대해 고객별로 같은 feed_generated_at 의 ack 행이
있는지. ack 행은 relay 서버(POST /v1/ack)가 data/feed_relay/ack/<YYYYMMDD>.jsonl 에 남긴다.
피드가 없는 경우 판정 불가(rc=2)다.

config/feed_customers.json 형식은 relay 의 customers.json 과 같다:
{"customers": [{"id": "pilot1"}, ...]} (없으면 고객 0명으로 처리 — 전원 확인 rc=0).

알림: 미확인/판정 불가일 때 services/discord_notify/notify.py 를 subprocess 로 호출한다
(test 명령 + --text). notify config 파일이 없으면 조용히 건너뛴다. 알림 실패는 검사 rc 를
바꾸지 않고 stderr 경고만 남긴다.

종료코드: 0 = 전원 확인 / 1 = 미확인 고객 있음 / 2 = 판정 불가(피드 없음).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, time, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACK_DIR = os.path.join(REPO, "data", "feed_relay", "ack")
FEED_PATH = os.path.join(REPO, "data", "feed_relay", "feed", "latest.json")
CUSTOMERS_CONFIG = os.path.join(REPO, "config", "feed_customers.json")
REPORT = os.path.join(REPO, "reports", "feed_ack_latest.json")
NOTIFY = os.path.join(REPO, "services", "discord_notify", "notify.py")
NOTIFY_CONFIG = os.path.join(REPO, "services", "discord_notify", "config.json")
ACK_DUE_HOUR, ACK_DUE_MIN = 8, 55

KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    return datetime.now(KST)


def parse_ts(text) -> datetime | None:
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(str(text))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt


def read_ack_rows(ack_dir: str) -> list[dict]:
    rows = []
    for path in sorted(glob.glob(os.path.join(ack_dir, "*.jsonl"))):
        try:
            with open(path, encoding="utf-8") as fh:
                lines = fh.readlines()
        except OSError as exc:
            print("ack 파일 읽기 실패({0}): {1}".format(path, exc), file=sys.stderr)
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError as exc:
                print("ack 행 파싱 실패({0}): {1}".format(path, exc), file=sys.stderr)
                continue
            if row.get("customer_id"):
                rows.append(row)
    return rows


def load_feed(feed_path: str) -> dict | None:
    try:
        with open(feed_path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def load_customer_ids(path: str) -> list[str]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    return [str(row["id"]) for row in (data.get("customers") or []) if row.get("id") is not None]


def feed_expected_at(now: datetime | None = None) -> str:
    now = now or now_kst()
    due = datetime.combine(now.date(), time(ACK_DUE_HOUR, ACK_DUE_MIN), tzinfo=KST)
    return due.isoformat(timespec="seconds")


def judge(rows: list[dict], customer_ids: list[str], expected_generated_at: str,
          now: datetime | None = None) -> dict:
    by_customer: dict[str, dict] = {}
    for row in rows:
        cid = str(row.get("customer_id"))
        ts = parse_ts(row.get("received_at") or row.get("acked_at"))
        entry = by_customer.setdefault(cid, {"acked": False, "last": None})
        if str(row.get("feed_generated_at") or "") == expected_generated_at:
            entry["acked"] = True
        if ts is not None and (entry["last"] is None or ts > entry["last"]):
            entry["last"] = ts
    now = now or now_kst()
    customers = []
    missing = []
    for cid in customer_ids:
        entry = by_customer.get(cid, {"acked": False, "last": None})
        last = entry["last"]
        customers.append({
            "id": cid,
            "acked": bool(entry["acked"]),
            "last_ack_at": last.isoformat(timespec="seconds") if last else None,
            "age_min": round(max(0.0, (now - last).total_seconds() / 60.0), 1) if last else None,
        })
        if not entry["acked"]:
            missing.append(cid)
    return {"customers": customers, "missing": missing}


def write_report(report_path: str, payload: dict) -> str:
    os.makedirs(os.path.dirname(report_path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(report_path) or ".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, report_path)
    return report_path


def notify_message(result: dict, rc: int) -> str:
    if rc == 2:
        return ("**고객 수신 확인 — 판정 불가**\n"
                "릴레이에 게시된 피드가 없어 오늘 ack 를 판정할 수 없습니다. 피드 게시를 확인해 주세요.")
    lines = ["**고객 수신 확인 — 미완료**", "",
             "피드: {0}".format(result["generated_at"] or "미상")]
    if result["missing"]:
        lines += ["미완료: {0}".format(", ".join(result["missing"])), "",
                  "미완료 고객은 오늘 매매가 시작되지 않습니다. PC 전원·크레온 로그인·네트워크를 확인해 주세요."]
    return "\n".join(lines)


def send_notify(result: dict, rc: int, notify_config: str) -> None:
    if not os.path.isfile(notify_config):
        return
    cmd = [sys.executable, NOTIFY, "--config", notify_config, "test", "--text", notify_message(result, rc)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print("알림 호출 실패: {0}".format(exc), file=sys.stderr)
        return
    if proc.returncode != 0:
        print("알림 실패(rc={0}): {1}".format(proc.returncode, proc.stderr.strip()[-200:]), file=sys.stderr)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="고객 피드 ack 검사")
    ap.add_argument("--ack-dir", default=ACK_DIR)
    ap.add_argument("--feed-path", default=FEED_PATH)
    ap.add_argument("--customers-config", default=CUSTOMERS_CONFIG)
    ap.add_argument("--report", default=REPORT)
    ap.add_argument("--notify-config", default=NOTIFY_CONFIG)
    args = ap.parse_args(argv)
    now = now_kst()
    feed = load_feed(args.feed_path)
    expected = str((feed or {}).get("generated_at") or "")
    result = {"generated_at": expected or None, "feed_expected_at": feed_expected_at(now),
              "customers": [], "missing": []}
    if not expected:
        write_report(args.report, result)
        send_notify(result, 2, args.notify_config)
        print(json.dumps(result, ensure_ascii=False))
        print("판정 불가 — 게시된 피드 없음", file=sys.stderr)
        return 2
    rows = read_ack_rows(args.ack_dir)
    customer_ids = load_customer_ids(args.customers_config)
    judged = judge(rows, customer_ids, expected, now=now)
    result["customers"] = judged["customers"]
    result["missing"] = judged["missing"]
    write_report(args.report, result)
    rc = 0 if not judged["missing"] else 1
    if rc != 0:
        send_notify(result, rc, args.notify_config)
    print(json.dumps(result, ensure_ascii=False))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

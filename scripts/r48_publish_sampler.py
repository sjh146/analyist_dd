#!/usr/bin/env python3
"""R48 후속 — KRX OpenAPI 가 직전 거래일 봉을 몇 시부터 주는지 밤새 표본으로 실측한다.

왜 필요한가 (2026-11-23 23:0x 실측): 18:55 로더는 end=today-1 로 자르므로 아침 08:40 피드가
항상 T-2 종가로 스크리닝한다. 23:07 현재 API 에 당일(11-23)을 물으면 0행, 직전거래일(11-20)은
935/1,836행 → 당일봉 미발행은 소스 한계. 남은 질문은 T-1 봉의 발행 시각이고, 그것은 한 시점의
probe 로는 답할 수 없다(자정을 넘겨 '오늘'이 바뀌는 순간 target 이 T-1 로 이동한다).
그래서 이 스크립트는 일정 간격으로 같은 질의를 반복하며 가용이 처음 참이 된 시각을 남긴다.

정책: 호출은 krx_daily.fetch_day 를 그대로 재사용한다 → net_guard(프로세스 간 간격·쿨다운·예산)가
자동 적용되고, HTTP 4xx/5xx·respCode 오류면 즉시 중단한다. 전송 타임아웃·이름해석 실패는 '일시'
오류로 분류해 백오프 후 재시도한다(2026-11-23 23:14 실측: 호스트 load 8.3 구간의 read timeout
한 번으로 9시간 표본이 종료됐다). DB 쓰기 없음.

사용:
  python3 scripts/r48_publish_sampler.py --interval-min 15 --until 2026-11-24T08:40
산출: --out JSONL (한 줄 = 한 probe)
판정: scripts/r48_publish_window_report.py --jsonl 위 파일
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.environ.setdefault("PROJ_DIR", os.path.dirname(HERE))
os.environ.setdefault("PROJ_DIR", PROJ)

TRANSIENT_MARKERS = ("전송 오류", "수집 정책 중단", "이름해석")
OUT_DEFAULT = os.path.join(PROJ, "data", "reports", "r48_publish_probe.jsonl")
MIN_ROWS = int(os.environ.get("R48_MIN_ROWS", "500"))


def load_dotenv(path):
    """KRX_API_KEY 등이 셸에 없으면 .env 에서 최소한만 읽는다."""
    if os.environ.get("KRX_API_KEY"):
        return
    try:
        text = open(path, encoding="utf-8").read()
    except OSError as e:
        print(f"R48 sampler: .env 읽기 실패({e}) — 환경변수만 사용", file=sys.stderr)
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("=") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if k == "KRX_API_KEY" and k not in os.environ:
            os.environ[k] = v.strip().strip('"').strip("'")


load_dotenv(os.path.join(PROJ, ".env"))
sys.path.insert(0, HERE)
import krx_daily as k  # noqa: E402


def last_session(today, holidays):
    d = today - timedelta(days=1)
    for _ in range(14):
        if d.weekday() < 5 and d.isoformat() not in holidays:
            return d
        d -= timedelta(days=1)
    return d


def classify_error(msg):
    """전송 타임아웃·DNS·net_guard 쿨다운은 'transient', 그 밖(HTTP 4xx/5xx·respCode·비JSON)은 차단."""
    for m in TRANSIENT_MARKERS:
        if m in msg:
            return "transient"
    return "blocked"


def probe(base, key, holidays):
    today = date.today()
    target = last_session(today, holidays)
    rec = {"probe_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "today": today.isoformat(), "target_session": target.isoformat(),
           "min_rows": MIN_ROWS, "markets": {}, "ok": 0}
    for mk, path in k.MARKETS.items():
        try:
            rows = k.fetch_day(base, key, path, target.strftime("%Y%m%d")) or []
            n = len(rows)
        except Exception as e:  # noqa: BLE001
            msg = f"{mk}: {type(e).__name__}: {e}"[:300]
            rec["error"] = msg
            rec[classify_error(msg)] = True
            return rec
        rec["markets"][mk] = n
        if n >= MIN_ROWS:
            rec["ok"] += 1
    rec["published"] = rec["ok"] >= 2
    return rec


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval-min", type=float, default=15.0)
    ap.add_argument("--until", default=None, help="YYYY-MM-DDTHH:MM (로컬). 기본 다음날 08:40")
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--max-probes", type=int, default=60)
    a = ap.parse_args(argv)

    key = (os.environ.get("KRX_API_KEY") or "").strip()
    if not key:
        print("R48 sampler: KRX_API_KEY 미설정 — 중단", flush=True)
        return 1
    now = datetime.now()
    until = (datetime.fromisoformat(a.until) if a.until
             else (now + timedelta(days=1)).replace(hour=8, minute=40, second=0, microsecond=0))
    if until <= now:
        until = now + timedelta(hours=1)
    holidays = set(k.load_json(k.HOLIDAY_PATH, []))
    base = k.resolve_base(key)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    print(f"R48 sampler 시작 base={base} 간격={a.interval_min}m 종료={until:%F %H:%M} out={a.out}", flush=True)

    published_at = None
    probes = 0
    transient_streak = 0
    while probes < a.max_probes:
        rec = probe(base, key, holidays)
        rec["i"] = probes
        with open(a.out, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        print(json.dumps(rec, ensure_ascii=False), flush=True)
        probes += 1
        if rec.get("blocked"):
            print("차단 신호(HTTP/respCode) — 즉시 중단(IP 보호)", flush=True)
            return 2
        if rec.get("transient"):
            transient_streak += 1
            if transient_streak >= 5:
                print(f"일시 오류 {transient_streak}회 연속 — 중단(IP 보호)", flush=True)
                return 3
            wait = a.interval_min * (2 ** min(transient_streak, 2))
            print(f"일시 오류 — {wait:.0f}분 대기 후 재시도({transient_streak}/5)", flush=True)
            if datetime.now() + timedelta(minutes=wait) >= until:
                break
            time.sleep(wait * 60)
            continue
        transient_streak = 0
        if rec.get("published"):
            published_at = published_at or rec["probe_at"]
        if datetime.now() + timedelta(minutes=a.interval_min) >= until:
            break
        time.sleep(a.interval_min * 60)
    print(f"R48 sampler 종료 probes={probes} 최초가용={published_at}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

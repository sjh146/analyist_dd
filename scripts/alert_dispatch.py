#!/usr/bin/env python3
"""alert_dispatch — Prometheus 알림 규칙을 **사람에게 전달**한다.

WHY (실측 2026-09-25, 리서처 R15)
  `config/prometheus/alert.dq.rules.yml` 에 규칙 20개(DQ 위반·러너 파서 실패·as-of 위반·
  뉴스 저장 정지 등)가 있지만 **Alertmanager 가 없다** — 즉 규칙이 평가만 되고 아무 데도
  전달되지 않았다. DQ 위반이 시스템 안에서만 조용히 켜지고 사람은 몰랐다.
  새 인프라(Alertmanager)를 세우는 대신, 위생 점검과 같은 방식으로 **30분마다 알림 API 를
  폴링해 firing 상태만 전달**한다(이미 평가 중인 규칙을 그대로 활용 — 추가 비용 0).

동작
  - GET /api/v1/alerts 에서 state=firing 만 추린다.
  - **변화가 있을 때만** 출력한다(같은 알림 반복은 침묵, 6시간마다 재알림) → 크론 소음 방지.
  - 출력이 없으면 아무것도 보내지 않는다(watchdog 패턴). 종료코드는 항상 0(잡 실패로 보이지 않게).
  - 상태 파일: data/reports/alert_dispatch_state.json

사용
  /usr/bin/python3 scripts/alert_dispatch.py            # 변화 시에만 출력
  /usr/bin/python3 scripts/alert_dispatch.py --json     # 기계 판독(항상 출력)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(PROJ, "data/reports/alert_dispatch_state.json")
API = os.environ.get("PROM_URL", "http://127.0.0.1:9090") + "/api/v1/alerts"
KST = timezone(timedelta(hours=9))
REENOTIFY_H = 6


def fetch_firing(timeout=10):
    try:
        with urllib.request.urlopen(API, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        return None, f"Prometheus 조회 실패: {type(exc).__name__}"
    out = []
    for a in (data.get("data") or {}).get("alerts") or []:
        if a.get("state") != "firing":
            continue
        labels = a.get("labels") or {}
        out.append({
            "name": labels.get("alertname", "?"),
            "severity": labels.get("severity", "?"),
            "summary": (a.get("annotations") or {}).get("summary", ""),
            "active_at": a.get("activeAt", ""),
            "value": str(a.get("value", "")),
        })
    out.sort(key=lambda x: (x["severity"], x["name"]))
    return out, None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    now = datetime.now(KST)
    firing, err = fetch_firing()
    if a.json:
        print(json.dumps({"ts": now.isoformat(timespec="seconds"), "error": err,
                          "firing": firing or []}, ensure_ascii=False, indent=2))
        return 0

    state = {}
    try:
        with open(STATE, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError):
        pass

    if err:      # Prometheus 자체가 죽었으면 그것도 알려야 한다(단, 반복은 억제)
        sig = hashlib.sha1(err.encode()).hexdigest()[:12]
        last = state.get("last_sig")
        last_ts = state.get("last_ts", "")
        silent = last == sig
        if silent and last_ts:
            try:
                if (now - datetime.fromisoformat(last_ts)) < timedelta(hours=REENOTIFY_H):
                    return 0
            except ValueError:
                pass
        print(f"[알림] {now.strftime('%m-%d %H:%M')} Prometheus 접근 실패 — 규칙 평가 상태를 알 수 없음")
        print(f"  {err}")
        _save(state, sig, now)
        return 0

    sig = hashlib.sha1(json.dumps(firing, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
    if state.get("last_sig") == sig:
        try:
            if (now - datetime.fromisoformat(state.get("last_ts", ""))) < timedelta(hours=REENOTIFY_H):
                return 0     # 같은 상태 반복 → 침묵
        except ValueError:
            pass

    if not firing:
        if state.get("last_sig") not in (None, sig):
            print(f"[알림] {now.strftime('%m-%d %H:%M')} 발화 중인 알림 없음 (해소됨)")
        _save(state, sig, now)
        return 0
    if a.quiet:
        _save(state, sig, now)
        return 0

    print(f"[알림] {now.strftime('%m-%d %H:%M')} 발화 {len(firing)}건")
    for x in firing:
        mark = "🔴" if x["severity"] == "critical" else "🟡"
        print(f"  {mark} {x['name']} — {x['summary'][:110]}")
    print("  조회: python3 scripts/alert_dispatch.py --json")
    _save(state, sig, now)
    return 0


def _save(state, sig, now):
    state.update({"last_sig": sig, "last_ts": now.isoformat(timespec="seconds")})
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    raise SystemExit(main())

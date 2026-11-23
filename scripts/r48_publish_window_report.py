#!/usr/bin/env python3
"""R48 판정 — 밤새 수집한 probe JSONL 에서 T-1 봉 최초 가용 시각을 뽑아 07:30 로더 가능성을 가른다.

자정을 넘기면 '직전 거래일' 이 바뀌므로 관심 세션은 마지막 target_session 이다. 자정 이전 probe 는
target 이 하루 전이라 이미 가용이므로 판정에서 제외한다(안 그러면 '23:12 에 가용' 을 '07:30 이전
발행' 으로 오독한다. 2026-11-23 실측에서 실제로 이 오독이 났다).

성공(사전 등록): 관심 세션의 최초 가용 시각 ≤ 07:30 이고 두 마켓 ≥500행 → 07:30 로더 가능.
07:30 이후 발행 또는 07:30 까지 미발행 → 현행 18:55 로더 구조가 최선으로 축 종결.
출력: 요약 JSON 한 개 + 판정 문장. exit 0 = 가용, 1 = 불가, 3 = 표본 부족/판정 불가.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, time

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)


def load_records(path):
    recs = []
    bad = 0
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            recs.append(json.loads(line))
        except ValueError:
            bad += 1
    if bad:
        print(f"R48 report: 파싱 실패 {bad}행 무시", flush=True)
    return recs


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl", default=os.path.join(PROJ, "data", "reports", "r48_publish_probe.jsonl"))
    ap.add_argument("--threshold", default="07:30")
    a = ap.parse_args(argv)
    th = time.fromisoformat(a.threshold)

    if not os.path.exists(a.jsonl):
        print(json.dumps({"error": "probe 파일 없음", "path": a.jsonl}, ensure_ascii=False))
        return 3
    recs = load_records(a.jsonl)
    if not recs:
        print(json.dumps({"error": "probe 0건"}, ensure_ascii=False))
        return 3

    recs.sort(key=lambda r: r.get("probe_at", ""))
    all_targets = sorted({r.get("target_session") for r in recs if r.get("target_session")})
    focus = all_targets[-1] if all_targets else None
    recs = [r for r in recs if r.get("target_session") == focus]
    if not recs:
        print(json.dumps({"error": "관심 세션 probe 없음"}, ensure_ascii=False))
        return 3
    first = next((r for r in recs if r.get("published")), None)
    last = recs[-1]
    last_day = datetime.fromisoformat(last["probe_at"]).date()
    covers_th = any(datetime.fromisoformat(r["probe_at"]).time() >= th
                    and datetime.fromisoformat(r["probe_at"]).date() == last_day
                    for r in recs if r.get("probe_at"))
    before_th = None
    if first:
        before_th = datetime.fromisoformat(first["probe_at"]).time() < th
    out = {
        "n_probes": len(recs),
        "focus_session": focus,
        "window": [recs[0].get("probe_at"), last.get("probe_at")],
        "first_published_at": first["probe_at"] if first else None,
        "first_published_markets": first.get("markets") if first else None,
        "last_probe": {"at": last.get("probe_at"), "markets": last.get("markets"),
                       "published": last.get("published")},
        "threshold": a.threshold,
        "published_before_threshold": bool(before_th),
        "sample_covers_threshold": bool(covers_th),
        "blocked_seen": any(r.get("blocked") for r in recs),
        "transient_probes": sum(1 for r in recs if r.get("transient")),
    }
    if first and before_th:
        out["verdict"] = "07:30_로더_가능"
        rc = 0
    elif covers_th and not first:
        out["verdict"] = "07:30_까지_미발행"
        rc = 1
    elif first:
        out["verdict"] = "07:30_이후_발행"
        rc = 1
    else:
        out["verdict"] = "표본부족"
        rc = 3
    print(json.dumps(out, ensure_ascii=False))
    print(f"R48 판정: {out['verdict']} · 세션 {focus} · probe {out['n_probes']}건 · 최초가용 "
          f"{out['first_published_at'] or '없음'} · 문턱 {a.threshold} · 표본문턱포함={out['sample_covers_threshold']}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

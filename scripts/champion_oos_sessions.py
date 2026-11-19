#!/usr/bin/env python3
"""챔피언 OOS 세션 기대·t 의 **정본 파일** 생성 (재배포 게이트 B축 입력).

WHY(2026-11-19): `scripts/redeploy_gate.py` 의 B축은 "세션 기대·t" 를 읽어야 하는데,
정본 산출물이 없어 항상 '측정 불가 = BLOCK' 이었다. 실제 계산은 이미 존재한다 —
`services/xgboost-ml/reports/overnight/expectancy_is_oos_split.json`
(러너 `scripts/_expectancy_is_oos_split.py`, 후보 2,251행·113세션, topk=3, 왕복 0.21%).
그 파일의 스키마가 게이트와 달라 읽히지 않았으므로, **여기서 게이트 계약으로 정규화**한다.

읽기 전용. 출력:
  data/reports/champion_oos_sessions.json
  {"source": <원본 경로>, "created_at": <원본>, "models": {
     "champion": {"n_sessions":25,"session_expectancy_pct":-0.8754,"t_stat":-2.51,
                  "train_end":"2026-10-01","sample":{...}}}, "primary":"champion"}
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SRC = os.path.join(REPO, "services/xgboost-ml/reports/overnight/expectancy_is_oos_split.json")
DEFAULT_OUT = os.path.join(REPO, "data/reports/champion_oos_sessions.json")


def build(src: dict, primary: str) -> dict:
    sample = src.get("sample") or {}
    models = {}
    for name, block in (src.get("models") or {}).items():
        oos = (block or {}).get("oos") or {}
        models[name] = {
            "n_sessions": oos.get("n_sessions"),
            "session_expectancy_pct": oos.get("avg_pct"),
            "median_pct": oos.get("median_pct"),
            "pos_sessions_pct": oos.get("pos_sessions_pct"),
            "t_stat": oos.get("t_stat"),
            "train_end": (block or {}).get("train_end"),
        }
    return {
        "source": DEFAULT_SRC,
        "created_at": src.get("created_at"),
        "normalized_at": datetime.now(KST).isoformat(timespec="seconds"),
        "sample": {
            "n_rows": sample.get("n_rows"), "n_sessions": sample.get("n_sessions"),
            "date_start": sample.get("date_start"), "date_end": sample.get("date_end"),
            "topk": sample.get("topk"), "exit": sample.get("exit"),
            "fee_round_trip_pct": sample.get("fee_round_trip_pct"),
        },
        "primary": primary,
        "models": models,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="챔피언 OOS 세션 기대·t 정본화")
    ap.add_argument("--src", default=DEFAULT_SRC)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--primary", default="champion")
    args = ap.parse_args(argv)

    if not os.path.exists(args.src):
        print("입력 없음: %s — 산출기(_expectancy_is_oos_split.py)를 먼저 실행하라" % args.src)
        return 2
    src = json.load(open(args.src, encoding="utf-8"))
    if not (src.get("models") or {}):
        print("입력에 models 키가 없다(스키마 불일치): %s" % args.src)
        return 2

    out = build(src, args.primary)
    out["source"] = os.path.relpath(args.src, REPO)
    # 게이트(_read_session_metrics)의 '평면형' 계약도 함께 만족시킨다 —
    # primary 모델 수치를 최상위 avg_pct/t_stat/n_sessions 로 노출.
    primary = (out.get("models") or {}).get(args.primary) or {}
    out["avg_pct"] = primary.get("session_expectancy_pct")
    out["t_stat"] = primary.get("t_stat")
    out["n_sessions"] = primary.get("n_sessions")
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    tmp = args.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(tmp, args.out)

    for name, m in out["models"].items():
        print("%-14s n=%s avg=%s t=%s (train_end %s)"
              % (name, m["n_sessions"], m["session_expectancy_pct"], m["t_stat"], m["train_end"]))
    print("정본 저장:", args.out, "| primary =", args.primary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

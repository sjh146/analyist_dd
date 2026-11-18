#!/usr/bin/env python3
"""oos_accrual_log.py — 진짜 OOS 표본 적립 원장 (한 줄 append).

WHY (2026-11-19 CG353 실측): champion_robust_eval 의 per-date AUC SD 를 14폴드에서
풀링하면 0.0587 이고, SE(n날짜)=0.0587/sqrt(n) → n=10 이면 0.0186 으로 사전문턱
+0.02 와 같은 크기다. 즉 '10날짜 채점'으로는 +0.02 를 해상할 수 없고, 판정은
'노이즈'가 아니라 '미해상(underpowered)'이다. 해상에 필요한 것은 표본인데,
배포 챔피언의 진짜 OOS 는 캘린더가 흘러야만 늘어난다 — 그래서 매주 같은 창·같은
프로토콜로 채점한 값을 **원장에 적립**하고, 누적 날짜수로 SE 를 함께 출력한다.

사용:
  python3 scripts/oos_accrual_log.py --append services/xgboost-ml/reports/overnight/oos_accrual_20261120.json
  python3 scripts/oos_accrual_log.py --show
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

LEDGER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                      "data", "reports", "oos_accrual.jsonl")
# CG353 실측: within-fold per-date AUC SD (14 폴드 풀링)
PER_DATE_SD = 0.0587


def load_summary(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    return {
        "measured_at": d.get("measured_at"),
        "model_dir": d.get("model_dir"),
        "window_anchor": d.get("window_anchor"),
        "train_start": d.get("train_start"),
        "train_end": d.get("train_end"),
        "oos_only": d.get("oos_only"),
        "robust_auc": d.get("robust_auc"),
        "dates_scored": d.get("dates_scored"),
        "rows_scored": d.get("rows_scored"),
        "folds": [{"fold": x.get("fold"), "window": x.get("window"),
                   "n_dates": x.get("n_dates"), "auc_mean": x.get("auc_mean")}
                  for x in d.get("folds", [])],
        "source": os.path.basename(path),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="OOS 표본 적립 원장")
    ap.add_argument("--append", default=None, help="champion_robust_eval 요약 JSON")
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args(argv)

    if a.append:
        rec = load_summary(a.append)
        os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
        with open(LEDGER, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        n = rec.get("dates_scored") or 0
        print(f"[append] {rec['source']} robust={rec['robust_auc']} dates={n} "
              f"SE(1σ)={PER_DATE_SD / math.sqrt(n):.4f}" if n else "[append] dates=0")

    if a.show or not a.append:
        if not os.path.exists(LEDGER):
            print("원장 없음 — 아직 적립 0건")
            return 0
        rows = [json.loads(l) for l in open(LEDGER, encoding="utf-8") if l.strip()]
        dates = sum(int(r.get("dates_scored") or 0) for r in rows)
        print(f"적립 {len(rows)}회 · 누적 채점 날짜 {dates} (OOS 창은 서로 겹칠 수 있음)")
        for r in rows[-8:]:
            n = int(r.get("dates_scored") or 0)
            print(f"  {r.get('measured_at')} auc={r.get('robust_auc')} dates={n} "
                  f"SE={PER_DATE_SD / math.sqrt(n):.4f}" if n else f"  {r}")
        need = {se: math.ceil((PER_DATE_SD / se) ** 2) for se in (0.01, 0.007)}
        print(f"해상 목표: SE 0.010 → {need[0.01]}날짜 · SE 0.007 → {need[0.007]}날짜")
    return 0


if __name__ == "__main__":
    sys.exit(main())

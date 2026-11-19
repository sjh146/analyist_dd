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
                   "n_dates": x.get("n_dates"), "auc_mean": x.get("auc_mean"),
                   "dates": x.get("dates") or []}
                  for x in d.get("folds", [])],
        "source": os.path.basename(path),
    }


def _record_key(rec: dict) -> tuple:
    """적립 레코드의 동일성 키 = (앵커, 폴드별 (폴드번호, 창)).

    왜 필요한가(실측 2026-11-19): 같은 날 다시 채점하면 앵커·창·AUC 가 그대로인 레코드가
    한 줄 더 쌓였고, `--show` 가 dates_scored 를 **단순 합산**해 '누적 20날짜'로 표시했다
    (실제로는 같은 10날짜). 표본 수를 부풀리면 SE 를 과소평가해 '미해상'을 '해상'으로
    오독하게 된다 — 중복 적립은 건너뛰고, 누적은 **창 단위 union** 으로 센다.
    """
    folds = tuple(sorted((str(f.get("fold")), tuple(f.get("window") or []))
                         for f in (rec.get("folds") or [])))
    return (rec.get("window_anchor"), folds)


def _read_ledger() -> list:
    if not os.path.exists(LEDGER):
        return []
    out = []
    with open(LEDGER, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def distinct_dates(rows) -> int:
    """실제로 채점된 **고유 날짜 수**(창 겹침·앵커 이동의 중복 제거).

    왜 날짜 단위인가(실측 2026-11-19 CG354→CG356): 종전 구현은 (폴드번호, 창구간) 키의 union 을
    세었다. 앵커가 2일만 밀려도 창구간이 '새 창'으로 보여 같은 날짜를 두 번 세고, 누적 20날짜로
    표시되며 SE 를 0.0186→0.0131 로 과소평가한다(실제 표본은 그대로). champion_robust_eval 이
    이제 폴드별 `dates`(채점된 날짜)를 남기므로 날짜 집합으로 센다. 옛 레코드(dates 없음)는
    창구간 키로 폴백한다 — 그 값은 **상한**이지 정확값이 아니다.
    """
    real = set()
    legacy = {}
    legacy_only = False
    for r in rows:
        for f in (r.get("folds") or []):
            ds = f.get("dates") or []
            if ds:
                real.update(str(x) for x in ds)
            else:
                legacy_only = True
                key = (str(f.get("fold")), tuple(f.get("window") or []))
                legacy[key] = max(legacy.get(key, 0), int(f.get("n_dates") or 0))
    n_legacy = sum(legacy.values())
    return max(len(real), n_legacy) if legacy_only else len(real)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="OOS 표본 적립 원장")
    ap.add_argument("--append", default=None, help="champion_robust_eval 요약 JSON")
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args(argv)

    if a.append:
        rec = load_summary(a.append)
        os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
        rows = _read_ledger()
        if any(_record_key(r) == _record_key(rec) for r in rows):
            print(f"[skip] 중복 적립 — 앵커 {rec.get('window_anchor')} · 같은 창/같은 날짜"
                  f"(새 날짜가 없다). 누적은 창 union 으로 센다")
        else:
            with open(LEDGER, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n = rec.get("dates_scored") or 0
            print(f"[append] {rec['source']} robust={rec['robust_auc']} dates={n} "
                  f"SE(1σ)={PER_DATE_SD / math.sqrt(n):.4f}" if n else "[append] dates=0")

    if a.show or not a.append:
        rows = _read_ledger()
        if not rows:
            print("원장 없음 — 아직 적립 0건")
            return 0
        n_distinct = distinct_dates(rows)
        print(f"적립 {len(rows)}회 · 누적 채점 날짜 {n_distinct} (창 union 기준 · 중복 창 제외)")
        for r in rows[-8:]:
            n = int(r.get("dates_scored") or 0)
            print(f"  {r.get('measured_at')} auc={r.get('robust_auc')} dates={n} "
                  f"SE={PER_DATE_SD / math.sqrt(n):.4f}" if n else f"  {r}")
        need = {se: math.ceil((PER_DATE_SD / se) ** 2) for se in (0.01, 0.007)}
        print(f"해상 목표: SE 0.010 → {need[0.01]}날짜 · SE 0.007 → {need[0.007]}날짜"
              f" (현재 {n_distinct}날짜 → SE {PER_DATE_SD / math.sqrt(max(n_distinct, 1)):.4f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

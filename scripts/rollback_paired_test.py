#!/usr/bin/env python3
"""rollback_paired_test.py — 되돌림 판정의 **유의성**을 짝(paired) 세션 Δ 로 검정한다.

WHY(2026-11-26): `champion_rollback_monitor` 의 규칙은 점추정 비교다(챔피언 − 백업 ≤ degrade_pct).
그 규칙으로 첫 판정이 rollback(차 −0.488%p)으로 나왔는데, 두 arm 의 개별 t 가 |1.89|/|0.33| 이고
중앙값은 방향이 반대였다(챔피언 −0.74 vs 백업 −0.84). 같은 세션·같은 종목을 짝지어 보면 개별
표준오차보다 훨씬 작은 SE 를 얻을 수 있으므로, **실행 가능한 판정인지**를 이 도구로 가른다.

사전등록(변경 금지): 아래 3개를 모두 만족할 때만 '롤백 실행 근거'로 기록한다.
  ① 짝 Δ 평균 < 0 이고 ② 블록 부트스트랩(겹침보정, block=5) t ≤ -2 ③ CI95 상한 < degrade_pct(-0.2)
  미달이면 '점추정만 문턱 초과 — 실행 근거 아님'으로 기록하고 챔피언을 유지한다.

입력: `_expectancy_is_oos_split.py --dump-sessions` 로 만든 JSON(models[*].sessions = {날짜: %p}).

사용:
  python3 scripts/rollback_paired_test.py --raw services/xgboost-ml/reports/overnight/rollback_evidence_raw.json
"""
from __future__ import annotations

import argparse
import json
import os
import random

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "data", "reports", "rollback_paired_test.json")


def paired_series(raw: dict, prev_dir: str, train_end: str | None = None):
    models = raw.get("models") or {}
    champ = (models.get("champion") or {})
    prev = (models.get(prev_dir) or {})
    cs, ps = champ.get("sessions") or {}, prev.get("sessions") or {}
    if not cs or not ps:
        return None
    te = train_end or champ.get("train_end") or prev.get("train_end")
    dates = sorted(d for d in cs if d in ps and (te is None or d > te))
    return dates, [round(cs[d] - ps[d], 4) for d in dates]


def _stats(xs):
    n = len(xs)
    if n < 2:
        return {"n": n}
    mean = sum(xs) / n
    sd = (sum((x - mean) ** 2 for x in xs) / (n - 1)) ** 0.5
    return {"n": n, "mean": round(mean, 4), "sd": round(sd, 4),
            "t_iid": round(mean / (sd / n ** 0.5), 3) if sd else None,
            "pos": sum(1 for x in xs if x > 0)}


def block_boot(xs, block=5, n_boot=4000, seed=0):
    n = len(xs)
    if n < block + 1:
        return {"n_boot": 0}
    rng = random.Random(seed)
    k = max(1, n // block)
    means = []
    for _ in range(n_boot):
        sample = []
        for _i in range(k):
            s = rng.randrange(n)
            sample.extend(xs[(s + j) % n] for j in range(block))
        means.append(sum(sample) / len(sample))
    means.sort()
    mean = sum(xs) / n
    se = (sum((m - sum(means) / len(means)) ** 2 for m in means) / (len(means) - 1)) ** 0.5
    return {"block": block, "n_boot": n_boot, "se": round(se, 4),
            "t": round(mean / se, 3) if se else None,
            "ci95": [round(means[int(0.025 * len(means))], 4), round(means[int(0.975 * len(means))], 4)],
            "p_one_sided": round(sum(1 for m in means if m >= 0) / len(means), 4)}


def judge(res, degrade_pct=-0.2):
    b = res.get("boot") or {}
    ci = b.get("ci95") or [None, None]
    ok = (res.get("mean", 0) < 0 and (b.get("t") is not None and b["t"] <= -2)
          and ci[1] is not None and ci[1] < degrade_pct)
    return ("롤백 실행 근거" if ok else "점추정만 문턱 초과 — 실행 근거 아님"), ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="되돌림 판정 짝 검정")
    ap.add_argument("--raw", required=True)
    ap.add_argument("--prev-dir", default=None)
    ap.add_argument("--degrade-pct", type=float, default=-0.2)
    ap.add_argument("--block", type=int, default=5)
    ap.add_argument("--json-out", default=OUT)
    a = ap.parse_args(argv)

    raw_path = a.raw if os.path.isabs(a.raw) else os.path.join(REPO, a.raw)
    raw = json.load(open(raw_path, encoding="utf-8"))
    prev_dir = a.prev_dir
    if not prev_dir:
        ev = os.path.join(REPO, "data", "reports", "rollback_evidence.json")
        prev_dir = (json.load(open(ev, encoding="utf-8")).get("prev_dir")
                    if os.path.exists(ev) else None)
    if not prev_dir:
        print("[x] prev_dir 미상 — --prev-dir 로 지정하라")
        return 2
    got = paired_series(raw, prev_dir)
    if not got:
        print("[x] 세션 시계열이 없다 — 생산기를 --dump-sessions 로 다시 실행해야 한다")
        return 2
    dates, deltas = got
    res = {"prev_dir": prev_dir, "champion_vs_prev": True,
           "window": [dates[0], dates[-1]] if dates else None,
           "n_sessions": len(deltas), "mean": round(sum(deltas) / len(deltas), 4),
           "iid": _stats(deltas), "boot": block_boot(deltas, a.block),
           "halves": {"front": round(sum(deltas[: len(deltas) // 2]) / max(1, len(deltas) // 2), 4),
                      "back": round(sum(deltas[len(deltas) // 2:]) / max(1, len(deltas) - len(deltas) // 2), 4)},
           "series": dict(zip(dates, deltas))}
    verdict, ok = judge(res, a.degrade_pct)
    res["verdict"] = verdict
    res["executable"] = bool(ok)
    print(json.dumps({k: v for k, v in res.items() if k != "series"}, ensure_ascii=False, indent=1))
    os.makedirs(os.path.dirname(a.json_out), exist_ok=True)
    with open(a.json_out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print("[ok] 기록:", os.path.relpath(a.json_out, REPO))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""패널(유니버스·창) 드리프트 감사 — '같은 config, 다른 패널' 의 절대 AUC 이동폭을 잰다.

왜: 스코어보드 북극성은 'arm 의 폴드평균 절대값 vs 고정 기준선 0.5406' 으로 개선을 판정하고
사전문턱은 +0.02 다. 그런데 기준선 태그(kind·q·core_only·horizon)에 패널/유니버스가 없어
서로 다른 패널에서 측정된 arm 이 같은 자로 비교된다. 이 스크립트는 그 전제를 실측한다:
  (1) 같은 arm id 가 서로 다른 패널에서 측정된 값의 범위(드리프트)
  (2) 5폴드 평균의 표준오차(std/√folds) — 절대 비교의 해상도
  (3) 판정에 계상되는 '측정 사이클' 의 패널 분포
사용: python3 scripts/panel_drift_audit.py [--json-out data/reports/panel_drift_audit.json]
읽기 전용(원장만 읽는다). 학습·DB 쓰기 없음.
"""
import json
import math
import os
import statistics as stats
from collections import defaultdict

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(PROJ, "data/reports/model_engineer_ledger.jsonl")
OUT = os.path.join(PROJ, "data/reports/panel_drift_audit.json")
BASELINE = 0.5406
THRESH = 0.02


def _rows(path):
    out = []
    bad = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    bad += 1
    if bad:
        print(f"⚠ 원장 파싱 실패 {bad}행(스킵)")
    return out


def _arms(rec):
    parsed = rec.get("parsed") if isinstance(rec.get("parsed"), dict) else {}
    per = parsed.get("per_exp") or {}
    if not isinstance(per, dict):
        return {}
    return {k: v for k, v in per.items() if isinstance(v, dict) and isinstance(v.get("mean"), (int, float))}


def _panel(rec):
    parsed = rec.get("parsed") if isinstance(rec.get("parsed"), dict) else {}
    cfg = parsed.get("config") if isinstance(parsed.get("config"), dict) else {}
    name = cfg.get("panel") or parsed.get("panel") or "?"
    return str(name).rsplit("/", 1)[-1]


def main():
    recs = _rows(LEDGER)
    by_arm = defaultdict(list)
    se_all = []
    measured_panels = defaultdict(int)
    n_measured = 0
    for rec in recs:
        arms = _arms(rec)
        if not arms:
            continue
        panel = _panel(rec)
        if rec.get("rc") == 0 and not (isinstance(rec.get("parsed"), dict) and rec["parsed"].get("error")):
            n_measured += 1
            measured_panels[panel] += 1
        for arm, val in arms.items():
            nf = val.get("folds")
            nf = len(nf) if isinstance(nf, list) else None
            sd = val.get("std")
            if isinstance(sd, (int, float)) and nf:
                se_all.append(sd / math.sqrt(nf))
            by_arm[arm].append({"ts": rec.get("ts"), "id": rec.get("id"), "panel": panel,
                                "mean": float(val["mean"]), "std": sd, "n_folds": nf})

    drift = []
    for arm, obs in by_arm.items():
        panels = {}
        for o in obs:
            panels.setdefault(o["panel"], []).append(o["mean"])
        if len(panels) < 2:
            continue
        per_panel = {p: round(sum(v) / len(v), 4) for p, v in panels.items()}
        vals = list(per_panel.values())
        drift.append({"arm": arm, "n_panels": len(per_panel), "per_panel": per_panel,
                      "spread": round(max(vals) - min(vals), 4),
                      "max": round(max(vals), 4), "min": round(min(vals), 4)})
    drift.sort(key=lambda d: -d["spread"])

    spreads = [d["spread"] for d in drift]
    out = {
        "generated_by": "scripts/panel_drift_audit.py",
        "ledger": LEDGER,
        "n_ledger_rows": len(recs),
        "n_measured_cycles": n_measured,
        "measured_panels": dict(sorted(measured_panels.items(), key=lambda kv: -kv[1])),
        "baseline": BASELINE,
        "pre_registered_threshold": THRESH,
        "arms_multi_panel": len(drift),
        "drift_spread": {
            "max": round(max(spreads), 4) if spreads else None,
            "median": round(stats.median(spreads), 4) if spreads else None,
            "n_ge_threshold": sum(1 for s in spreads if s >= THRESH),
        },
        "fold_mean_se": {
            "n": len(se_all),
            "median": round(stats.median(se_all), 4) if se_all else None,
            "p25": round(sorted(se_all)[len(se_all) // 4], 4) if se_all else None,
            "p75": round(sorted(se_all)[3 * len(se_all) // 4], 4) if se_all else None,
        },
        "arms": drift,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"원장 {len(recs)}행 · 측정 사이클 {n_measured} · 다패널 arm {len(drift)}")
    print(f"패널 드리프트: max {out['drift_spread']['max']} · median {out['drift_spread']['median']}"
          f" · 문턱(+{THRESH}) 이상 {out['drift_spread']['n_ge_threshold']}/{len(drift)}")
    print(f"폴드평균 SE: median {out['fold_mean_se']['median']} (n={len(se_all)})")
    print("측정 사이클 패널 분포:", out["measured_panels"])
    for d in drift[:8]:
        print(f"  {d['arm']}: spread {d['spread']:.4f} {d['per_panel']}")
    print("→", OUT)


if __name__ == "__main__":
    main()

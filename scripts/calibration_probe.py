#!/usr/bin/env python3
"""확률 보정 계층 진단 — cross-fitted Platt / isotonic (CG82 자율 선행, CG83).

배경(CG81 실측, 2026-10-03): walk-forward OOS 확률은 **과신**이다 — 신뢰도 10분위에서
상위 분위 예측 0.7220 vs 실제 양성률 0.5436 = **+0.1783**(하위는 −0.0567). 동시에 소비자
(swing)는 **절대문턱 0.55** 를 쓴다: 스윕 모델은 0.55 초과가 37.0% 인데 라이브 배포
챔피언은 0.0%(MT116, 3세션 무진입) → 판정 지표(AUC)와 매매 경로(절대문턱)가 다른 축이다.

이 프로브는 **같은 OOS 행**에서 raw vs calibrated 를 비교한다. 적합은 반드시
**폴드 밖(cross-fitted)** — 폴드 k 를 뺀 나머지로 보정기를 적합하고 폴드 k 에 적용한다
(전체에 적합해 전체로 평가하면 보정기 자신의 인샘플 낙관이 섞인다).

산출:
  ① Brier / logloss / AUC(raw vs calibrated) — 순위 지표(AUC)는 단조 변환이라 **불변이어야
     한다**(sanity: 값이 움직이면 구현 결함)
  ② 신뢰도 10분위 · ECE · 최대 과신(raw vs calibrated)
  ③ 절대문턱 표(건수·정밀도·실현수익) raw vs calibrated + top-k 커트오프 번역

⚠ 읽기 전용. AUC 를 올리는 레버가 아니라 **AUC 를 매매로 옮기는 경로**의 계측기다.

용법(컨테이너):
  docker exec stock_xgboost_ml python /app/scripts/calibration_probe.py \
      /app/reports/overnight/cg81_preds.jsonl \
      --json-out /app/reports/overnight/cg83_calibration.json
"""
import argparse
import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

# 컨테이너에서 `python scripts/calibration_probe.py` 로 돌려도 `import app` 이 되도록
# (스크립트는 /app/scripts/ → 두 단계 위가 /app). 호스트에서는 무해하다.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _auc(pairs):
    """pairs: list[(score, y)] -> AUC (Mann-Whitney, 동점 0.5 가중). 순수 파이썬."""
    import bisect
    pos = sorted(s for s, y in pairs if y == 1)
    neg = sorted(s for s, y in pairs if y != 1)
    if not pos or not neg:
        return None
    tot = 0.0
    for s in pos:
        lo = bisect.bisect_left(neg, s)
        hi = bisect.bisect_right(neg, s)
        tot += lo + 0.5 * (hi - lo)
    return tot / (len(pos) * len(neg))


def _brier(p, y):
    return sum((pi - yi) ** 2 for pi, yi in zip(p, y)) / len(y)


def _logloss(p, y):
    eps = 1e-12
    tot = 0.0
    for pi, yi in zip(p, y):
        q = min(max(pi, eps), 1 - eps)
        tot += yi * math.log(q) + (1 - yi) * math.log(1 - q)
    return -tot / len(y)


def _reliability(p, y, bins=10):
    """(bin list, ECE, 최대 과신 |pred−actual|)."""
    order = sorted(range(len(p)), key=lambda i: p[i])
    n = len(p)
    out = []
    ece = 0.0
    max_over = 0.0
    for b in range(bins):
        idx = order[b * n // bins:(b + 1) * n // bins]
        if not idx:
            continue
        pm = sum(p[i] for i in idx) / len(idx)
        ym = sum(y[i] for i in idx) / len(idx)
        gap = pm - ym
        out.append({"bin": b + 1, "n": len(idx), "pred": round(pm, 4),
                    "actual": round(ym, 4), "gap": round(gap, 4)})
        ece += (len(idx) / n) * abs(gap)
        max_over = max(max_over, abs(gap))
    return out, ece, max_over


def _threshold_table(rows, p, thrs):
    out = []
    for t in thrs:
        sel = [r for r, pi in zip(rows, p) if pi > t]
        if not sel:
            out.append({"threshold": t, "n": 0, "rate": 0.0, "precision": None, "fwd_ret": None})
            continue
        prec = sum(int(r["y_true"]) for r in sel) / len(sel)
        fr = [float(r["fwd_ret"]) for r in sel if r.get("fwd_ret") is not None]
        out.append({"threshold": t, "n": len(sel), "rate": round(len(sel) / len(rows), 4),
                    "precision": round(prec, 4),
                    "fwd_ret": round(sum(fr) / len(fr), 6) if fr else None})
    return out


def _topk_cutoff(rows, p, k):
    """top-k/일 정책이 함의하는 확률 커트오프(중앙·평균)."""
    byday = defaultdict(list)
    for r, pi in zip(rows, p):
        byday[str(r["date"])].append(pi)
    cuts = []
    for _, g in byday.items():
        g = sorted(g, reverse=True)
        if len(g) >= k:
            cuts.append(g[k - 1])
    cuts.sort()
    if not cuts:
        return None
    return {"k": k, "n_dates": len(cuts),
            "mean": round(sum(cuts) / len(cuts), 4),
            "median": round(cuts[len(cuts) // 2], 4),
            "p10": round(cuts[int(0.10 * len(cuts))], 4),
            "p90": round(cuts[int(0.90 * len(cuts))], 4)}


def _metrics(rows, p):
    y = [int(r["y_true"]) for r in rows]
    rel, ece, max_over = _reliability(p, y)
    ps = sorted(p)
    auc = _auc(list(zip(p, y)))
    return {
        "auc": round(auc, 4) if auc is not None else None,
        "brier": round(_brier(p, y), 4),
        "logloss": round(_logloss(p, y), 4),
        "ece": round(ece, 4),
        "max_overconf": round(max_over, 4),
        "p50": round(ps[len(ps) // 2], 4),
        "p90": round(ps[int(0.90 * len(ps))], 4),
        "max": round(max(ps), 4),
        "reliability": rel,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("preds")
    ap.add_argument("--json-out", default="")
    ap.add_argument("--thresholds", default="0.45,0.50,0.55,0.60")
    ap.add_argument("--topk", type=int, default=10)
    ap.add_argument("--methods", default="platt,isotonic")
    a = ap.parse_args()

    rows = []
    with open(a.preds, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("y_pred") is None or r.get("y_true") is None or r.get("date") is None:
                continue
            rows.append(r)
    if not rows:
        print("행이 없다")
        return 1

    try:
        from app.calibration import PlattCalibrator, IsotonicCalibrator
    except Exception as e:  # pragma: no cover
        print(f"app.calibration import 실패: {e}")
        return 2

    thrs = [float(x) for x in a.thresholds.split(",")]
    methods = [m.strip() for m in a.methods.split(",") if m.strip()]
    folds = sorted({int(r["fold"]) for r in rows})
    dates = sorted({str(r["date"]) for r in rows})
    y_all = [int(r["y_true"]) for r in rows]
    base_rate = sum(y_all) / len(y_all)
    raw_p = [float(r["y_pred"]) for r in rows]

    print(f"파일: {a.preds}")
    print(f"행 {len(rows)} · 폴드 {folds} · 날짜 {len(dates)} ({dates[0]}~{dates[-1]})")
    print(f"양성률 {base_rate:.4f}")

    raw = _metrics(rows, raw_p)
    raw["thresholds"] = _threshold_table(rows, raw_p, thrs)
    raw["topk"] = _topk_cutoff(rows, raw_p, a.topk)

    result = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        "preds_file": a.preds,
        "n_rows": len(rows),
        "n_dates": len(dates),
        "date_min": dates[0],
        "date_max": dates[-1],
        "n_folds": len(folds),
        "base_rate": round(base_rate, 4),
        "topk": a.topk,
        "raw": raw,
        "calibrated": {},
    }

    for m in methods:
        cls = {"platt": PlattCalibrator, "isotonic": IsotonicCalibrator}.get(m)
        if cls is None:
            print(f"알 수 없는 method: {m}")
            continue
        # cross-fit: fold k 는 나머지 폴드로 적합한 보정기로만 예측한다
        pred = [None] * len(rows)
        per_fold = {}
        for k in folds:
            tr = [i for i, r in enumerate(rows) if int(r["fold"]) != k]
            te = [i for i, r in enumerate(rows) if int(r["fold"]) == k]
            if not tr or not te:
                continue
            cal = cls().fit([float(rows[i]["y_pred"]) for i in tr],
                            [int(rows[i]["y_true"]) for i in tr])
            out = list(cal.calibrate([float(rows[i]["y_pred"]) for i in te]))
            for j, i in enumerate(te):
                pred[i] = float(out[j])
            yy = [int(rows[i]["y_true"]) for i in te]
            f_auc = _auc(list(zip([pred[i] for i in te], yy)))
            per_fold[str(k)] = {"n": len(te),
                                "brier": round(_brier([pred[i] for i in te], yy), 4),
                                "auc": round(f_auc, 4) if f_auc is not None else None}
        if any(x is None for x in pred):
            print(f"{m}: 일부 행 미예측 — 건너뜀")
            continue
        met = _metrics(rows, pred)
        met["thresholds"] = _threshold_table(rows, pred, thrs)
        met["topk"] = _topk_cutoff(rows, pred, a.topk)
        met["per_fold"] = per_fold
        result["calibrated"][m] = met
        print(f"\n[{m}] cross-fitted")
        print(f"  AUC {met['auc']} (raw {raw['auc']}) · Brier {met['brier']} (raw {raw['brier']})"
              f" · logloss {met['logloss']} (raw {raw['logloss']})")
        print(f"  ECE {met['ece']} (raw {raw['ece']}) · 최대 과신 {met['max_overconf']}"
              f" (raw {raw['max_overconf']})")
        if met["topk"] and raw["topk"]:
            print(f"  top-{a.topk}/일 커트오프 median {met['topk']['median']}"
                  f" (raw {raw['topk']['median']})")

    # 최선 보정기 선택(보정 자체의 효과만 보려면 ECE·Brier 동시 개선을 본다)
    best = None
    for m, v in result["calibrated"].items():
        gain = raw["brier"] - v["brier"]
        if best is None or gain > best[1]:
            best = (m, gain)
    if best:
        result["best_method"] = best[0]
        result["brier_gain"] = round(best[1], 4)
        result["ece_gain"] = round(raw["ece"] - result["calibrated"][best[0]]["ece"], 4)
        result["auc_delta"] = round(result["calibrated"][best[0]]["auc"] - raw["auc"], 4)
        print(f"\n최선: {best[0]} · Brier 이득 {result['brier_gain']:+.4f}"
              f" · ECE 이득 {result['ece_gain']:+.4f} · AUC 변화 {result['auc_delta']:+.4f}"
              " (단조 변환이면 0 이어야 한다)")

    if a.json_out:
        tmp = a.json_out + ".tmp"
        os.makedirs(os.path.dirname(a.json_out) or ".", exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        os.replace(tmp, a.json_out)
        print(f"\n요약 저장: {a.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

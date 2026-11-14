#!/usr/bin/env python3
"""CG275 — 폴드별 '학습셋(in-sample) AUC vs OOS AUC' 갭 리포트.

WHY(2026-11-14, 19사이클 무개선): 조정 축(피처변환·HP·유니버스·라벨)의 실측 상한은 +0.011 이고
데이터 축 스크린(8테이블 89컬럼 × 2패널 × 2라벨)에서도 문턱(|AUC−0.5|≥0.065) 통과가 0개였다.
남은 질문은 "AUC 가 안 오르는 이유가 **정보 상한**인가 **과적합/분산**인가"다. 둘은 처방이 정반대다:
  · 갭(train−OOS) 이 크면 → 모델이 학습셋을 외운다 = 학습 신호는 있는데 일반화가 안 된다
    → 용량·정규화(분산) 투자, 데이터 추가는 무효.
  · 갭이 작고 OOS ≈ train ≈ 0.5 근처면 → 모델이 이미 정보를 다 쓴다 = **정보 상한**
    → 모델 축 종결, 데이터 축(신규 원천·시점정합 피처)만 남는다.

입력: `wf_label_sweep.py --report-train-auc` 가 남긴 요약 JSON(folds[].train_auc, folds[].mean).
      train_auc 가 하나도 없으면 '측정 불가'(플래그 미지정)로 rc=2 — 0 갭을 '정보 상한'으로
      오독하지 않게 하드 실패시킨다.
출력: --out JSON { arms: {exp: {train_mean, oos_mean, gap, gap_by_fold, ...}}, verdict }
"""
import argparse
import json
import statistics
import sys


def _f(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None          # NaN 제거


def summarize(summary: dict) -> dict:
    arms = {}
    for r in (summary.get("results") or []):
        exp = r.get("exp")
        folds = r.get("folds") or {}
        train, oos, gaps, names = [], [], [], []
        for fname, fv in sorted(folds.items()):
            if not isinstance(fv, dict):
                continue
            t = _f(fv.get("train_auc"))
            o = _f(fv.get("ens_pooled_auc"))
            if o is None:
                o = _f(fv.get("mean"))
            if t is None or o is None:
                continue
            train.append(t)
            oos.append(o)
            gaps.append(t - o)
            names.append(fname)
        if not gaps:
            arms[exp] = {"n_folds": 0, "error": "train_auc 없음(--report-train-auc 미지정?)",
                         "desc": r.get("desc")}
            continue
        arms[exp] = {
            "desc": r.get("desc"),
            "n_folds": len(gaps),
            "n_seeds": (summary.get("config") or {}).get("seeds"),
            "folds": names,
            "train_by_fold": [round(x, 4) for x in train],
            "oos_by_fold": [round(x, 4) for x in oos],
            "train_mean": round(statistics.mean(train), 4),
            "train_std": round(statistics.pstdev(train), 4),
            "oos_mean": round(statistics.mean(oos), 4),
            "oos_std": round(statistics.pstdev(oos), 4),
            "gap_by_fold": [round(x, 4) for x in gaps],
            "gap": round(statistics.mean(gaps), 4),
            "gap_std": round(statistics.pstdev(gaps), 4),
            "gap_min": round(min(gaps), 4),
            "gap_max": round(max(gaps), 4),
            # OOS 가 우연(0.5)에서 얼마나 떨어졌는가 = 이 모델이 실제로 가진 정보량의 하한
            "oos_edge_vs_chance": round(statistics.mean(oos) - 0.5, 4),
        }
    return {"arms": arms, "config": summary.get("config"), "finished_at": summary.get("finished_at"),
            "panel": (summary.get("config") or {}).get("panel")}


def verdict_for(arms: dict, gap_ceiling: float = 0.30, gap_floor: float = 0.10) -> tuple:
    """갭 분포로 처방을 가른다. 반환 (verdict, detail, delta).

    문턱·처방은 사전등록 그대로(항목 success — 사후에 뒤집지 않는다):
      갭 ≥0.30 → 학습셋 암기, 일반화분 없음 = 정보 상한(데이터 투자)
      갭 ≤0.10 → 학습셋조차 못 맞힘 = 모델 용량·표현력(언더핏) 투자
    판정은 구동기(judge_insample_gap)가 같은 숫자로 다시 계산한다 — 여기 문구는 사람용 요약이다.
    """
    ok = {k: v for k, v in arms.items() if v.get("n_folds")}
    if not ok:
        return "판정불가", "폴드별 train_auc 가 없다(--report-train-auc 미지정 또는 학습 실패)", None
    name = max(ok, key=lambda k: ok[k]["n_folds"])
    a = ok[name]
    gap = a["gap"]
    txt = (f"{name}: 폴드 {a['n_folds']}개 · train {a['train_mean']}±{a['train_std']} vs "
           f"OOS {a['oos_mean']}±{a['oos_std']} → 갭 {gap}±{a['gap_std']} "
           f"(min {a['gap_min']} max {a['gap_max']}) · OOS−0.5 {a['oos_edge_vs_chance']}")
    if gap >= gap_ceiling:
        return ("정보 상한 — 학습셋 암기, 일반화분 없음(데이터 축)", txt + f" — 갭≥{gap_ceiling}", gap)
    if gap <= gap_floor:
        return ("모델 용량·표현력 축(언더핏) — 정규화·용량 투자", txt + f" — 갭≤{gap_floor}", gap)
    return ("혼합 — 갭 중간대", txt + f" — {gap_floor} < 갭 < {gap_ceiling}", gap)


def main() -> int:
    ap = argparse.ArgumentParser(description="in-sample vs OOS AUC 갭 리포트(CG275)")
    ap.add_argument("--summary", required=True, help="wf_label_sweep 요약 JSON 경로")
    ap.add_argument("--out", default=None, help="리포트 JSON 출력 경로")
    ap.add_argument("--gap-ceiling", type=float, default=0.30)
    ap.add_argument("--gap-floor", type=float, default=0.10)
    a = ap.parse_args()

    try:
        with open(a.summary, encoding="utf-8") as f:
            summary = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"요약 읽기 실패: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    rep = summarize(summary)
    v, d, delta = verdict_for(rep["arms"], a.gap_ceiling, a.gap_floor)
    rep["verdict"], rep["detail"] = v, d
    rep["thresholds"] = {"gap_ceiling": a.gap_ceiling, "gap_floor": a.gap_floor}
    print(f"판정: {v}")
    print(f"근거: {d}")
    for k, s in rep["arms"].items():
        if s.get("n_folds"):
            print(f"  {k}: train {s['train_mean']} OOS {s['oos_mean']} 갭 {s['gap']} "
                  f"폴드별 {s['gap_by_fold']}")
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1)
        print(f"기록: {a.out}")
    return 0 if rep["arms"] and any(s.get("n_folds") for s in rep["arms"].values()) else 2


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""CG34 — 배포 챔피언 견고 AUC 의 학습구간 분해 (재계산 없이 창 재집계).

동기: retrain_champion(프로덕션 명령 `--days 90 --stock-limit 200`)로 2026-09-23 15:18 에
학습된 챔피언은 학습 구간 ≈ 2026-06-25 ~ 2026-09-23 이다(DB 실측: 같은 200종목 유니버스에서
06-25~09-23 = 12,600행 vs meta n_rows=12,629 ✓). CG31 의 5개 평가창 중 창4(05-29~07-20)는
학습구간과 겹치고 창5(07-28~09-15)는 전부 학습구간 안이다 → 창 평균 0.5302 는 오염된 값이다.
창은 서로 독립 표본이므로 **재실행 없이** 창을 갈라 집계할 수 있다.
"""
import json
import statistics

P = "/home/jhshi/analyist_dd/services/xgboost-ml/reports/champion_robust_eval.json"
CUT = "2026-06-25"

d = json.load(open(P))
folds = d["folds"]
oos = [f for f in folds if f["window"][1] < CUT]   # 창 전체가 학습 시작(06-25) 이전 = 진짜 OOS
ovl = [f for f in folds if f["window"][1] >= CUT]  # 창 끝이 학습구간에 닿음 = 오염
om = [f["auc_mean"] for f in oos]
im = [f["auc_mean"] for f in ovl]
allm = [f["auc_mean"] for f in folds]

out = {
    "source": P,
    "measured_at": d["measured_at"],
    "protocol": d["protocol"],
    "rows_scored": d["rows_scored"],
    "dates_scored": d["dates_scored"],
    "trained_through": CUT,
    "trained_through_evidence": (
        "프로덕션 명령 full_pipeline_dd.sh L311 `retrain_champion --days 90 --stock-limit 200`"
        " + DB 실측 200종목 06-25~09-23 = 12,600행 ≈ meta n_rows 12,629 · "
        "robust_auc.json 의 model_aucs 가 training-result-20260923-151854.json 과 일치"
    ),
    "windows": folds,
    "oos_folds": [f["fold"] for f in oos],
    "oos_auc": om,
    "oos_mean": round(statistics.mean(om), 4),
    "oos_std": round(statistics.pstdev(om), 4),
    "overlap_folds": [f["fold"] for f in ovl],
    "overlap_auc": im,
    "overlap_mean": round(statistics.mean(im), 4),
    "all_windows_mean": round(statistics.mean(allm), 4),
    "inflation": round(statistics.mean(im) - statistics.mean(om), 4),
    "oos_above_half": sum(1 for v in om if v > 0.5),
    "note": ("per_exp 를 만들지 않는다 — scoreboard._rec_best_mean 이 per_exp 를 arm 폴드 "
             "평균으로 읽어 거짓 '최고 arm'을 만든다(2026-09-29 CG31 오염 사고)."),
}
print(json.dumps(out, ensure_ascii=False, indent=2))

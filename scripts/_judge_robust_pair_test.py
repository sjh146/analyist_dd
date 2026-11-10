#!/usr/bin/env python3
"""judge_champion_baseline 의 '같은 런 병렬 채점' 경로 회귀 테스트 (2026-11-10 CG183).

왜: 재학습 후보(CG183)는 기록 기준선이 아니라 **같은 창·같은 런에서 동시에 잰 챔피언**과
짝지어야 한다. parsed['other_models'] 에 대조 모델 값이 있고 항목에 counterfactual_model 이
있을 때만 짝 경로로 가고, 없으면 종전('기준선 실측') 그대로여야 한다(동작 불변).

실행(컨테이너): docker exec stock_xgboost_ml sh -c 'cd /app && python3 scripts/_judge_robust_pair_test.py'
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_engineer_cycle as mec  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        fails.append(name)


parsed = {
    "robust_auc": 0.5700, "auc_std_across_folds": 0.0210,
    "fold_means": [0.5500, 0.5750, 0.5850], "auc_pooled": 0.5650,
    "auc_per_date_mean": 0.5700, "rows_scored": None,
    "other_models": {"champ": {"robust_auc": 0.5450, "folds": [0.5300, 0.5500, 0.5550],
                               "train_end": "2026-10-01"}},
}

item_pair = {"id": "CG183", "counterfactual_model": "champ"}
v, d, delta = mec.judge_champion_baseline(item_pair, parsed)
check("짝 경로 Δ 계산", abs((delta or 0) - 0.0250) < 1e-9, f"delta={delta}")
check("짝 신호 판정(Δ≥0.02)", v == "짝 신호", f"verdict={v}")
check("대조 이름 표기", "같은 런" in d, d[:80])

p2 = dict(parsed, robust_auc=0.5500)
v2, d2, delta2 = mec.judge_champion_baseline(item_pair, p2)
check("짝 노이즈 판정(Δ<0.02)", v2 == "짝 노이즈", f"verdict={v2} delta={delta2}")

v3, d3, delta3 = mec.judge_champion_baseline({"id": "CG180"}, parsed)
check("대조 모델 미지정 → 종전 경로", v3 == "기준선 실측" and delta3 is None, f"verdict={v3}")

v4, d4, _ = mec.judge_champion_baseline({"id": "X", "counterfactual_model": "nope"}, parsed)
check("대조 모델이 요약에 없음 → 종전 경로", v4 == "기준선 실측", f"verdict={v4}")

v5, d5, _ = mec.judge_champion_baseline({"id": "X"}, {"error": "요약 파일 없음"})
check("robust 없음 → 판정불가", v5 == "판정불가", f"verdict={v5}")

print("\n결과:", "ALL PASS" if not fails else f"{len(fails)} FAIL {fails}")
sys.exit(0 if not fails else 1)

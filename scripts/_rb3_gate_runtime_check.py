"""최종 판정: 런타임 게이트가 스모크 fold 의 선별 목록을 몇 개로 줄이는가?

두 수치가 모순이므로(이름집합 12 vs 계측 30) **런타임 함수를 그 데이터에 직접 호출**해 확정한다.
"""
import json
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

import train_curated as tc  # noqa: E402

d = json.load(open("/app/reports/overnight/smoke_co_summary.json"))
for r in d["results"]:
    print(f"\n[{r['exp']}] n_effective_features(계측)={r.get('n_effective_features')}")
    for k, v in (r.get("folds") or {}).items():
        sel = list(v.get("selected_features") or [])
        got = tc.select_curated_features(sel, True)
        got_nosent = tc.select_curated_features(sel, False)
        print(f"  {k}: 저장된 선별 {len(sel)} · 런타임 게이트(allow_sentiment=True) → {len(got)}"
              f" · (False) → {len(got_nosent)} | 계측 {v.get('n_effective_features')}")
        if len(got) != len(sel):
            print(f"      탈락: {[f for f in sel if f not in set(got)][:12]}")
print("\nCORE_FEATURES 총", len(tc.CORE_FEATURES))
print("CORE 전체:", sorted(tc.CORE_FEATURES))

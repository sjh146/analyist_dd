"""스모크 요약에서 '선별 vs 실효(게이트 통과)' 피처 수를 교차 확인.

목적: _rb3_curated_gate_probe.py 는 RB1 런(5폴드)의 selected_features 를 이름집합으로
셌고, wf_label_sweep 의 새 계측(n_effective_features = train_seed 가 돌려준 curated 길이)은
폴드별 실측값이다. 두 수치가 어긋나 보이면(22 vs 30) 계측이 아니라 내 해석이 틀린 것이다 →
여기서 폴드마다 sel ∩ CORE 를 직접 세어 어느 쪽이 맞는지 확정한다.
"""
import ast
import json
import os
import sys

ROOT = os.environ.get("RB_ROOT", "/app")
SUMMARY = os.environ.get("RB_SUMMARY", os.path.join(ROOT, "reports/overnight/smoke_co_summary.json"))
TC = os.path.join(ROOT, "scripts/train_curated.py")


def core_set():
    tree = ast.parse(open(TC).read())
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "CORE_FEATURES":
                    return set(ast.literal_eval(node.value))
    raise SystemExit("CORE_FEATURES 없음")


def main():
    core = core_set()
    d = json.load(open(SUMMARY))
    print("summary:", SUMMARY, "finished_at:", d.get("finished_at"))
    print("core:", len(core))
    for r in d["results"]:
        print(f"\n[{r['exp']}] auc={r.get('auc_mean')} n_eff={r.get('n_effective_features')} err={r.get('error')}")
        for k, v in (r.get("folds") or {}).items():
            sel = list(v.get("selected_features") or [])
            inter = [f for f in sel if f in core]
            print(f"   {k}: 선별 {len(sel)} → core교집합 {len(inter)} | 계측 n_effective={v.get('n_effective_features')}"
                  f" | mean={round(float(v['mean']), 4)}")
            if len(inter) != len(sel):
                print(f"      게이트에서 탈락: {[f for f in sel if f not in core][:12]}")


if __name__ == "__main__":
    sys.exit(main())

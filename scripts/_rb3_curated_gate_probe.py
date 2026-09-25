"""RB3 사전조사: 'curated 게이트'가 실제 모델 입력을 얼마나 깎는지 정량화.

⚠ 정정(2026-09-26, 같은 날 실측): 이 스크립트가 낸 "선별 30개 중 core 안 22개(31%)" 는
**wf_label_sweep 런에는 적용되지 않는다.** wf_label_sweep 은 L223~224 에서
`tc.select_curated_features` 를 항등함수로 몽키패치하므로 그 런의 게이트는 꺼져 있고,
계측값(실효피처 = 선별 수 그대로 [30]·[48])이 맞다. 이 스크립트는 '게이트를 켠 프로덕션
경로라면 무엇이 탈락하는가'를 보는 참고자료로만 쓰라(_rb3_gate_runtime_check.py 로 교차확인).

배경: wf_label_sweep 은 edge 로 topN 을 고른 뒤 ml.train_seed 를 호출하는데,
train_seed 는 내부에서 tc.select_curated_features(=CORE_FEATURES ∩ names)로
**다시 한 번** 걸러낸다. 즉 실험군이 고른 피처가 core 48 밖이면 조용히 탈락한다.
→ 라벨/피처풀/변환 실험들이 'core 48 키홀'을 통해 측정되고 있을 수 있다.

읽기 전용. 아무것도 수정하지 않는다.
"""
import ast
import json
import os
import re
import sys

import os as _o; ROOT = _o.environ.get("RB_ROOT", "/app")
TC = os.path.join(ROOT, "scripts/train_curated.py")
SUMMARY = os.path.join(ROOT, "reports/overnight/wf_label_sweep_summary.json")
PANEL_CANDIDATES = [
    os.path.join(ROOT, "app/models/wf/panel_420_asofpatch.npz"),
    os.path.join(ROOT, "services/xgboost-ml/models/wf/panel_420_asofpatch.npz"),
    os.path.join(ROOT, "app/models/wf/panel_420_asofpatch.npz"),
]


def load_core():
    src = open(TC).read()
    tree = ast.parse(src)
    core = sent = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "CORE_FEATURES":
                    core = ast.literal_eval(node.value)
                if isinstance(t, ast.Name) and t.id == "SENTIMENT_NEWS_FEATURES":
                    sent = ast.literal_eval(node.value)
    return core, sent


def main():
    core, sent = load_core()
    print(f"CORE_FEATURES = {len(core)}개  (sentiment/news 제외 대상 {len(sent or [])}개)")
    print("  앞 12개:", core[:12])

    panel = next((p for p in PANEL_CANDIDATES if os.path.exists(p)), None)
    print("panel:", panel)
    feat_names = None
    if panel:
        import numpy as np
        z = np.load(panel, allow_pickle=True)
        print("  npz keys:", list(z.keys())[:12])
        for k in ("feature_names", "features", "cols", "columns"):
            if k in z.keys():
                feat_names = [str(x) for x in z[k]]
                print(f"  {k}: {len(feat_names)}개 (예: {feat_names[:5]})")
                break
        # 행/기간 스냅샷
        for k in ("X", "y", "dates", "stock_code"):
            if k in z.keys():
                arr = z[k]
                print(f"  {k}: shape={getattr(arr, 'shape', None)}")

    if feat_names is None:
        print("!! 패널에서 피처명을 못 찾음 — 아래 게이트 계산은 요약 JSON 의 selected_features 기준으로만 수행")

    # 요약 JSON: RB1 런의 config 별 selected_features 를 모아 core 교집합률 계산
    d = json.load(open(SUMMARY))
    print("\n=== 요약(finished_at %s) config %d개" % (d.get("finished_at"), len(d["results"])))
    for r in d["results"]:
        sel_all = []
        for f in r.get("folds", {}).values():
            sel_all += list(f.get("selected_features") or [])
        sel_uniq = sorted(set(sel_all))
        in_core = [f for f in sel_uniq if f in core]
        n_sel_avg = (sum(len(f.get("selected_features") or []) for f in r.get("folds", {}).values())
                     / max(1, len(r.get("folds", {}))))
        print(f"  {r.get('exp'):22s} auc={r.get('auc_mean'):.4f} "
              f"폴드당 선별 {n_sel_avg:.1f}개 | 런 전체 유니크 {len(sel_uniq)}개 "
              f"| core 안 {len(in_core)}개 ({100.0*len(in_core)/max(1,len(sel_uniq)):.0f}%)")
        outside = [f for f in sel_uniq if f not in core]
        if outside:
            print(f"      core 밖(모델에 못 들어감): {outside[:15]}{' …' if len(outside) > 15 else ''}")

    if feat_names:
        core_in_panel = [f for f in core if f in feat_names]
        print(f"\n패널 피처 {len(feat_names)}개 중 core 와 교집합 = {len(core_in_panel)}개 "
              f"({100.0*len(core_in_panel)/len(core):.0f}% of core)")
        miss = [f for f in core if f not in feat_names]
        print(f"  core 인데 패널에 없음 {len(miss)}개: {miss[:10]}")


if __name__ == "__main__":
    sys.exit(main())

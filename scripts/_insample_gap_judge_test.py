"""CG275 판정기/파서 회귀 테스트 — 경계에서 판정이 뒤집히는지 검증(2026-11-14)."""
import json
import os
import sys
import tempfile

sys.path.insert(0, "/home/jhshi/analyist_dd/scripts")
import model_engineer_cycle as m

fails = []


def check(name, cond, extra=""):
    print(f"{'PASS' if cond else 'FAIL'} {name}{(' — ' + str(extra)) if extra and not cond else ''}")
    if not cond:
        fails.append(name)


def arm(gap, train=0.76, oos=0.53, n=5, seeds=3):
    return {"desc": "t", "n_folds": n, "n_seeds": seeds,
            "train_mean": train, "train_std": 0.1, "oos_mean": oos, "oos_std": 0.02,
            "gap": gap, "gap_std": 0.09, "gap_min": gap - 0.05, "gap_max": gap + 0.05,
            "gap_by_fold": [gap] * n, "oos_edge_vs_chance": round(oos - 0.5, 4)}


def report(arms):
    d = tempfile.mkdtemp(prefix="isgap")
    p = os.path.join(d, "r.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"arms": arms, "panel": "panel_prod200.npz",
                   "thresholds": {"gap_ceiling": 0.30, "gap_floor": 0.10}}, f, ensure_ascii=False)
    return p


item = {"id": "CG275", "paired_reference": "CO_core30_h5", "gap_ceiling": 0.30, "gap_floor": 0.10}

# ① 사전등록 경계: 0.35 → 정보 상한 / 0.05 → 용량 / 0.22 → 혼합
for gap, want in ((0.35, "정보 상한"), (0.05, "용량"), (0.22, "혼합")):
    p = report({"CO_core30_h5": arm(gap)})
    parsed = m.parse_insample_gap(p, 0.0)
    v, d, delta = m.judge_insample_gap(item, parsed)
    check(f"경계 갭 {gap} → {want}", want in v, v)
    check(f"경계 갭 {gap} delta={gap}", delta == gap, delta)

# ② train_auc 가 없으면 '판정불가'(0 갭을 정보 상한으로 오독하지 않는다)
p = report({"CO_core30_h5": {"n_folds": 0, "error": "train_auc 없음"}})
v, d, _ = m.judge_insample_gap(item, m.parse_insample_gap(p, 0.0))
check("train_auc 없음 → 판정불가", v == "판정불가", v)

# ③ 스코어보드 오독 방지: per_exp 를 만들지 않는다
p = report({"CO_core30_h5": arm(0.35)})
check("per_exp 미생성", "per_exp" not in m.parse_insample_gap(p, 0.0))

# ④ 낡은 요약 금지(mtime floor)
mt = os.path.getmtime(p)
check("낡은 요약 → 오류", "미갱신" in str(m.parse_insample_gap(p, mt + 60).get("error")))

# ⑤ 두 arm 을 모두 근거에 남긴다(용량 gradient)
p = report({"CO_core30_h5": arm(0.228), "CO_smooth_d1_h5": arm(0.0518, 0.587, 0.5352)})
v, d, _ = m.judge_insample_gap(item, m.parse_insample_gap(p, 0.0))
check("근거에 두 arm 모두", "CO_smooth_d1_h5" in d and "CO_core30_h5" in d, d)

print("\n" + ("전부 PASS" if not fails else f"{len(fails)}건 FAIL: {fails}"))
sys.exit(1 if fails else 0)

"""_wf_fold_auc_alias_test.py — CG261 metric('wf_fold_auc') 별칭 배선 검증.

왜(2026-11-14 02:0x): CG261 시작 로그에 "알 수 없는 metric 'wf_fold_auc' — 요약 경로 없음(판정불가로
기록)" 이 찍혔다. 별칭 배선 후 ① summary_path 가 --summary-out 을 따라가고 ② 같은 런 폴드 짝 Δ 로
arm 별 판정이 나오는지, ③ 두 arm 이 모두 문턱 미달이면 '노이즈'인지 확인한다.
"""
import importlib.util
import json
import os
import sys
import tempfile

PROJ = "/home/jhshi/analyist_dd"
spec = importlib.util.spec_from_file_location(
    "mec", os.path.join(PROJ, "scripts", "model_engineer_cycle.py"))
mec = importlib.util.module_from_spec(spec)
sys.modules["mec"] = mec
spec.loader.exec_module(mec)

ITEM = {
    "id": "CG261", "metric": "wf_fold_auc",
    "paired_reference": "MK_timevary_nomkt_h5",
    "arm_list": ["TV_all_nomkt_h5", "IC30_nomkt_h5"],
    "panel": "panel_prod200.npz",
    "command": ("docker exec stock_xgboost_ml sh -c 'cd /app && python -u scripts/wf_label_sweep.py "
                "--only TV_all_nomkt_h5,IC30_nomkt_h5,MK_timevary_nomkt_h5 "
                "--out /app/reports/overnight/cg261_selall.jsonl "
                "--summary-out /app/reports/overnight/cg261_selall_summary.json'"),
}

fails = []
checks = 0


def check(name, cond, note=""):
    global checks
    checks += 1
    print(("PASS " if cond else "FAIL ") + name + ((" — " + note) if note else ""))
    if not cond:
        fails.append(name)


# ① summary_path 별칭 — --summary-out 를 호스트 경로로 변환해 따라간다
sp = mec.summary_path("wf_fold_auc", ITEM["command"])
check("summary_path 가 --summary-out 을 따른다",
      sp.endswith("services/xgboost-ml/reports/overnight/cg261_selall_summary.json"), sp)

# ② 요약 스키마 합성: control 0.5300 고정, TV_all 폴드 +0.021(5/5 양), IC30 +0.004(2/5 양)
CTRL = [0.5300, 0.5300, 0.5300, 0.5300, 0.5300]
TV = [c + d for c, d in zip(CTRL, [0.0210, 0.0225, 0.0205, 0.0215, 0.0200])]
IC = [c + d for c, d in zip(CTRL, [0.0040, -0.0010, 0.0050, -0.0005, 0.0035])]


def mksummary(path, arms):
    res = []
    for exp, means in arms:
        res.append({"exp": exp, "desc": exp, "status": "ok",
                    "folds": {f"fold{i+1}": {"mean": v, "std": 0.002}
                              for i, v in enumerate(means)}})
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"finished_at": "2026-11-14T03:30:00+09:00",
                   "config": {"panel": "/app/app/models/wf/panel_prod200.npz"},
                   "results": res}, f)


tmp = tempfile.mkdtemp(prefix="cg261test_")
p = os.path.join(tmp, "cg261.json")
mksummary(p, [("MK_timevary_nomkt_h5", CTRL), ("TV_all_nomkt_h5", TV), ("IC30_nomkt_h5", IC)])

parsed = mec.parse_by_metric(ITEM, p, 0.0)
check("파서가 per_exp 를 만들지 않는다(스코어보드 오독 방지)", "per_exp" not in parsed,
      str(list(parsed)[:8]))
check("arms 3개 파싱", set(parsed.get("arms", {})) ==
      {"MK_timevary_nomkt_h5", "TV_all_nomkt_h5", "IC30_nomkt_h5"})
check("짝 Δ 가 arm 별로 만들어진다", set(parsed.get("pairs", {})) ==
      {"TV_all_nomkt_h5", "IC30_nomkt_h5"})
v, d, delta = mec.judge_by_metric(ITEM, parsed)
check("TV_all +0.021·5/5 양 → 신호있음", v == "신호있음", f"{v} {delta} | {d}")
check("판정 문자열에 두 arm 이 모두 실린다",
      "TV_all_nomkt_h5" in d and "IC30_nomkt_h5" in d, d[:200])
check("IC30(+0.0022)도 짝Δ 로 함께 보고", "IC30_nomkt_h5 0.5322" in d and d.count("짝Δ vs") == 2, d[:200])

# ③ 두 arm 모두 문턱 미달 → 노이즈
p2 = os.path.join(tmp, "cg261_noise.json")
mksummary(p2, [("MK_timevary_nomkt_h5", CTRL),
               ("TV_all_nomkt_h5", [c + 0.006 for c in CTRL]),
               ("IC30_nomkt_h5", [c - 0.003 for c in CTRL])])
parsed2 = mec.parse_by_metric(ITEM, p2, 0.0)
v2, d2, delta2 = mec.judge_by_metric(ITEM, parsed2)
check("두 arm 모두 문턱 미달 → 노이즈", v2 == "노이즈", f"{v2} {delta2}")

# ④ 낡은 요약 가드는 별칭에서도 살아 있다
parsed3 = mec.parse_by_metric(ITEM, p, os.path.getmtime(p) + 10)
check("요약 미갱신 가드 유지", "미갱신" in str(parsed3.get("error")), str(parsed3)[:120])

print("\n결과: %d/%d PASS" % (checks - len(fails), checks))
sys.exit(1 if fails else 0)

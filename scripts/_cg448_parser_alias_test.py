#!/usr/bin/env python3
"""robust_auc_multifold 별칭 + paired_reference 폴백 회귀 (2026-11-23 CG448 실측).

왜 필요한가: metric 이름이 파서 표에 없으면 rc=0 으로 완주한 측정이 '판정불가(parser 없음)'로
원장에서 사라진다(CG43·CG260·CG448 반복). 오늘 추가한 두 줄이 그 구멍을 닫았으므로,
① 별칭이 실제 파서로 라우팅되는지 ② counterfactual 태그 폴백이 기준 arm 을 찾는지
③ arm == counterfactual 이면 자기대조 Δ0 '노이즈'를 만들지 않는지 를 고정한다.
"""
import json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_engineer_cycle as m

FAIL = []


def check(name, cond, detail=""):
    if cond:
        print(f"PASS {name}")
    else:
        FAIL.append(name)
        print(f"FAIL {name} {detail}")


check("alias routes to baseline_protocol_gap",
      m.parse_by_metric({"metric": "robust_auc_multifold"}, "/nonexistent") != {"error": "parser 없음"},
      m.parse_by_metric({"metric": "robust_auc_multifold"}, "/nonexistent"))

real = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "services/xgboost-ml/reports/overnight/cg448_summary.json")
item = {"id": "CG448", "metric": "robust_auc_multifold",
        "arm": "CG376_noroll_h5",
        "counterfactual": "LS_quant_q30_h5 (같은 런·같은 패널)"}
if os.path.exists(real):
    parsed = m.parse_by_metric(item, real, 0.0)
    check("real summary parses", not parsed.get("error"), parsed.get("error"))
    check("ref from counterfactual tag", parsed.get("paired_reference") == "LS_quant_q30_h5",
          parsed.get("paired_reference"))
    pr = (parsed.get("pairs") or {}).get("CG376_noroll_h5") or {}
    check("paired delta arithmetic", abs((pr.get("mean") or 0) - 0.0006) < 0.002, pr)
    check("fold positives counted", (pr.get("pos"), pr.get("n")) == (2, 5), (pr.get("pos"), pr.get("n")))
    verdict, detail, delta = m._judge3(m.judge_by_metric(item, parsed))
    check("verdict is noise (not 판정불가)", verdict == "노이즈", f"{verdict} {detail[:80]}")
else:
    print(f"SKIP real summary (없음: {real})")

tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
json.dump({"results": [
    {"exp": "A", "folds": {"f1": {"mean": 0.52}, "f2": {"mean": 0.54}}},
    {"exp": "B", "folds": {"f1": {"mean": 0.51}, "f2": {"mean": 0.55}}}]}, tmp)
tmp.close()
os.utime(tmp.name, None)
same = {"metric": "robust_auc_multifold", "arm": "A", "counterfactual": "A (같은 런)"}
p2 = m.parse_by_metric(same, tmp.name, 0.0)
check("self-compare not used as reference", p2.get("paired_reference") == "", p2.get("paired_reference"))
check("no pair vs self", "A" not in (p2.get("pairs") or {}), p2.get("pairs"))
os.unlink(tmp.name)

print(f"\n{len(FAIL)} FAIL")
sys.exit(1 if FAIL else 0)

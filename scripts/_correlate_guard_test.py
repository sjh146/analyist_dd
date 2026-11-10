"""회귀: correlate() 가 b(다중폴드 AUC) 부재/부분 라벨에서 죽지 않아야 한다.

실측 배경(2026-11-10 CG183): OOS 거래일 12일 < 3*h=15 라 part_b 가 {} 를 돌려줬고,
correlate 가 KeyError 'cand' 로 죽어 **돈 지표(c) 증거까지 저장되지 않았다**(5분 측정 소실).
"""
import importlib.util
import sys

PATH = "/app/scripts/model_metric_protocol_audit.py"
spec = importlib.util.spec_from_file_location("_audit", PATH)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

A = {"champ": {"deployed_max": 0.55, "deployed_gt_0.55": 0, "deployed_median": 0.5},
     "cand": {"deployed_max": 0.54, "deployed_gt_0.55": 0, "deployed_median": 0.49}}
B = {"champ": {"robust_auc": 0.49, "auc_pooled": 0.487},
     "cand": {"robust_auc": 0.518, "auc_pooled": 0.507}}
C = {"champ": {"avg_pct": -0.174, "t_stat": -0.78},
     "cand": {"avg_pct": -0.135, "t_stat": -0.79}}
fails = []

r = m.correlate(A, {}, C)
if not (r.get("n") == 0 and "note" in r):
    fails.append(f"빈 b: n=0·note 기대, 실제 {r}")

r = m.correlate(A, {"champ": B["champ"]}, C)   # 부분 라벨
if not (r.get("n") == 0 and "note" in r):
    fails.append(f"부분 b: n=0·note 기대, 실제 {r}")

r = m.correlate(A, B, C)                        # 정상
if r.get("n") != 2 or r.get("labels") != ["cand", "champ"]:
    fails.append(f"정상: n=2 기대, 실제 {r}")

print("PASS" if not fails else "FAIL: " + " | ".join(fails))
sys.exit(0 if not fails else 1)

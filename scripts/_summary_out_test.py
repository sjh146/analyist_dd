"""summary_path 의 항목별 summary_out 지원 검증 (2026-11-10 CG186 실측 회귀 방지).

왜: CG186 은 command 가 래퍼(`sh scripts/cg186_retrain_audit.sh`)라 커맨드 문자열에 --out 이 없다.
그래서 구동기가 기본 경로(services/xgboost-ml/reports/model_metric_protocol_audit.json)를 봤고,
래퍼가 실제로 쓴 /app/reports/overnight/cg186_audit.json(mtime 23:45)은 아예 열리지 않아
"요약 미갱신 → 판정불가"로 원장에 남았다 — 측정은 있는데 판정이 사라진 유형.

실행: python3 scripts/_summary_out_test.py
"""
import importlib.util
import os
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "mec", os.path.join(PROJ, "scripts/model_engineer_cycle.py"))
mec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mec)

fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {got!r}")
    if not ok:
        print(f"         기대값: {want!r}")
        fails += 1


# [A] 항목이 summary_out 을 주면 그 경로(컨테이너→호스트 매핑)를 최우선으로 쓴다.
check("summary_out 우선(래퍼 항목)",
      mec.summary_path("robust_oos_audit", "docker exec stock_xgboost_ml sh -c 'cd /app && sh scripts/cg186_retrain_audit.sh'",
                       "/app/reports/overnight/cg186_audit.json"),
      os.path.join(PROJ, "services/xgboost-ml/reports/overnight/cg186_audit.json"))

# [B] summary_out 이 없으면 종전 동작(기본 경로) 그대로 — 회귀 없음.
check("summary_out 없음 → 기본 경로 유지",
      mec.summary_path("robust_oos_audit", "docker exec x sh -c 'python scripts/full_pipeline_dd.sh'", None),
      os.path.join(PROJ, "services/xgboost-ml/reports/model_metric_protocol_audit.json"))

# [C] 커맨드의 --out 이 여전히 summary_out 보다 뒤에 있지 않다(=명시 필드가 우선).
check("summary_out > 커맨드 --out",
      mec.summary_path("metric_audit_pair",
                       "docker exec x sh -c 'python scripts/model_metric_protocol_audit.py --out /app/reports/a.json'",
                       "/app/reports/overnight/b.json"),
      os.path.join(PROJ, "services/xgboost-ml/reports/overnight/b.json"))

# [D] 공백/따옴표 섞인 값도 정리된다.
check("따옴표 제거",
      mec.summary_path("wf_sweep_summary", "", "'/app/reports/overnight/c.json'"),
      os.path.join(PROJ, "services/xgboost-ml/reports/overnight/c.json"))

# [E] 실제 CG186 산출물이 이 경로 규칙으로 열리는가(파일 존재).
p = mec.summary_path("robust_oos_audit", "sh scripts/cg186_retrain_audit.sh",
                     "/app/reports/overnight/cg186_audit.json")
check("CG186 산출물 존재", os.path.exists(p), True)
if os.path.exists(p):
    parsed = mec.parse_by_metric({"metric": "robust_oos_audit", "model_label": "cand"}, p, 0.0)
    check("CG186 파싱(모델 선택 cand)", parsed.get("model"), "cand")
    check("CG186 폴드 수", len(parsed.get("fold_means") or []), 2)

print("전부 PASS" if not fails else f"실패 {fails}건")
sys.exit(0 if not fails else 1)

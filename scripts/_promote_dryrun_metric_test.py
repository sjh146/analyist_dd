#!/usr/bin/env python3
"""[테스트] 구동기의 승격 게이트 dry-run metric(`champion_promote_dryrun`) 배선 검증.

왜(실측 2026-09-30 22:34 CG43): 이 metric 이 등록돼 있지 않아 구동기 로그에
"경고: 알 수 없는 metric 'champion_promote_dryrun' — 요약 경로 없음(판정불가로 기록)" 이 찍혔고,
그대로면 rc=0 일 때 항목이 done 으로 닫히면서 **게이트 판정(status·사유·후보 AUC)이 통째로
사라진다**. 항목의 유일한 산출물이 그 판정이다.

검사 항목:
 1) summary_path 가 `--summary-out` 을 존중하고 컨테이너 경로(/app/... · 상대경로)를 호스트로 옮긴다
 2) 기본 경로(플래그 없음) · 알 수 없는 metric 은 빈 경로(회귀: 예외 금지)
 3) 파서가 status·사유·AUC·기준선을 뽑고 **per_exp 를 만들지 않는다**(scoreboard 오염 방지)
 4) 낡은 요약(mtime <= floor)은 거부한다(옛 결과 오독 방지)
 5) 판정 매핑: would_promote / kept_incumbent / invalid_candidate / status 없음
 6) rc=5(invalid_candidate)는 인프라 실패가 아니라 게이트 판정 → execute() 가 완료로 다룬다
    (코드 경로 검사: gate_rc5 분기가 존재하고 status 판정을 쓴다)

실행: python3 scripts/_promote_dryrun_metric_test.py
"""
import importlib.util
import json
import os
import tempfile
import time

MEC = "/home/jhshi/analyist_dd/scripts/model_engineer_cycle.py"
spec = importlib.util.spec_from_file_location("mec", MEC)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

PASS = FAIL = 0


def check(name, got, want):
    global PASS, FAIL
    ok = got == want
    PASS, FAIL = PASS + ok, FAIL + (not ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}\n      got ={got}\n      want={want}")


# ── 1) summary_path ────────────────────────────────────────────────────────────
CG43_CMD = ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 12600 "
            "python -u -m app.training.retrain_champion --days 90 --stock-limit 200 "
            "--out-dir app/models/champion_cand && python -m app.training.champion_promote "
            "--candidate app/models/champion_cand --dry-run "
            "--summary-out /app/app/reports/cg43_promote_dryrun.json'")
check("CG43 커맨드 → 컨테이너 절대경로 변환",
      m.summary_path("champion_promote_dryrun", CG43_CMD),
      os.path.join(m.PROJ, "services/xgboost-ml/app/reports/cg43_promote_dryrun.json"))
check("상대경로도 컨테이너 cwd(/app) 기준으로 변환",
      m.summary_path("champion_promote_dryrun", "... --summary-out app/reports/pd.json"),
      os.path.join(m.PROJ, "services/xgboost-ml/app/reports/pd.json"))
check("--summary-out= 형식",
      m.summary_path("champion_promote_dryrun", "x --summary-out=/app/reports/pd.json"),
      os.path.join(m.PROJ, "services/xgboost-ml/reports/pd.json"))
check("플래그 없음 → 기본 경로(회귀)",
      m.summary_path("champion_promote_dryrun"),
      os.path.join(m.PROJ, "services/xgboost-ml/reports/ml_result.json"))
check("알 수 없는 metric 은 빈 경로(회귀: 예외 금지)",
      m.summary_path("이런건없다", "x"), "")
check("--out 회귀(_out_arg 경유)",
      m.summary_path("champion_robust_eval", "x --out /app/reports/a.json"),
      os.path.join(m.PROJ, "services/xgboost-ml/reports/a.json"))

# ── 2) 파서 ────────────────────────────────────────────────────────────────────
tmpd = tempfile.mkdtemp(prefix="promote_dryrun_test_")
p = os.path.join(tmpd, "dryrun.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump({"auc": 0.5461, "candidate_metric": "auc", "promoted": False,
               "status": "kept_incumbent",
               "reason": "candidate auc 0.5461 < floor 0.53",
               "champion_auc_before": 0.5513, "champion_baseline": 0.5513,
               "champion_baseline_source": "robust_auc.json",
               "model_aucs": {"xgb": 0.5461}, "n_rows": 12600, "n_features": 48,
               "up_rate": 0.51, "decided_at": "2026-09-30T23:20:00"}, f)
pr = m.parse_champion_promote_dryrun(p, 0)
check("파서: status", pr.get("status"), "kept_incumbent")
check("파서: 후보 AUC 는 auc 키에서", pr.get("candidate_auc"), 0.5461)
check("파서: 챔피언 기준선", pr.get("champion_auc_before"), 0.5513)
check("파서: 학습행", pr.get("n_rows"), 12600)
check("파서: per_exp 를 만들지 않는다(scoreboard 오염 방지)", "per_exp" in pr, False)
check("파서: 낡은 요약 거부(mtime<=floor)",
      "error" in m.parse_champion_promote_dryrun(p, time.time() + 60), True)
check("파서: 파일 없음", "error" in m.parse_champion_promote_dryrun(p + ".nope", 0), True)
with open(p, "w", encoding="utf-8") as f:
    json.dump({"auc": 0.5}, f)          # status 키 없는 파일
check("파서: status 없음(게이트 판정 아님)",
      "error" in m.parse_champion_promote_dryrun(p, 0), True)

# ── 3) 판정 ────────────────────────────────────────────────────────────────────
item = {"id": "CG43", "metric": "champion_promote_dryrun",
        "baseline": "champion/robust_auc.json 0.5513"}
it = {"id": "CG43", "metric": "champion_promote_dryrun"}
v, d, delta = m.judge_promote_dryrun(it, {"status": "would_promote",
                                          "candidate_auc": 0.5602,
                                          "champion_auc_before": 0.5513,
                                          "champion_baseline": 0.5513,
                                          "n_rows": 12600,
                                          "reason": "dry-run: incumbent left untouched"})
check("판정: would_promote", v, "게이트 통과(승격후보 생성)")
check("판정: detail 에 후보 AUC·기준선·사유 포함",
      all(s in d for s in ("0.5602", "0.5513", "12600", "사유:")), True)
check("판정: 델타는 만들지 않는다(AUC 판정 아님)", delta, None)
v, d, _ = m.judge_promote_dryrun(it, {"status": "kept_incumbent",
                                      "candidate_auc": 0.5461,
                                      "reason": "candidate auc 0.5461 < floor 0.53"})
check("판정: kept_incumbent", v, "후보 생성·게이트 거부")
check("판정: 거부 사유가 detail 에 남는다", "floor 0.53" in d, True)
v, _, _ = m.judge_promote_dryrun(it, {"status": "invalid_candidate", "reason": "no model"})
check("판정: invalid_candidate", v, "후보 무효")
v, d, _ = m.judge_promote_dryrun(it, {"error": "요약 파일 없음"})
check("판정: 파서 오류 → 판정불가", v, "판정불가")
check("판정: 오류 문구 전달", d, "요약 파일 없음")

# ── 4) execute() 의 rc=5 완료 처리 분기가 살아있는지(소스 검사) ──────────────────
src = open(MEC, encoding="utf-8").read()
check("execute: gate_rc5 분기 존재", "gate_rc5 = not _p5.get(\"error\")" in src, True)
check("execute: 백로그 상태가 rc=0 또는 gate_rc5 를 완료로 본다",
      "if rc == 0 or gate_rc5:" in src, True)
check("execute: 판정 디스패치에 promote 포함",
      "elif item.get(\"metric\") == \"champion_promote_dryrun\":" in src, True)
check("ingest: promote 파서 배선", "else parse_champion_promote_dryrun(spath, 0)" in src, True)

print(f"\n{PASS}/{PASS + FAIL} PASS")
raise SystemExit(1 if FAIL else 0)

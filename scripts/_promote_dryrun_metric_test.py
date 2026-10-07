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
# ⚠ CG134(2026-10-07): 보고용 매핑 갭 수리 — 비-dry-run 경로의 라이브 스코어 게이트 차단은
# '판정불가'가 아니다(게이트가 작동해 승격을 막은 것이 정답).
v, d, _ = m.judge_promote_dryrun(it, {"status": "blocked_live_score",
                                      "reason": "라이브 스코어 게이트 미통과 — 승격 거부: 게이트 판정이 passed 가 아니다"})
check("판정: blocked_live_score", v, "게이트 통과·라이브 스코어 차단(미승격)")
check("판정: 라이브 차단 사유가 detail 에 남는다", "라이브 스코어 게이트 미통과" in d, True)
v, d, _ = m.judge_promote_dryrun(it, {"error": "요약 파일 없음"})
check("판정: 파서 오류 → 판정불가", v, "판정불가")
check("판정: 오류 문구 전달", d, "요약 파일 없음")

# ── 4) execute() 의 rc=5 완료 처리 분기가 살아있는지(소스 검사) ──────────────────
src = open(MEC, encoding="utf-8").read()
check("execute: gate_rc5 분기 존재", "gate_rc5 = not _p5.get(\"error\")" in src, True)
check("execute: 백로그 상태가 rc=0 또는 gate_rc5 를 완료로 본다",
      "if rc == 0 or gate_rc5:" in src, True)
check("execute: parse_by_metric 디스패치 배선",
      "parsed = parse_by_metric(item, spath, mtime_floor)" in src, True)
check("execute: judge_by_metric 디스패치 배선",
      "verdict, detail, delta = judge_by_metric(item, parsed, per)" in src, True)
check("ingest: parse_by_metric 배선", "parsed = parse_by_metric(it, spath, 0.0)" in src, True)
check("tick: rejudge_parser_gap 배선", "for iid, verdict, detail in rejudge_parser_gap():" in src, True)

# ── 5) rejudge_parser_gap: 이미 돌고 있던 실행의 '판정불가' 기록을 교정 ──────────
#    (실측 2026-09-30 CG43 — 착수 시점 모듈에 파서가 없어 기록이 'parser 없음'으로 남는다)
work = tempfile.mkdtemp(prefix="rejudge_test_")
pd = os.path.join(work, "pd.json")
with open(pd, "w", encoding="utf-8") as f:
    json.dump({"auc": 0.5461, "status": "kept_incumbent",
               "reason": "candidate auc 0.5461 < floor 0.53",
               "champion_auc_before": 0.5513, "champion_baseline": 0.5513,
               "n_rows": 12600}, f)
m.BACKLOG = os.path.join(work, "backlog.json")
m.LEDGER = os.path.join(work, "ledger.jsonl")
with open(m.BACKLOG, "w", encoding="utf-8") as f:
    json.dump({"items": [
        {"id": "CG99", "status": "done", "metric": "champion_promote_dryrun",
         "command": f"docker exec x python -m app.training.champion_promote --dry-run "
                    f"--summary-out {pd}",
         "result": {"verdict": "판정불가", "detail": "parser 없음", "rc": 0}},
        {"id": "CG98", "status": "failed", "metric": "wf_sweep_summary", "command": "x"},
    ]}, f)
with open(m.LEDGER, "w", encoding="utf-8") as f:
    for r in [{"id": "CG99", "rc": 0, "metric": "champion_promote_dryrun",
               "parsed": {"error": "parser 없음 (metric='champion_promote_dryrun')"},
               "verdict": "판정불가", "detail": "", "reported": False},
              {"id": "CG98", "rc": 1, "metric": "wf_sweep_summary",
               "parsed": {"error": "실행 실패 — 측정값 없음"}, "verdict": "실행실패",
               "reported": True}]:
        f.write(json.dumps(r) + "\n")
fixed = m.rejudge_parser_gap()
check("rejudge: 교정 대상 1건", len(fixed), 1)
led = m.load_ledger()
check("rejudge: 판정 교체", led[0]["verdict"], "후보 생성·게이트 거부")
check("rejudge: 근거(사유·AUC)가 detail 에 들어간다",
      all(s in led[0]["detail"] for s in ("0.5461", "floor 0.53")), True)
check("rejudge: per_exp 를 만들지 않는다", "per_exp" in (led[0]["parsed"] or {}), False)
check("rejudge: reported 플래그 유지 → 다음 틱이 교정 결과를 보고", led[0].get("reported"), False)
check("rejudge: 교정 흔적을 남긴다", "rejudged" in led[0], True)
check("rejudge: 실행실패 기록은 손대지 않는다", led[1]["verdict"], "실행실패")
check("rejudge: 백로그 result 갱신",
      m.load_backlog()["items"][0]["result"]["verdict"], "후보 생성·게이트 거부")
check("rejudge: 멱등(2회차 무변경)", m.rejudge_parser_gap(), [])

# ── 6) parse_by_metric / judge_by_metric 디스패치 ─────────────────────────────
check("디스패치: 모르는 metric → parser 없음 오류",
      "parser 없음" in m.parse_by_metric({"metric": "없음"}, "/tmp/x", 0).get("error", ""), True)
check("디스패치: promote metric 파서 선택",
      m.parse_by_metric({"metric": "champion_promote_dryrun"}, pd, 0).get("status"),
      "kept_incumbent")
v, d, delta = m.judge_by_metric({"id": "CG99", "metric": "champion_promote_dryrun"},
                                m.parse_champion_promote_dryrun(pd, 0))
check("디스패치: promote 판정 선택", v, "후보 생성·게이트 거부")
v, d, delta = m.judge_by_metric({"metric": "wf_sweep_summary", "arm": "A",
                                 "counterfactual": "B"},
                                {"per_exp": {"A": {"mean": 0.55}, "B": {"mean": 0.52}}})
check("디스패치: arm 실험은 judge_per 경로", v, "신호있음")

print(f"\n{PASS}/{PASS + FAIL} PASS")
raise SystemExit(1 if FAIL else 0)

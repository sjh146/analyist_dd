#!/usr/bin/env python3
"""검증: champion_robust_eval 파서·판정·ingest (2026-09-29, CG31 셋업).

왜 필요한가: CG31 은 **승격 기준선 숫자**를 만드는 항목이다. 파서가 틀리면 기준선이 틀어져
승격 판정이 통째로 어긋난다. 합성 요약 JSON + 임시 PROJ(ME_PROJ) 로 검증한다:
① summary_path 매핑 ② per_exp 변환(창→폴드 키 정합) ③ mtime floor 거부(낡은 요약 차단)
④ 판정 문자열('기준선 실측' + 숫자 포함) ⑤ ingest 가 원장·백로그를 실제로 갱신하는지(e2e)
⑥ 기존 wf_sweep 경로 회귀 없음.
"""
import json
import os
import sys
import tempfile

tmp = tempfile.mkdtemp(prefix="me_cg31_")
os.environ["ME_PROJ"] = tmp
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_engineer_cycle as m  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f" — {extra}" if extra else ""))
    if not cond:
        fails.append(name)


rep = os.path.join(tmp, "services/xgboost-ml/reports")
os.makedirs(rep, exist_ok=True)
p = os.path.join(rep, "champion_robust_eval.json")
payload = {
    "model_dir": "app/models/champion",
    "protocol": "5-fold 연속 시간창, h=5 시장상대 중앙값 라벨, 크로스섹션 AUC, purge=5거래일",
    "metric": "cross_sectional_auc_mean",
    "robust_auc": 0.5405,
    "auc_std_across_folds": 0.0212,
    "folds": [
        {"fold": 1, "window": ["2025-11-28", "2026-01-20"], "n_dates": 10, "auc_mean": 0.5399, "auc_std": 0.02},
        {"fold": 2, "window": ["2026-01-28", "2026-03-23"], "n_dates": 10, "auc_mean": 0.4763, "auc_std": 0.03},
        {"fold": 3, "window": ["2026-03-31", "2026-05-22"], "n_dates": 10, "auc_mean": 0.5591, "auc_std": 0.04},
        {"fold": 4, "window": ["2026-06-01", "2026-07-21"], "n_dates": 10, "auc_mean": 0.5772, "auc_std": 0.05},
        {"fold": 5, "window": ["2026-07-29", "2026-09-16"], "n_dates": 10, "auc_mean": 0.5500, "auc_std": 0.06},
    ],
    "dates_scored": ["2025-11-28", "2026-09-16"],
    "rows_scored": 4000,
    "auc_pooled": 0.5610,
    "auc_per_date_mean": 0.5401,
    "model_aucs": None,
    "errors": [],
    "measured_at": "2026-09-29T17:35:00",
}
with open(p, "w", encoding="utf-8") as f:
    json.dump(payload, f)

check("summary_path 매핑(champion_robust_eval)",
      m.summary_path("champion_robust_eval").endswith("services/xgboost-ml/reports/champion_robust_eval.json"))
check("기존 wf_sweep 매핑 회귀 없음",
      m.summary_path("wf_sweep_summary").endswith("reports/overnight/wf_label_sweep_summary.json"))
try:
    m.summary_path("nope")
    check("알 수 없는 metric → ValueError", False)
except ValueError:
    check("알 수 없는 metric → ValueError", True)

parsed = m.parse_champion_robust(p, 0)
check("robust_auc 파싱", parsed.get("robust_auc") == 0.5405, str(parsed.get("robust_auc")))
check("파서 오류 없음", not parsed.get("error"), str(parsed.get("error")))
check("창 5개 → WINDOW1..5", sorted(parsed["per_exp"]) == [f"WINDOW{i}" for i in range(1, 6)],
      str(sorted(parsed["per_exp"])))
check("창 폴드 값·std 유지",
      parsed["per_exp"]["WINDOW1"]["mean"] == 0.5399 and parsed["per_exp"]["WINDOW1"]["std"] == 0.02)
check("창 승률(0.5 초과 개수) 4/5",
      sum(v["fold_win_rate"] for v in parsed["per_exp"].values()) == 4.0)
check("풀링·날짜별평균 전달", parsed.get("auc_pooled") == 0.5610 and parsed.get("auc_per_date_mean") == 0.5401)

stale = m.parse_champion_robust(p, os.path.getmtime(p))
check("mtime floor 거부(낡은 요약 차단)", "미갱신" in (stale.get("error") or ""), str(stale.get("error")))

empty = os.path.join(rep, "empty.json")
with open(empty, "w", encoding="utf-8") as f:
    json.dump({"folds": []}, f)
check("folds 비면 오류", "비어" in (m.parse_champion_robust(empty, 0).get("error") or ""))

item = {"id": "CG31", "title": "champion baseline", "metric": "champion_robust_eval",
        "baseline": {"value": 0.5513, "source": "champion/robust_auc.json"}}
v, d, delta = m.judge_champion_baseline(item, parsed)
check("판정=기준선 실측", v == "기준선 실측", v)
check("판정에 실측·기준선 숫자 포함", "0.5405" in d and "0.5513" in d and "직접비교 금지" in d, d)
check("판정 delta 미설정(개선 판정 아님)", delta is None)

# ── ingest e2e (임시 백로그·원장) ────────────────────────────────────────────
os.makedirs(os.path.dirname(m.BACKLOG), exist_ok=True)
with open(m.BACKLOG, "w", encoding="utf-8") as f:
    json.dump({"updated_at": "t", "items": [dict(item, status="needs_setup", command="true")]}, f)
rc = m.ingest("CG31", log_rel="data/reports/me_cycle/champion_robust_eval.log")
with open(m.LEDGER, encoding="utf-8") as f:
    led = [json.loads(ln) for ln in f if ln.strip()]
with open(m.BACKLOG, encoding="utf-8") as f:
    b = json.load(f)
check("ingest rc=0", rc == 0, str(rc))
check("원장 1건 · rc=0 · 미보고", len(led) == 1 and led[0]["rc"] == 0 and led[0]["reported"] is False)
check("백로그 status=done · result 기록",
      b["items"][0]["status"] == "done" and b["items"][0]["result"]["verdict"] == "기준선 실측")
check("ingest 표시(out_of_band·source)",
      led[0]["parsed"].get("out_of_band") is True and led[0]["parsed"].get("source", "").endswith(".json"))
check("없는 id 는 rc=2", m.ingest("NOPE") == 2)

print("결과:", "ALL PASS" if not fails else f"FAIL {len(fails)}: {fails}")
sys.exit(1 if fails else 0)

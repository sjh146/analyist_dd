#!/usr/bin/env python3
"""_topk_metric_test.py — metric `topk_precision` 파서·판정기 자체점검 (pytest 없음).

왜: 새 metric 은 **착수 전에** 파서·판정·summary_path 를 배선해야 한다(2026-09-30 CG43 교훈 —
실행 중 프로세스는 옛 모듈을 들고 돌아 "parser 없음 → 판정불가" 로 rc=0 done 이 되면서
항목의 유일한 산출물이 원장에서 사라진다).

검사: ① 신호(k=3·5 둘 다 Δ≥+0.05·p<0.05) ② k=5 미달 → 노이즈 ③ 대조군 없음 → 판정불가
④ 요약 미갱신(mtime_floor) ⑤ 파일 없음 ⑥ k=3 포화 → 노이즈 ⑦ summary_path --json-out 매핑
⑧ per_exp 를 만들지 않는다(kstats 로만) — scoreboard 오독 방지.

실행: python3 scripts/_topk_metric_test.py
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_engineer_cycle as m  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(f"{name} — {detail}")


def write(payload, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    return path


def payload(k3_dm=0.08, k3_p=2.7e-5, k5_dm=0.06, k5_p=0.01, control="champ", sat=None):
    return {
        "preds": "x.jsonl", "rows": 100, "exps": ["cand", "champ"],
        "arm": "cand", "control": control, "restrict_q": 0.05, "min_pool": 20, "ks": [3, 5, 10],
        "saturated_cells": sat or {"3": 0, "5": 0, "10": 900},
        "skip_stats": {"saturated_k10": 900},
        "per_exp": {"cand": {"3": {"n_dates": 200, "prec_mean": 0.59, "ret_mean": 0.16}},
                    "champ": {"3": {"n_dates": 200, "prec_mean": 0.51, "ret_mean": 0.09}}},
        "paired": {"3": {"n_dates": 200, "n_eff_sign": 128, "ties": 72,
                         "prec_delta_mean": k3_dm, "prec_delta_pos": 88, "prec_sign_p": k3_p,
                         "ret_delta_mean": 0.069, "folds": {"1": "9/45"}},
                   "5": {"n_dates": 200, "n_eff_sign": 135, "ties": 65,
                         "prec_delta_mean": k5_dm, "prec_delta_pos": 75, "prec_sign_p": k5_p,
                         "ret_delta_mean": 0.025, "folds": {"1": "10/45"}}},
    }


tmp = tempfile.mkdtemp(prefix="topk_metric_")
item = {"id": "CG54", "metric": "topk_precision",
        "command": "docker exec stock_xgboost_ml sh -c 'cd /app && python scripts/topk_precision.py "
                   "x.jsonl --arm cand --control champ --json-out /app/reports/cg54_topk.json'"}

# ① 신호
p1 = write(payload(), os.path.join(tmp, "ok.json"))
parsed = m.parse_by_metric(item, p1, 0.0)
v, d, delta = m.judge_by_metric(item, parsed)
check("① 신호 판정", v == "신호있음", f"{v} :: {d[:90]}")
check("① kstats 로 실린다", isinstance(parsed.get("kstats"), dict) and "cand" in parsed["kstats"])
check("① per_exp 를 만들지 않는다", "per_exp" not in parsed, str(list(parsed)[:6]))

# ② k=5 미달
p2 = write(payload(k5_dm=0.018, k5_p=0.228), os.path.join(tmp, "k5_miss.json"))
v2 = m.judge_by_metric(item, m.parse_by_metric(item, p2, 0.0))[0]
check("② k=5 미달 → 노이즈", v2 == "노이즈", v2)

# ③ 대조군 없음
p3 = write(payload(control=None), os.path.join(tmp, "no_ctl.json"))
v3, d3, _ = m.judge_by_metric(item, m.parse_by_metric(item, p3, 0.0))
check("③ 대조군 없음 → 판정불가", v3 == "판정불가" and "대조군" in d3, f"{v3} :: {d3[:70]}")

# ④ 요약 미갱신(floor)
time.sleep(0.02)
p4 = write(payload(), os.path.join(tmp, "stale.json"))
parsed4 = m.parse_topk_precision(p4, time.time() + 10)
v4 = m.judge_by_metric(item, parsed4)[0]
check("④ mtime_floor 초과 → 판정불가", parsed4.get("error") and v4 == "판정불가",
      str(parsed4.get("error"))[:60])

# ⑤ 파일 없음
v5 = m.judge_by_metric(item, m.parse_topk_precision(os.path.join(tmp, "nope.json"), 0.0))[0]
check("⑤ 파일 없음 → 판정불가", v5 == "판정불가", v5)

# ⑥ k=3 포화 → 노이즈 (포화 셀이 있으면 그 k 는 판정 제외)
p6 = write(payload(sat={"3": 12, "5": 0, "10": 900}), os.path.join(tmp, "sat.json"))
v6, d6, _ = m.judge_by_metric(item, m.parse_by_metric(item, p6, 0.0))
check("⑥ 포화 k → 노이즈", v6 == "노이즈" and "포화" in d6, f"{v6} :: {d6[-60:]}")

# ⑦ summary_path 매핑(--json-out → 호스트 경로)
sp = m.summary_path("topk_precision", item["command"])
check("⑦ summary_path --json-out 매핑", sp.endswith("cg54_topk.json") and sp.startswith("/"),
      sp)
sp2 = m.summary_path("topk_precision", "docker exec x sh -c 'cd /app && python p.py y.jsonl'")
check("⑦ --json-out 없으면 빈 경로", sp2 == "", repr(sp2))

# ⑧ 기존 metric 회귀 — 등록된 파서들이 그대로 살아 있는가
check("⑧ champion_seed_family 파서 유지",
      m.parse_by_metric({"metric": "champion_seed_family"}, "/nonexistent.json").get("error")
      == "요약 파일 없음")
check("⑧ 알 수 없는 metric 은 예외 없이 오류",
      "parser 없음" in m.parse_by_metric({"metric": "nope_metric"}, "/x.json").get("error", ""))

print("\n".join(f"PASS {x}" for x in PASS))
if FAIL:
    print("\n".join(f"FAIL {x}" for x in FAIL))
print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} PASS")
sys.exit(1 if FAIL else 0)

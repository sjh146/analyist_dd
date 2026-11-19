#!/usr/bin/env python3
"""CG369 metric(reproducibility_overlap) 배선 회귀 — 경로/파서/판정기 3단 검증.

왜: 배선 누락 시 rc=0 완주가 '판정불가(parser 없음)'로 원장에 남는다(측정 소실).
실행: python3 scripts/_repro_overlap_wiring_test.py
"""
import importlib.util
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("mec", os.path.join(HERE, "model_engineer_cycle.py"))
m = importlib.util.module_from_spec(spec)
sys.modules["mec"] = m
spec.loader.exec_module(m)

ITEM = {
    "id": "CG369",
    "metric": "reproducibility_overlap",
    "command": (
        "docker exec -e UNIVERSE_ASOF_DATE=2026-10-01 stock_xgboost_ml sh -c 'cd /app && "
        "python -u scripts/champion_robust_eval.py --out /app/reports/overnight/cg369_frozen_a.json && "
        "python -u scripts/_cg369_repro_check.py --a a.jsonl --b b.jsonl "
        "--json reports/overnight/cg369_repro_frozen.json'"
    ),
}
fails = []


def check(name, cond, got=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{got}]" if got else ""))
    if not cond:
        fails.append(name)


# 1) 경로: --json 인자 → 호스트 경로
p = m.summary_path("reproducibility_overlap", ITEM["command"])
check("summary_path 가 --json 을 호스트 경로로 옮긴다",
      p.endswith("services/xgboost-ml/reports/overnight/cg369_repro_frozen.json"), p)
check("summary_path 가 알 수 없는 metric 경고를 더 내지 않는다",
      m.summary_path("reproducibility_overlap", ITEM["command"], quiet=True) == p)

# 2) 파서: 재현됨/재현 안 됨 두 스키마
good = {"n_dates_a": 6, "n_dates_b": 6, "n_dates_common": 6, "overlap_frac_mean": 1.0,
        "pred_max_abs_delta": 0.0, "auc_common_delta_mean": 0.0, "auc_common_delta_sd": 0.0,
        "auc_only_a_mean": None, "auc_only_b_mean": None, "verdict": "재현됨"}
bad = dict(good, overlap_frac_mean=0.35, pred_max_abs_delta=0.066,
           auc_common_delta_mean=-0.0319, verdict="재현 안 됨")
tmp = tempfile.mkdtemp()
for tag, payload, expect in (("good", good, "재현됨"), ("bad", bad, "재현 안 됨")):
    fp = os.path.join(tmp, f"{tag}.json")
    with open(fp, "w") as fh:
        json.dump(payload, fh)
    parsed = m.parse_by_metric(ITEM, fp, 0.0)
    check(f"parse({'good' if tag=='good' else 'bad'}) 오류 없음", not parsed.get("error"),
          str(parsed.get("error")))
    v, d, delta = m._judge3(m.judge_by_metric(ITEM, parsed))
    check(f"judge({tag}) → '{expect}'", v.startswith(expect), f"{v} | {d[:90]}")
    check(f"judge({tag}) delta=None(계측기, 무개선 카운터 무관)", delta is None, str(delta))

# 3) 갭 감지: 파일 없음 → 판정불가 + '요약 파일 없음'
missing = m.parse_by_metric(ITEM, os.path.join(tmp, "nope.json"), 0.0)
v, d, _ = m._judge3(m.judge_by_metric(ITEM, missing))
check("파일 없음 → 판정불가", v == "판정불가", f"{v} | {d}")

# 4) 재판정 트리거가 잡는 문구인지(rejudge_parser_gap 은 'parser 없음' 만 본다)
old = {"error": "parser 없음 (metric='reproducibility_overlap')"}
check("옛 기록 문구가 재판정 대상 조건과 일치", "parser 없음" in old["error"])

print(f"\n{len(fails)} FAIL / {len(fails) == 0 and 'ALL PASS' or fails}")
sys.exit(1 if fails else 0)

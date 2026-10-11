#!/usr/bin/env python3
"""자체점검: 구동기 metric kind `scale_bisect_probe` 배선 (CG175).

검사 항목
  1) summary_path 가 커맨드의 `--out` 을 호스트 경로로 옮긴다.
  2) 파서가 실 산출물을 읽어 champion_delta·cg166_delta·n_sample_rows 를 낸다.
  3) 파서가 `per_exp` 를 만들지 않는다(scoreboard 가 확률 스케일을 arm 폴드 AUC 로 오독 방지).
  4) 판정: |Δ|≥0.02 → '미재현', |Δ|<0.02 → '재현됨'.
  5) 낡은 요약 가드: mtime_floor 가 미래면 '미갱신' 오류로 거른다.
  6) 미등록 metric 은 여전히 'parser 없음'(회귀 없음).

실행(호스트): python3 scripts/_scale_bisect_metric_test.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_engineer_cycle as m  # noqa: E402

fails = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        fails.append(msg)


CMD = ("docker exec stock_xgboost_ml sh -c 'cd /app && python -u "
       "scripts/_cg163_scale_bisect.py --max-per-day 30 "
       "--out /app/app/reports/cg175_scale_bisect.json'")

# 1) 경로 해석
sp = m.summary_path("scale_bisect_probe", CMD)
check(sp.endswith("services/xgboost-ml/app/reports/cg175_scale_bisect.json"),
      f"summary_path --out → 호스트 경로 ({sp})")

item = {"metric": "scale_bisect_probe", "command": CMD, "conf_ts": 0.55}

# 2) 실 산출물 파싱 (없으면 합성)
real = sp if os.path.exists(sp) else None
if real:
    parsed = m.parse_by_metric(item, real, 0.0)
    check(not parsed.get("error"), f"실 산출물 파싱 무오류 ({parsed.get('error')})")
    check(parsed.get("metric_name") == "scale_bisect_probe", "metric_name = scale_bisect_probe")
    check(parsed.get("champion_delta") is not None, f"champion_delta 존재 ({parsed.get('champion_delta')})")
    check("per_exp" not in parsed, "per_exp 미생성(scoreboard 오독 방지)")
    v, d, _ = m.judge_by_metric(item, parsed)
    check(v in ("재현됨", "미재현"), f"실 산출물 판정 = {v}")
else:
    print("SKIP 실 산출물 없음 — 합성 케이스만 검사")

# 3~4) 합성: 큰 Δ → 미재현 / 작은 Δ → 재현됨
def synth(delta: float) -> str:
    d = {"probe": "cg175_scale_bisect", "dates": ["2026-10-08"], "verdict": "합성",
         "summary": {"published_sample": {"n": 10, "ge_055_pct": 0.0},
                     "rescored": {"champion": {"n": 10, "ge_055_pct": 19.0}},
                     "paired_delta": {"champion": {"n": 10, "mean_rescored_minus_published": delta}}},
         "n_sample_rows": 10, "n_failures": 0}
    fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(d, fh, ensure_ascii=False)
    fh.close()
    return fh.name

p_big = m.parse_by_metric(item, synth(0.332), 0.0)
check("per_exp" not in p_big, "합성(큰 Δ) per_exp 미생성")
check(m.judge_by_metric(item, p_big)[0] == "미재현", "합성 큰 Δ(0.332) → 미재현")
p_small = m.parse_by_metric(item, synth(0.005), 0.0)
check(m.judge_by_metric(item, p_small)[0] == "재현됨", "합성 작은 Δ(0.005) → 재현됨")

# 5) 낡은 요약 가드
guard = m.parse_by_metric(item, synth(0.1), 9.9e12)
check(bool(guard.get("error")) and "미갱신" in guard["error"], "mtime_floor 미래 → 미갱신 거부")

# 6) 회귀: 미등록 metric 은 여전히 parser 없음
unk = m.parse_by_metric({"metric": "nope_kind_xyz", "command": "x"}, "", 0.0)
check("parser 없음" in str(unk.get("error")), "미등록 metric → parser 없음(회귀 없음)")

print(f"\n{'ALL PASS' if not fails else str(len(fails)) + ' FAIL'}")
sys.exit(1 if fails else 0)

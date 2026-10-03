#!/usr/bin/env python3
"""[테스트] calibration_probe metric 배선 검증 (2026-10-03 CG83).

왜: 새 metric 을 백로그에 등록하면서 **파서·판정·경로 배선을 같은 커밋에서** 끝내지 않으면,
구동기가 "parser 없음 → 판정불가" 로 기록하고 rc=0 이라 항목이 done 으로 닫히면서 그 항목의
유일한 산출물(Brier·ECE 이득)이 원장에서 통째로 사라진다(CG43/CG10 함정).

실행: python3 scripts/_calibration_metric_test.py   (호스트)
"""
import importlib.util
import json
import os
import sys
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
    print(f"{'PASS' if ok else 'FAIL'}  {name}\n      got ={got!r}\n      want={want!r}")


# ── 1) summary_path: --json-out 를 존중해야 한다(기본 산출물 보존) ──────────────
CMD = ("docker exec stock_xgboost_ml sh -c 'cd /app && python -u scripts/calibration_probe.py "
       "/app/reports/overnight/cg81_preds.jsonl --json-out /app/reports/overnight/cg83_calibration.json'")
check("--json-out 컨테이너 경로 → 호스트 경로",
      m.summary_path("calibration_probe", CMD),
      os.path.join(m.PROJ, "services/xgboost-ml/reports/overnight/cg83_calibration.json"))
check("--json-out 없으면 빈 경로(판정불가 — 크래시 금지)",
      m.summary_path("calibration_probe", "python scripts/calibration_probe.py x.jsonl"), "")

# ── 2) 파서 라우팅: 'parser 없음' 이 아니어야 한다 ───────────────────────────
_p = m.parse_by_metric({"metric": "calibration_probe"}, "/nonexistent/x.json", 0.0)
check("파서 라우팅(parser 없음 아님)", "parser 없음" in str(_p.get("error")), False)
check("없으면 '요약 파일 없음'", _p.get("error"), "요약 파일 없음")

# ── 3) 파싱·판정: 실측 스키마로 '보정 유효' ─────────────────────────────────
SAMPLE = {
    "generated_at": "2026-10-03T13:00:00+00:00",
    "n_rows": 26685, "n_dates": 225, "base_rate": 0.5, "topk": 10,
    "best_method": "platt", "brier_gain": 0.0058, "ece_gain": 0.0395, "auc_delta": -0.0005,
    "raw": {"auc": 0.5305, "brier": 0.2552, "logloss": 0.7053, "ece": 0.0534,
            "max_overconf": 0.1783, "p50": 0.5316, "p90": 0.6594, "max": 0.9355,
            "thresholds": [{"threshold": 0.55, "n": 9871}],
            "topk": {"k": 10, "median": 0.6319}},
    "calibrated": {"platt": {"auc": 0.53, "brier": 0.2494, "logloss": 0.6919, "ece": 0.0139,
                             "max_overconf": 0.0252, "p90": 0.5378, "max": 0.6364,
                             "thresholds": [{"threshold": 0.55, "n": 1646}],
                             "topk": {"k": 10, "median": 0.526}}},
}
fd, path = tempfile.mkstemp(suffix=".json")
with os.fdopen(fd, "w") as f:
    json.dump(SAMPLE, f)
try:
    parsed = m.parse_calibration_probe(path, 0.0)
    check("파싱: brier_gain", parsed.get("brier_gain"), 0.0058)
    check("파싱: calibration 이 있다", isinstance(parsed.get("calibration"), dict), True)
    check("⚠ per_exp 를 만들지 않는다(스코어보드 오독 방지)", "per_exp" in parsed, False)
    check("파싱: cal_topk 번역", parsed["cal_topk"]["platt"]["median"], 0.526)
    v, d, delta = m.judge_calibration_probe({"metric": "calibration_probe"}, parsed)
    check("판정: 보정 유효", v, "보정 유효")
    check("판정 delta = brier_gain", delta, 0.0058)
    check("판정 detail 에 승격 아님 명시", "승격 아님" in d, True)

    # 문턱 미달 → 보정 무효
    SAMPLE2 = json.loads(json.dumps(SAMPLE))
    SAMPLE2["brier_gain"] = 0.0005
    path2 = path + ".b"
    with open(path2, "w") as f:
        json.dump(SAMPLE2, f)
    v2, _, _ = m.judge_calibration_probe({"metric": "calibration_probe"},
                                         m.parse_calibration_probe(path2, 0.0))
    check("판정: 이득 미달 → 보정 무효", v2, "보정 무효")

    # 요약 미갱신(floor == mtime) → error
    mt = os.path.getmtime(path)
    pe = m.parse_calibration_probe(path, mt)
    check("요약 미갱신 감지(mt <= floor)", "미갱신" in str(pe.get("error")), True)
    check("미갱신 → 판정불가", m.judge_calibration_probe({}, pe)[0], "판정불가")
    os.remove(path2)
finally:
    os.remove(path)

# ── 4) 백로그 정합: calibration_probe 항목은 command 에 --json-out 을 가져야 한다 ──
b = json.load(open(os.path.join(m.PROJ, "docs/QUANT_MODEL_BACKLOG.json")))
n = 0
for it in b["items"]:
    if it.get("metric") == "calibration_probe" and it.get("status") in (
            "pending", "backlog", "needs_setup"):
        cmd = it.get("command")
        if not cmd:
            print(f"NOTE: {it['id']} command 미정 — 착수 시 --json-out 필수")
            continue
        n += 1
        check(f"{it['id']} 에 --json-out 있음", "--json-out" in cmd, True)
        check(f"{it['id']} 판정대상 아님(AUC 승격 아님)", it.get("affects_model", False), False)
if n == 0:
    print("NOTE: 미완료 calibration_probe 항목이 없어 백로그 경로 검사를 건너뜀")

print(f"\n{PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)

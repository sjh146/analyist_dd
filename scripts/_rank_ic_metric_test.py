#!/usr/bin/env python3
"""_rank_ic_metric_test.py — CG113 랭크 IC metric 배선 자체점검 (pytest 없음 — 순수 파이썬).

검사 대상
  ① summary_path('rank_ic_money', command) 가 커맨드의 --json-out 을 호스트 경로로 변환한다
     (없으면 기본 경로, 그래도 없으면 빈 문자열 → '판정불가' 로 정직하게 끝난다).
  ② parse_rank_ic_money: 오류 3종(경로 없음·파일 없음·mtime 미갱신) + 스키마 필수키 + **per_exp 미생성**.
  ③ judge_rank_ic_money: 신호있음 / t 미달 / 부호 불일치(앞뒤 절반) / 양세션 미달 / 판정불가.
  ④ e2e: 합성 덤프(양(+) IC 60세션) → 도구 실행 → 파서·판정기 통과(스키마 정합).

실행(호스트): python3 scripts/_rank_ic_metric_test.py
"""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))
import model_engineer_cycle as m   # noqa: E402

fails = []
n_ok = 0


def check(name, cond, info=""):
    global n_ok
    if cond:
        n_ok += 1
        print("  [PASS] %s %s" % (name, info))
    else:
        fails.append(name)
        print("  [FAIL] %s %s" % (name, info))


def _write(path, payload):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def _mk_ic(mean=0.05, t=4.0, pos=0.65, h1=0.04, h2=0.06, n=80, implied=1.2):
    return {"n_sessions": n, "mean_ic": mean, "sd_ic": 0.05, "t": t,
            "pos_session_share": pos, "first_half_ic": h1, "second_half_ic": h2,
            "implied_edge_pct": implied}


def main() -> int:
    print("[1] summary_path")
    cmd = ("docker exec stock_xgboost_ml sh -c 'cd /app && python scripts/rank_ic_money.py "
           "--arm-jsonl /app/reports/overnight/x.jsonl --json-out /app/reports/overnight/cg113_rank_ic.json'")
    p = m.summary_path("rank_ic_money", cmd)
    check("summary_path_from_json_out",
          p.endswith("/services/xgboost-ml/reports/overnight/cg113_rank_ic.json"), p)
    p2 = m.summary_path("rank_ic_money", "python scripts/rank_ic_money.py")
    # 계약: --json-out 이 없으면 도구는 파일을 쓰지 않는다(stdout 만) → 요약 없음(빈 경로).
    #   fillable_topk_expectancy 와 같은 규칙이며, '판정불가'로 정직하게 끝나게 한다.
    #   (기본 경로를 돌려주면 아무도 쓰지 않는 파일을 '요약'으로 믿게 되어 더 나쁘다.)
    check("summary_path_no_json_out_empty", p2 == "", repr(p2))
    check("summary_path_relative_unmapped", "--json-out data/reports/x.json" not in p2, p2)

    print("[2] parse_rank_ic_money")
    check("parse_no_path", "error" in m.parse_rank_ic_money("", 0))
    check("parse_missing_file", "error" in m.parse_rank_ic_money("/tmp/nope_rank_ic.json", 0))

    tmpd = tempfile.mkdtemp()
    ok_path = _write(os.path.join(tmpd, "ok.json"),
                     {"arm": "AT", "fillable": True, "n_rows": 100,
                      "ic": _mk_ic(), "verdict": "신호있음"})
    check("parse_stale_mtime",
          "error" in m.parse_rank_ic_money(ok_path, time.time() + 10))
    parsed = m.parse_rank_ic_money(ok_path, 0)
    check("parse_ok", parsed.get("arm") == "AT" and parsed["ic"]["mean_ic"] == 0.05)
    check("parse_no_per_exp", "per_exp" not in parsed, str(sorted(parsed.keys()))[:80])
    bad_path = _write(os.path.join(tmpd, "bad.json"), {"ic": {}})
    check("parse_no_mean_ic", "error" in m.parse_rank_ic_money(bad_path, 0))
    junk_path = os.path.join(tmpd, "junk.json")
    with open(junk_path, "w", encoding="utf-8") as f:
        f.write("{not json")
    check("parse_junk", "error" in m.parse_rank_ic_money(junk_path, 0))

    print("[3] judge_rank_ic_money")
    item = {"id": "CG113", "metric": "rank_ic_money"}
    v, d, delta = m.judge_rank_ic_money(item, {"ic": _mk_ic()})
    check("judge_signal", v == "신호있음" and abs(delta - 0.05) < 1e-9, v)
    v, _, _ = m.judge_rank_ic_money(item, {"ic": _mk_ic(t=1.5)})
    check("judge_t_fail", v == "노이즈", v)
    v, _, _ = m.judge_rank_ic_money(item, {"ic": _mk_ic(h2=-0.01)})
    check("judge_half_fail", v == "노이즈", v)
    v, _, _ = m.judge_rank_ic_money(item, {"ic": _mk_ic(pos=0.50)})
    check("judge_pos_fail", v == "노이즈", v)
    v, _, _ = m.judge_rank_ic_money(item, {"ic": _mk_ic(mean=-0.02, t=-3.0)})
    check("judge_negative", v == "노이즈", v)
    v, _, _ = m.judge_rank_ic_money(item, {"error": "요약 파일 없음"})
    check("judge_error", v == "판정불가", v)
    v, _, _ = m.judge_rank_ic_money({"id": "CG113", "metric": "rank_ic_money", "min_t": 3.0},
                                    {"ic": _mk_ic(t=2.5)})
    check("judge_min_t_override", v == "노이즈", v)

    print("[4] e2e (합성 덤프 → 도구 → 파서·판정기)")
    rnd = random.Random(11)
    dump = os.path.join(tmpd, "synth.jsonl")
    with open(dump, "w", encoding="utf-8") as f:
        for di in range(60):
            date = "2026-%02d-%02d" % (1 + di // 28, 1 + di % 28)
            for _ in range(120):
                z = rnd.gauss(0, 1)
                f.write(json.dumps({"date": date, "code": "000001", "exp": "A",
                                    "y_pred": z,
                                    "fwd_ret": 0.2 * z + 0.98 * rnd.gauss(0, 1)}) + "\n")
    out = os.path.join(tmpd, "synth_out.json")
    rc = subprocess.run([sys.executable, os.path.join(REPO, "scripts/rank_ic_money.py"),
                         "--arm-jsonl", dump, "--json-out", out],
                        capture_output=True, text=True)
    check("e2e_tool_rc", rc.returncode == 0, rc.stderr[-200:])
    pr = m.parse_rank_ic_money(out, 0)
    check("e2e_parse", pr.get("ic", {}).get("mean_ic") is not None, str(pr)[:120])
    v, dd, _ = m.judge_rank_ic_money(item, pr)
    check("e2e_judge_signal", v == "신호있음", "%s | %s" % (v, dd[:90]))

    print("[5] 도구 자체점검 회귀")
    rc2 = subprocess.run([sys.executable, os.path.join(REPO, "scripts/rank_ic_money.py"),
                          "--selftest"], capture_output=True, text=True)
    check("tool_selftest", rc2.returncode == 0 and "12 PASS" in rc2.stdout,
          rc2.stdout.strip().splitlines()[-1] if rc2.stdout else "")

    print("[rank_ic_metric] %d PASS / %d FAIL" % (n_ok, len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""_fillable_topk_judge_test.py — 돈 지표(fillable_topk_expectancy) 파서·판정 자체점검.

pytest 가 없는 스택이므로 PASS/FAIL 을 직접 출력하고 실패 시 rc=1 로 끝난다.
호스트에서 돈다: python3 scripts/_fillable_topk_judge_test.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_engineer_cycle as m   # noqa: E402

FAIL = 0


def check(name, cond, extra=""):
    global FAIL
    ok = bool(cond)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {extra}" if extra else ""))
    if not ok:
        FAIL += 1


def make_summary(path, k3_net=0.55, k3_d=0.25, k3_t=3.1, k5_net=0.60, k5_d=0.18, k5_t=2.6,
                 halves3="both_positive", halves5="both_positive"):
    def block(net, d, t, hf):
        return {"arm": {"n": 80, "mean": net, "median": net, "sd": 1.2, "t": 2.5,
                        "pos_pct": 55.0, "worst": -4.0, "best": 5.0},
                "control": {"n": 80, "mean": net - d, "median": net - d, "sd": 1.2, "t": 1.0,
                            "pos_pct": 50.0, "worst": -4.5, "best": 4.8},
                "arm_halves": {"front": {"n": 40, "mean": 0.3}, "back": {"n": 40, "mean": 0.7},
                               "stable": hf, "split_session": "(5, '2026-03-02')"},
                "control_halves": {"front": {"n": 40, "mean": 0.1}, "back": {"n": 40, "mean": 0.4},
                                   "stable": hf, "split_session": "(5, '2026-03-02')"},
                "paired": {"n_dates": 80, "delta_mean": d, "median": d, "sd": 1.0, "t": t,
                           "pos_pct": 55.0, "worst": -3.0, "best": 3.0,
                           "pos": 44, "neg": 30, "ties": 6, "folds": {"1": "5/8", "2": "4/8"}}}
    payload = {"metric_name": "fillable_topk_expectancy", "exit": "close_h", "horizon": 5,
               "ks": [3, 5, 10], "arm_tag": "cg92_q05", "control_tag": "cg92_q30",
               "fee_roundtrip_pct": 0.21, "rows": {"arm": 24000, "control": 24000},
               "n_sessions_fillable": 80, "pool_median_fillable": 115,
               "conditions": {"fillable": {"filtered_out": {"arm": 300, "control": 300},
                                           "k": {"3": block(k3_net, k3_d, k3_t, halves3),
                                                 "5": block(k5_net, k5_d, k5_t, halves5)}},
                              "unfiltered": {"filtered_out": {"arm": 0, "control": 0},
                                             "k": {"3": {"arm": {"mean": -0.4},
                                                         "paired": {"delta_mean": -0.05}}}}}}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    return path


def main():
    tmp = tempfile.mkdtemp(prefix="ftk_")
    # 1) summary_path: --json-out 이 있으면 그 파일(호스트 경로 변환)
    sp = m.summary_path("fillable_topk_expectancy",
                        "docker exec c sh -c 'cd /app && python scripts/fillable_topk_expectancy.py "
                        "--json-out /app/reports/overnight/cg95_money.json'")
    check("summary_path --json-out → 호스트 경로",
          sp.replace("\\", "/").endswith("services/xgboost-ml/reports/overnight/cg95_money.json"), sp)
    sp2 = m.summary_path("fillable_topk_expectancy", "python scripts/x.py")
    check("summary_path --json-out 없음 → 빈 경로(예외 없음)", sp2 == "", repr(sp2))

    # 2) 파서: 없는 파일 / 낡은 파일
    p0 = m.parse_fillable_topk_expectancy(os.path.join(tmp, "nope.json"), 0.0)
    check("parser: 파일 없음 → error", bool(p0.get("error")), str(p0.get("error"))[:40])

    fresh = make_summary(os.path.join(tmp, "fresh.json"))
    mt = os.path.getmtime(fresh)
    stale = m.parse_fillable_topk_expectancy(fresh, mt + 10)
    check("parser: 요약 미갱신(mtime<=floor) → error", "미갱신" in str(stale.get("error")), str(stale.get("error"))[:40])

    parsed = m.parse_fillable_topk_expectancy(fresh, mt - 10)
    check("parser: 정상 파싱(conditions 존재)", bool((parsed.get("conditions") or {}).get("fillable")))
    check("parser: per_exp 를 만들지 않는다(스코어보드 오독 방지)", "per_exp" not in parsed)

    # 3) 판정기
    item = {"id": "CG95", "metric": "fillable_topk_expectancy"}
    v, d, first = m.judge_fillable_topk_expectancy(item, parsed)
    check("judge: 4조건 충족 → 신호있음", v == "신호있음", v + " | " + d[:70])
    check("judge: delta 반환", abs(float(first) - 0.25) < 1e-9, str(first))

    cases = [
        ("k5 Δ<+0.1", dict(k5_d=0.05), "노이즈"),
        ("k5 t<2", dict(k5_t=1.4), "노이즈"),
        ("arm 순기대<0", dict(k5_net=-0.1), "노이즈"),
        ("분할표본 unstable", dict(k5_net=0.6, halves5="unstable"), "노이즈"),
        ("k3 Δ<+0.1", dict(k3_d=0.09), "노이즈"),
    ]
    for nm, kw, want in cases:
        p = make_summary(os.path.join(tmp, f"c_{nm.replace(' ', '_').replace('<', 'lt')}.json"), **kw)
        pr = m.parse_fillable_topk_expectancy(p, 0.0)
        vv, dd, _ = m.judge_fillable_topk_expectancy(item, pr)
        check(f"judge: {nm} → {want}", vv == want, vv)

    # 4) k 통계 결측 → 판정불가
    bad = make_summary(os.path.join(tmp, "bad.json"))
    d0 = json.load(open(bad, encoding="utf-8"))
    del d0["conditions"]["fillable"]["k"]["5"]
    json.dump(d0, open(bad, "w", encoding="utf-8"))
    pr = m.parse_fillable_topk_expectancy(bad, 0.0)
    vv, dd, _ = m.judge_fillable_topk_expectancy(item, pr)
    check("judge: k=5 결측 → 판정불가", vv == "판정불가", vv)

    # 5) 파서 error 전달 → 판정불가
    vv, dd, _ = m.judge_fillable_topk_expectancy(item, {"error": "요약 파일 없음"})
    check("judge: 파서 error → 판정불가", vv == "판정불가", vv)

    # 6) 통합 배선: parse_by_metric / judge_by_metric 이 새 metric 을 인식하는가
    pr2 = m.parse_by_metric(item, fresh, 0.0)
    check("배선: parse_by_metric 이 fillable_topk_expectancy 인식",
          bool((pr2.get("conditions") or {}).get("fillable")), str(pr2.get("error"))[:40])
    v3, _, _ = m.judge_by_metric({"metric": "fillable_topk_expectancy",
                                  "id": "CG95", "success": "x"}, parsed)
    check("배선: judge_by_metric 이 fillable_topk_expectancy 인식", v3 == "신호있음", v3)

    print(f"\n{'ALL PASS' if not FAIL else str(FAIL) + ' FAIL'}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())

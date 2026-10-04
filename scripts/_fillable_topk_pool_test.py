#!/usr/bin/env python3
"""_fillable_topk_pool_test.py — 널 기준선 판정기(fillable_topk_vs_pool) + pool_series 자체점검.

WHY(2026-10-04 CG96): CG95 는 arm vs 대조군만 봐 **둘 다** +2%p/세션 대로 양(+)이었다 →
그 양수가 모델 엣지인지 시장 베타인지 가릴 널 기준선(세션 풀 평균 = 무작위 k 기대)이 없었다.
이 테스트는 (a) `fillable_topk_expectancy.pool_series` 가 세션별 풀 평균을 정확히 내는지,
(b) 새 metric `fillable_topk_vs_pool` 의 파서·판정·배선이 계약대로 동작하는지를 고정한다.

pytest 가 없는 스택이므로 PASS/FAIL 을 직접 출력하고 실패 시 rc=1 로 끝난다.
호스트에서 돈다: python3 scripts/_fillable_topk_pool_test.py
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
                 halves3="both_positive", halves5="both_positive", pool_mean=2.2, pool_t=2.8,
                 drop_baseline=False):
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
    fillable = {"filtered_out": {"arm": 300, "control": 300},
                "k": {"3": block(k3_net, k3_d, k3_t, halves3),
                      "5": block(k5_net, k5_d, k5_t, halves5)}}
    if not drop_baseline:
        fillable["baseline_pool"] = {
            "desc": "세션별 풀 평균",
            "stat": {"n": 80, "mean": pool_mean, "median": pool_mean, "sd": 3.0, "t": pool_t,
                     "pos_pct": 60.0, "worst": -5.0, "best": 8.0},
            "halves": {"front": {"n": 40, "mean": 1.5}, "back": {"n": 40, "mean": 2.9},
                       "stable": "both_positive", "split_session": "(5, '2026-03-02')"},
            "paired_by_k": {"3": {"n_dates": 80, "delta_mean": k3_d, "t": k3_t,
                                  "pos": 44, "neg": 30, "ties": 6},
                            "5": {"n_dates": 80, "delta_mean": k5_d, "t": k5_t,
                                  "pos": 43, "neg": 31, "ties": 6}},
        }
    payload = {"metric_name": "fillable_topk_expectancy", "exit": "close_h", "horizon": 5,
               "ks": [3, 5, 10], "arm_tag": "cg92_q05", "control_tag": "cg92_q30",
               "fee_roundtrip_pct": 0.21, "rows": {"arm": 24000, "control": 24000},
               "n_sessions_fillable": 80, "pool_median_fillable": 107,
               "conditions": {"fillable": fillable,
                              "unfiltered": {"filtered_out": {"arm": 0, "control": 0},
                                             "k": {"3": {"arm": {"mean": -0.4},
                                                         "paired": {"delta_mean": -0.05}}}}}}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    return path


def main():
    tmp = tempfile.mkdtemp(prefix="ftp_")

    # 1) summary_path 배선: 새 metric 도 --json-out 을 호스트 경로로 변환
    sp = m.summary_path("fillable_topk_vs_pool",
                        "docker exec c sh -c 'cd /app && python scripts/fillable_topk_expectancy.py "
                        "--json-out /app/reports/overnight/cg96_money.json'")
    check("summary_path: fillable_topk_vs_pool --json-out → 호스트 경로",
          sp.replace("\\", "/").endswith("services/xgboost-ml/reports/overnight/cg96_money.json"), sp)
    check("summary_path: fillable_topk_vs_pool --json-out 없음 → 빈 경로",
          m.summary_path("fillable_topk_vs_pool", "python scripts/x.py") == "")

    item = {"id": "CG96", "metric": "fillable_topk_vs_pool"}

    # 2) 정상 파싱 + per_exp 금지
    fresh = make_summary(os.path.join(tmp, "fresh.json"))
    pr = m.parse_by_metric(item, fresh, 0.0)
    check("parse_by_metric: 새 metric 인식", bool((pr.get("conditions") or {}).get("fillable")),
          str(pr.get("error"))[:40])
    check("parser: baseline_pool 을 담는다",
          bool(((pr.get("conditions") or {}).get("fillable") or {}).get("baseline_pool")))
    check("parser: per_exp 를 만들지 않는다", "per_exp" not in pr)

    # 3) 판정기 정상
    v, d, first = m.judge_fillable_topk_vs_pool(item, pr)
    check("judge: 4조건 충족 → 신호있음", v == "신호있음", v + " | " + d[:80])
    check("judge: delta(Δ arm−풀) 반환", abs(float(first) - 0.25) < 1e-9, str(first))

    # 4) 미달 케이스 → 노이즈
    cases = [
        ("k5 Δ<+0.1", dict(k5_d=0.09), "노이즈"),
        ("k5 t<2", dict(k5_t=1.4), "노이즈"),
        ("arm 순기대<0", dict(k5_net=-0.1), "노이즈"),
        ("분할표본 unstable", dict(halves5="unstable"), "노이즈"),
        ("k3 Δ<+0.1", dict(k3_d=0.05), "노이즈"),
        ("분할표본 neither", dict(k3_net=0.6, halves3="neither"), "노이즈"),
    ]
    for nm, kw, want in cases:
        p = make_summary(os.path.join(tmp, f"c_{abs(hash(nm))}.json"), **kw)
        prc = m.parse_by_metric(item, p, 0.0)
        vv, dd, _ = m.judge_fillable_topk_vs_pool(item, prc)
        check(f"judge: {nm} → {want}", vv == want, vv)

    # 5) baseline_pool 결측 → 판정불가(구 스키마 방어)
    nb = make_summary(os.path.join(tmp, "nobase.json"), drop_baseline=True)
    prn = m.parse_by_metric(item, nb, 0.0)
    vv, dd, _ = m.judge_fillable_topk_vs_pool(item, prn)
    check("judge: baseline_pool 결측 → 판정불가", vv == "판정불가", vv)

    # 6) 파서 error 전달 → 판정불가
    vv, dd, _ = m.judge_fillable_topk_vs_pool(item, {"error": "요약 파일 없음"})
    check("judge: 파서 error → 판정불가", vv == "판정불가", vv)

    # 7) 통합 배선: judge_by_metric
    v3, _, _ = m.judge_by_metric(item, pr)
    check("배선: judge_by_metric 이 fillable_topk_vs_pool 인식", v3 == "신호있음", v3)

    # 8) 기존 metric 회귀 — fillable_topk_expectancy 는 baseline_pool 이 있어도 종전대로
    old_item = {"id": "CG95", "metric": "fillable_topk_expectancy"}
    v_old, _, _ = m.judge_by_metric(old_item, pr)
    check("회귀: fillable_topk_expectancy 판정 불변(신호있음)", v_old == "신호있음", v_old)

    # 9) pool_series 단위 검증(컨테이너 전용 의존성 — 없으면 NOTE)
    try:
        import fillable_topk_expectancy as fte   # noqa: E402
        rows = [{"fold": 1, "date": "2026-01-02", "gross_pct": 1.0},
                {"fold": 1, "date": "2026-01-02", "gross_pct": 3.0},
                {"fold": 1, "date": "2026-01-03", "gross_pct": -1.0}]
        pool = fte.pool_series(rows, 0.21)
        ok = (abs(pool[(1, "2026-01-02")] - (2.0 - 0.21)) < 1e-9
              and abs(pool[(1, "2026-01-03")] - (-1.0 - 0.21)) < 1e-9)
        check("pool_series: 세션 평균 − 수수료", ok, str(pool))
    except Exception as e:   # numpy/pandas 등 컨테이너 전용 의존성 부재
        print(f"[NOTE] pool_series 단위검증 건너뜀(호스트 의존성 부재: {type(e).__name__}) "
              f"— 컨테이너에서 재실행 권장")

    print(f"\n{'ALL PASS' if not FAIL else str(FAIL) + ' FAIL'}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""CG84 셋업 자체점검 — 정책 비교 계측기 + 구동기 배선(parse/judge/summary_path).

호스트에서 돈다(순수 파이썬 · app.calibration 미사용 → --no-calibration):
  python3 scripts/_policy_compare_test.py
컨테이너에서도 돈다:
  docker exec -e PYTHONPATH=/app stock_xgboost_ml python /app/scripts/_policy_compare_test.py

검사:
  [1] binom_two_sided 부호검정 값 sanity
  [2] pair_compare — top-k 가 일관 우위면 Δ>0·pos_rate 1.0·p<0.05
  [3] pair_compare — 두 집합이 같으면 Δ 0·전부 동점(ties)
  [4] pair_compare — top-k 가 열위면 Δ<0
  [5] judge_policy_compare — 통과 k ≥ 2 → '근거 있음'
  [6] judge_policy_compare — 통과 k < 2 → '근거 없음'
  [7] judge_policy_compare — error → '판정불가'
  [8] summary_path('policy_compare', ...) 이 --json-out 을 호스트 경로로 매핑
  [9] parse_policy_compare — 스키마 파싱 + per_exp 를 만들지 않는다
  [10] parse_policy_compare — 파일 없음 → error
  [11] e2e — 합성 preds jsonl 로 계측기 실행 → JSON 산출·k_passed 계산
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

FA = FB = 0
FAILS = []


def check(name, cond, got=""):
    global FA, FB
    if cond:
        FA += 1
        print(f"  PASS {name}")
    else:
        FB += 1
        FAILS.append(name)
        print(f"  FAIL {name} {got}")


def _rows(p_vals, fwd):
    """(y_pred, fwd_ret) 쌍 리스트 → 행 dict 리스트(합성 데이터 헬퍼)."""
    return [{"y_pred": p, "fwd_ret": f, "fold": 1, "date": "2026-01-01", "y_true": 1}
            for p, f in zip(p_vals, fwd)]


def main():
    import policy_compare_probe as pc
    import model_engineer_cycle as m

    print("[1] binom_two_sided")
    check("n=0 → 1.0", pc.binom_two_sided(0, 0) == 1.0)
    check("(10,0) → 2/2^10", abs(pc.binom_two_sided(10, 0) - 2 / 1024) < 1e-12,
          got=str(pc.binom_two_sided(10, 0)))
    check("(5,5) → 1.0", pc.binom_two_sided(5, 5) == 1.0)

    # 그룹 8개, 각 12행: p 는 0.95→0.20 단조감소, fwd 도 단조감소 → top-k 가 항상 우위
    p_vals = [round(0.95 - 0.05 * i, 2) for i in range(12)]
    f_good = [round(0.10 - 0.01 * i, 4) for i in range(12)]     # 단조감소 → top-k 우위
    f_flat = [0.05] * 12                                         # 전부 동일 → Δ 0
    f_bad = [round(-0.10 + 0.01 * i, 4) for i in range(12)]      # 단조증가 → top-k 열위
    rows, groups = [], {}
    for d in range(8):
        base = len(rows)
        for i, (p, f) in enumerate(zip(p_vals, f_good)):
            rows.append({"y_pred": p, "fwd_ret": f, "fold": 1, "date": f"2026-01-{d+1:02d}",
                         "y_true": 1})
        groups[(1, f"2026-01-{d+1:02d}")] = list(range(base, base + 12))
    pc._ROWS = rows

    print("[2] pair_compare — top-k 우위")
    a = pc.pair_compare(groups, [r["y_pred"] for r in rows], 0.55, 3)
    check("Δ > 0", (a["mean_delta"] or 0) > 0, got=str(a["mean_delta"]))
    check("pos_rate = 1.0", a["pos_rate"] == 1.0, got=str(a["pos_rate"]))
    check("p < 0.05", a["p_value"] < 0.05, got=str(a["p_value"]))
    check("n_pairs = 8", a["n_pairs"] == 8, got=str(a["n_pairs"]))

    print("[3] pair_compare — 동일 집합")
    rows2, groups2 = [], {}
    for d in range(8):
        base = len(rows2)
        for p, f in zip(p_vals, f_flat):
            rows2.append({"y_pred": p, "fwd_ret": f, "fold": 1, "date": f"2026-01-{d+1:02d}",
                          "y_true": 1})
        groups2[(1, f"2026-01-{d+1:02d}")] = list(range(base, base + 12))
    pc._ROWS = rows2
    b = pc.pair_compare(groups2, [r["y_pred"] for r in rows2], 0.55, 3)
    check("Δ = 0", b["mean_delta"] == 0.0, got=str(b["mean_delta"]))
    check("전부 동점", b["ties"] == 8, got=str(b["ties"]))
    check("p = 1.0", b["p_value"] == 1.0, got=str(b["p_value"]))

    print("[4] pair_compare — top-k 열위")
    rows3, groups3 = [], {}
    for d in range(8):
        base = len(rows3)
        for p, f in zip(p_vals, f_bad):
            rows3.append({"y_pred": p, "fwd_ret": f, "fold": 1, "date": f"2026-01-{d+1:02d}",
                          "y_true": 1})
        groups3[(1, f"2026-01-{d+1:02d}")] = list(range(base, base + 12))
    pc._ROWS = rows3
    c = pc.pair_compare(groups3, [r["y_pred"] for r in rows3], 0.55, 3)
    check("Δ < 0", (c["mean_delta"] or 0) < 0, got=str(c["mean_delta"]))
    check("neg = 8", c["neg"] == 8, got=str(c["neg"]))

    print("[5-7] judge_policy_compare")
    item = {"metric": "policy_compare", "id": "CG84"}
    parsed_pass = {"primary_scale": "raw", "ks": [3, 5, 10], "criterion": {},
                   "policy": {"raw": {f"k{k}": {"mean_delta": 0.01, "pos_rate": 0.7,
                                                "pos": 7, "n_pairs": 10, "p_value": 0.01}
                                     for k in (3, 5, 10)}}}
    v, d, dl = m.judge_policy_compare(item, parsed_pass)
    check("통과 3/3 → 근거 있음", v == "정책 교체 근거 있음", got=v)
    check("delta 실림", dl == 0.01, got=str(dl))

    parsed_one = {"primary_scale": "raw", "ks": [3, 5, 10], "criterion": {},
                  "policy": {"raw": {
                      "k3": {"mean_delta": 0.01, "pos_rate": 0.7, "pos": 7, "n_pairs": 10, "p_value": 0.01},
                      "k5": {"mean_delta": -0.002, "pos_rate": 0.4, "pos": 4, "n_pairs": 10, "p_value": 0.3},
                      "k10": {"mean_delta": 0.001, "pos_rate": 0.55, "pos": 5, "n_pairs": 10, "p_value": 0.6}}}}
    v2, d2, _ = m.judge_policy_compare(item, parsed_one)
    check("통과 1/3 → 근거 없음", v2 == "정책 교체 근거 없음", got=v2)

    v3, d3, dl3 = m.judge_policy_compare(item, {"error": "요약 파일 없음"})
    check("error → 판정불가", v3 == "판정불가" and dl3 is None, got=f"{v3} / delta={dl3}")

    print("[8] summary_path 배선")
    cmd = ("docker exec stock_xgboost_ml sh -c 'cd /app && python -u "
           "scripts/policy_compare_probe.py /app/reports/overnight/cg81_preds.jsonl "
           "--json-out /app/reports/overnight/cg84_policy.json'")
    sp = m.summary_path("policy_compare", cmd)
    check("--json-out → 호스트 경로",
          sp.endswith("services/xgboost-ml/reports/overnight/cg84_policy.json"), got=sp)
    check("--json-out 없으면 빈 경로", m.summary_path("policy_compare", "python x.py") == "")

    print("[9-10] parse_policy_compare")
    with tempfile.TemporaryDirectory() as tmp:
        jp = os.path.join(tmp, "pc.json")
        payload = {
            "generated_at": "2026-10-03T14:00:00+00:00",
            "n_rows": 100, "n_groups": 10, "threshold": 0.55, "ks": [3, 5, 10],
            "primary_scale": "raw", "k_passed": [3, 5],
            "criterion": {"min_pos_rate": 0.6, "max_p": 0.05, "min_k_pass": 2},
            "scales": {"raw": {"k3": {"n_pairs": 10, "mean_delta": 0.011, "std": 0.02,
                                      "se": 0.006, "pos": 7, "neg": 3, "ties": 0,
                                      "pos_rate": 0.7, "p_value": 0.02, "abs_mean_ret": 0.01,
                                      "topk_mean_ret": 0.021, "abs_n": 44, "topk_n": 30},
                             "k5": {"n_pairs": 10, "mean_delta": 0.008, "pos_rate": 0.7,
                                    "pos": 7, "neg": 3, "ties": 0, "p_value": 0.04}},
                      "platt": {"k3": {"n_pairs": 10, "mean_delta": 0.004, "pos_rate": 0.6,
                                       "pos": 6, "neg": 4, "ties": 0, "p_value": 0.2}}},
        }
        with open(jp, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        p = m.parse_policy_compare(jp, 0.0)
        check("error 없음", not p.get("error"), got=str(p.get("error")))
        check("raw.k3.mean_delta 유지", p["policy"]["raw"]["k3"]["mean_delta"] == 0.011)
        check("per_exp 를 만들지 않음", "per_exp" not in p)
        check("primary_scale 유지", p.get("primary_scale") == "raw")
        # 판정 스케일 raw → k3·k5 통과(2개) → 근거 있음
        vv, _, _ = m.judge_policy_compare(item, p)
        check("파싱본 판정 = 근거 있음", vv == "정책 교체 근거 있음", got=vv)
        p2 = m.parse_policy_compare(os.path.join(tmp, "nope.json"), 0.0)
        check("파일 없음 → error", bool(p2.get("error")))

    print("[11] e2e — 계측기 실행(합성 preds, --no-calibration)")
    with tempfile.TemporaryDirectory() as tmp:
        pp = os.path.join(tmp, "preds.jsonl")
        with open(pp, "w", encoding="utf-8") as f:
            for d in range(8):
                for p, fv in zip(p_vals, f_good):
                    f.write(json.dumps({"exp": "X", "fold": 1, "date": f"2026-01-{d+1:02d}",
                                        "code": "000001", "y_true": 1, "y_pred": p,
                                        "fwd_ret": fv}) + "\n")
        out = os.path.join(tmp, "out.json")
        r = subprocess.run([sys.executable, os.path.join(HERE, "policy_compare_probe.py"),
                            pp, "--no-calibration", "--json-out", out],
                           capture_output=True, text=True, timeout=120)
        ok = r.returncode == 0 and os.path.exists(out)
        check("rc=0 · JSON 산출", ok, got=r.stdout[-400:] + r.stderr[-400:])
        if ok:
            with open(out, encoding="utf-8") as f:
                d = json.load(f)
            check("scales.raw 존재", "raw" in d.get("scales", {}))
            check("k_passed 에 3·5 포함", set(d.get("k_passed") or []) >= {3, 5},
                  got=str(d.get("k_passed")))

    print(f"\n결과: {FA} PASS / {FB} FAIL")
    if FAILS:
        print("실패:", ", ".join(FAILS))
    return 1 if FB else 0


if __name__ == "__main__":
    sys.exit(main())

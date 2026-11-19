#!/usr/bin/env python3
"""[CG359] date_paired_auc 파서·판정기 회귀 테스트.

왜 필요한가: 새 metric 을 등록하면서 파서만 있고 판정 분기가 없으면 judge_per 로 흘러
원장 헤드라인이 '판정불가'가 된다(과거 CG255·CG186 계열 사고). 또 해상 게이트(짝 SE >
문턱/3)가 없으면 n=2 짝 결과를 '노이즈'로 오독한다 — 이 테스트가 그 두 가지를 못 박는다.

실행: python3 scripts/_date_paired_parser_test.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_engineer_cycle as mec  # noqa: E402


def write(d):
    fd, p = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(d, f)
    return p


def mk(n, delta, sd, pos=None, boot=(-0.05, 0.05)):
    return {
        "arms": {
            "A": {"dates": n, "pooled_auc": 0.52,
                  "date_auc": {"n": n, "mean": 0.52, "sd": 0.06, "se": 0.06 / n ** 0.5,
                               "min": 0.4, "max": 0.66}},
            "B": {"dates": n, "pooled_auc": 0.52 + delta,
                  "date_auc": {"n": n, "mean": 0.52 + delta, "sd": 0.06, "se": 0.06 / n ** 0.5,
                               "min": 0.4, "max": 0.66}},
        },
        "pair": {"date": {"n_pairs": n,
                          "delta": {"n": n, "mean": delta, "sd": sd,
                                    "se": sd / n ** 0.5, "t": delta / (sd / n ** 0.5),
                                    "pos": pos if pos is not None else int(n * 0.6),
                                    "neg": n - (pos if pos is not None else int(n * 0.6)),
                                    "pos_frac": 0.6, "sign_test_p": 0.3},
                          "first": "2026-10-02", "last": "2026-11-04", "bootstrap95": list(boot)}},
        "protocol": {"unit_primary": "date"},
    }


def run(item, path, floor=0.0):
    parsed = mec.parse_by_metric(item, path, floor)
    verdict, detail = mec.judge_by_metric(item, parsed)
    return parsed, verdict, detail


CASES = []


def case(fn):
    CASES.append(fn)
    return fn


@case
def real_cg359():
    """실제 CG359 산출물 — 짝 SE 0.0187 > 0.0067 이므로 '미해상 — 짝 SE 과대'."""
    p = "data/reports/overnight/cg359_paired_dates.json"
    if not os.path.exists(p):
        return "SKIP 파일없음", ""
    parsed, verdict, detail = run({"id": "T1", "metric": "date_paired_auc"}, p)
    ok = (verdict == "미해상 — 짝 SE 과대" and parsed["paired"]["n"] == 22
          and abs(parsed["paired"]["delta_mean"] + 0.0194) < 0.001)
    return ("PASS" if ok else "FAIL"), f"{verdict} | {detail[:120]}"


@case
def signal():
    p = write(mk(22, 0.030, 0.020))
    _, verdict, detail = run({"id": "T2", "metric": "date_paired_auc"}, p)
    return ("PASS" if verdict == "신호" else "FAIL"), f"{verdict} | {detail[:100]}"


@case
def noise():
    p = write(mk(22, 0.004, 0.020))
    _, verdict, detail = run({"id": "T3", "metric": "date_paired_auc"}, p)
    return ("PASS" if verdict == "노이즈" else "FAIL"), f"{verdict} | {detail[:100]}"


@case
def underpowered():
    """n=5 는 SE 가 작아도 판정 최소 미달 → 판정불가."""
    p = write(mk(5, 0.050, 0.010, pos=5))
    _, verdict, detail = run({"id": "T4", "metric": "date_paired_auc"}, p)
    return ("PASS" if verdict == "판정불가" else "FAIL"), f"{verdict} | {detail[:100]}"


@case
def stale_summary():
    p = write(mk(22, 0.030, 0.020))
    _, verdict, detail = run({"id": "T5", "metric": "date_paired_auc"}, p,
                             floor=os.path.getmtime(p) + 5)
    return ("PASS" if verdict == "판정불가" and "미갱신" in detail else "FAIL"), detail[:80]


@case
def missing_file():
    _, verdict, detail = run({"id": "T6", "metric": "date_paired_auc"}, "/tmp/__no_such__.json")
    return ("PASS" if verdict == "판정불가" else "FAIL"), detail[:60]


def main():
    bad = 0
    for fn in CASES:
        try:
            res, msg = fn()
        except Exception as e:  # pragma: no cover - 테스트 실패를 명시적으로 보이게
            res, msg = "ERROR", f"{type(e).__name__}: {e}"
        if res != "PASS" and res != "SKIP 파일없음":
            bad += 1
        print(f"[{res}] {fn.__name__}: {msg}")
    print(f"\n{len(CASES)-bad}/{len(CASES)} PASS")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

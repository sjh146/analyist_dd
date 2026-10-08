#!/usr/bin/env python3
"""자체점검: protocol_noise_floor metric 배선 (CG143, 2026-10-09).

검증:
  1) summary_path("protocol_noise_floor") → .../overnight/cg143_noise_floor.json (고정 경로)
  2) parse_by_metric 이 이 metric 을 새 파서로 보낸다(다른 파서로 새지 않는다)
  3) 판정: 결정성 위반 / 잡음≥문턱 / 잡음<문턱 세 갈래
  4) 오류 경로(파일 없음·mtime 미갱신·런 실패)에서 예외 없이 error dict
  5) **per_exp 를 만들지 않는다**(scoreboard 오독 방지 — CG31 사고와 동형)

실행: python3 scripts/_protocol_noise_floor_test.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import model_engineer_cycle as m  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, extra: object = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"PASS  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}  {extra}")


def _write(d, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    return path


def _sample(shift, det, errors=None):
    return {
        "metric": "protocol_noise_floor",
        "anchors": {"A": "2026-10-07", "B": "2026-10-06"},
        "config": "folds=3 dates_per_fold=5 stocks=60 h=5 rel model=champion",
        "determinism_same_anchor": det,
        "auc": {"a1": 0.5310, "a2": 0.5310 if det else 0.5299, "b1": 0.5310 - shift},
        "anchor_shift_delta": shift,
        "folds": {"a1": [0.53, 0.52], "b1": [0.51, 0.53]},
        "windows": {"a1": [["a", "b"]], "b1": [["c", "d"]]},
        "pre_registered_threshold": 0.02,
        "verdict": "x",
        "errors": errors or {},
    }


def main() -> int:
    sp = m.summary_path("protocol_noise_floor")
    check("summary_path: 고정 경로", sp.endswith("overnight/cg143_noise_floor.json"), sp)

    tmp = tempfile.mkdtemp(prefix="cg143test_")

    # 3a) 결정성 위반
    p = _write(_sample(0.0020, False), os.path.join(tmp, "det.json"))
    r = m.parse_by_metric({"metric": "protocol_noise_floor"}, p, 0.0)
    check("파서 배선: dict 반환", isinstance(r, dict), repr(type(r)))
    check("결정성 위반 판정", r.get("verdict", "").startswith("결정성 위반"), r.get("verdict"))
    check("per_exp 미생성", "per_exp" not in r)

    # 3b) 잡음 ≥ 문턱
    p = _write(_sample(0.0227, True), os.path.join(tmp, "hi.json"))
    r = m.parse_by_metric({"metric": "protocol_noise_floor"}, p, 0.0)
    check("잡음≥문턱 판정", "잡음바닥 >= 문턱" in r.get("verdict", ""), r.get("verdict"))
    check("noise_over_threshold True", r.get("noise_over_threshold") is True)
    check("anchor_shift_delta 보존", r.get("anchor_shift_delta") == 0.0227)

    # 3c) 잡음 < 문턱
    p = _write(_sample(0.0100, True), os.path.join(tmp, "lo.json"))
    r = m.parse_by_metric({"metric": "protocol_noise_floor"}, p, 0.0)
    check("잡음<문턱 판정", r.get("verdict") == "잡음바닥 < 문턱", r.get("verdict"))
    check("noise_over_threshold False", r.get("noise_over_threshold") is False)

    # 4) 오류 경로 — 예외 없이 error dict
    r = m.parse_by_metric({"metric": "protocol_noise_floor"}, os.path.join(tmp, "nope.json"), 0.0)
    check("파일 없음 → error", "error" in r, r)
    p = os.path.join(tmp, "lo.json")
    r = m.parse_by_metric({"metric": "protocol_noise_floor"}, p, time.time() + 10)
    check("mtime 미갱신 → error", "error" in r, r)
    p = _write(_sample(0.02, True, errors={"a1": "boom"}), os.path.join(tmp, "err.json"))
    r = m.parse_by_metric({"metric": "protocol_noise_floor"}, p, 0.0)
    check("런 실패 → error", "error" in r, r)

    print(f"\n{PASS}/{PASS + FAIL} PASS")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

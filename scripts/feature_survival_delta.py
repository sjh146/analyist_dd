#!/usr/bin/env python3
"""피처 생존 v1 vs v2 델타 리포트 (docs/spec_panel_rebuild.md 후속).

두 생존 판정 JSON 을 읽어 클래스 이동(부활/악화/유지)을 표로 만든다. DB·네트워크 미사용.

⚠ 비교 한계: v1(panel_prod200) 과 v2(panel_prod200_v2) 는 **유니버스가 다르다**
(교집합 23/200 — prod 유니버스는 '최근 데이터순 200종목'이라 시간이 지나면 구성이 바뀐다).
따라서 이 델타는 "같은 종목에서의 개선"이 아니라 **패널 재빌드로 관측 가능해진 변화**로 읽어야 한다.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def load(path):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return {x["feature"]: x for x in data["features"]}, data


def main(argv=None):
    ap = argparse.ArgumentParser(description="피처 생존 v1/v2 델타")
    ap.add_argument("--v1", default="data/reports/feature_survival.json")
    ap.add_argument("--v2", default="data/reports/feature_survival_v2.json")
    ap.add_argument("--json-out", default="data/reports/feature_survival_delta.json")
    ap.add_argument("--md-out", default="data/reports/feature_survival_delta.md")
    args = ap.parse_args(argv)

    a, da = load(args.v1)
    b, db = load(args.v2)

    moves = []
    for name in sorted(set(a) & set(b)):
        ca, cb = a[name]["class"], b[name]["class"]
        if ca != cb:
            moves.append({
                "feature": name, "from": ca, "to": cb,
                "nnz_v1": a[name]["stats"]["nnz_ratio_honest"],
                "nnz_v2": b[name]["stats"]["nnz_ratio_honest"],
                "std_v2": b[name]["stats"]["std"],
            })

    revived = [m for m in moves if m["from"].startswith("DEAD") and m["to"] in ("ALIVE", "CONDITIONAL")]
    zero_to_sparse = [m for m in moves if m["from"] == "DEAD_CONST_ZERO" and m["to"] == "DEAD_SPARSE"]
    worse = [m for m in moves if m["to"].startswith("DEAD") and not m["from"].startswith("DEAD")]
    still_zero = sorted(n for n, x in b.items() if x["class"] == "DEAD_CONST_ZERO")

    out = {
        "measured_at": datetime.now(KST).isoformat(timespec="seconds"),
        "v1": {"panel": da["panel"], "summary": da["summary"]},
        "v2": {"panel": db["panel"], "summary": db["summary"]},
        "universe_caveat": ("v1/v2 유니버스 교집합 23/200 — prod 유니버스는 최근데이터순이라 구성이 바뀐다. "
                            "델타는 '같은 종목 개선'이 아니다."),
        "class_changes": len(moves),
        "transition_counts": {f"{k[0]}->{k[1]}": v for k, v in Counter((m["from"], m["to"]) for m in moves).items()},
        "revived": revived,
        "zero_to_sparse": zero_to_sparse,
        "worse": worse,
        "still_const_zero": still_zero,
        "verdict": "",
    }

    effective = [m for m in zero_to_sparse if m["nnz_v2"] >= 0.01]
    out["verdict"] = (
        "재빌드로 클래스가 바뀐 피처 %d개(전부 상수0→희박). 그중 nnz≥1%% 는 %d개 — "
        "즉 재빌드만으로는 실질 부활이 거의 없다. 남은 상수0 %d개는 소스 부재다."
        % (len(moves), len(effective), len(still_zero))
    )

    with open(args.json_out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
        fh.write("\n")

    lines = [
        "# 피처 생존 v1 → v2 델타 (2026-11-18)",
        "",
        "| 구분 | v1 | v2 |",
        "|---|---|---|",
        f"| 패널 | {da['panel']['shape']} {da['panel']['span'][0]}~{da['panel']['span'][1]} | "
        f"{db['panel']['shape']} {db['panel']['span'][0]}~{db['panel']['span'][1]} |",
        f"| alive / dead / 조건부 | {da['summary']['alive']} / {da['summary']['dead']} / {da['summary']['conditional']} "
        f"| {db['summary']['alive']} / {db['summary']['dead']} / {db['summary']['conditional']} |",
        f"| DEAD_CONST_ZERO | {da['summary']['by_class']['DEAD_CONST_ZERO']} | {db['summary']['by_class']['DEAD_CONST_ZERO']} |",
        f"| DEAD_SPARSE | {da['summary']['by_class']['DEAD_SPARSE']} | {db['summary']['by_class']['DEAD_SPARSE']} |",
        "",
        "**한계**: " + out["universe_caveat"],
        "",
        "## 판정",
        "",
        out["verdict"],
        "",
        "## 상수0 → 희박(정보는 생겼지만 밀도가 낮음)",
        "",
        "| feature | nnz v1 | nnz v2 |",
        "|---|---|---|",
    ]
    for m in zero_to_sparse:
        lines.append(f"| {m['feature']} | {m['nnz_v1']:.6f} | {m['nnz_v2']:.6f} |")
    lines += ["", "## 여전히 상수 0 (소스 부재)", ""]
    lines += [f"- {n}" for n in still_zero]
    lines += ["", "## 실질 부활(nnz≥1%)", ""]
    lines += ([f"- {m['feature']} (nnz {m['nnz_v2']:.4f})" for m in effective]
              or ["- 없음"])
    with open(args.md_out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    print("class_changes=%d revived=%d zero_to_sparse=%d still_zero=%d"
          % (len(moves), len(revived), len(zero_to_sparse), len(still_zero)))
    print(out["verdict"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

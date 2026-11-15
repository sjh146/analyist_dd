#!/usr/bin/env python3
"""_cg308_fwd_check.py — dump 의 fwd_ret 가 패널 가격 기반 5일 선행수익과 일치하는가(임시).

_cg307_check 의 corr 0.53 이 (a) 단위 차이 (b) fwd_ret 자체 결함 중 무엇인지 가른다.
"""
import json

import numpy as np
import pandas as pd

PANEL = "/app/app/models/wf/panel_prod200.npz"
DUMP = "/app/reports/overnight/cg291_preds.jsonl"


def main():
    z = np.load(PANEL, allow_pickle=True)
    df = pd.DataFrame({"date": np.asarray(z["dates"]).astype(str),
                       "code": np.asarray(z["codes"]).astype(str),
                       "price": np.asarray(z["price"], dtype=float)})
    df = df[df["price"] > 0].sort_values(["code", "date"])
    df["label"] = df.groupby("code", sort=False)["price"].shift(-5) / df["price"] - 1.0
    ref = {(r.date, r.code): r.label for r in df.itertuples()}

    rows = []
    with open(DUMP) as f:
        for line in f:
            r = json.loads(line)
            if r["exp"] != "CO_q05_h5":
                continue
            rows.append((r["date"], r["code"], r["fwd_ret"]))
    print("dump rows", len(rows))
    same, diff = 0, []
    for dt, cd, fr in rows:
        lb = ref.get((dt, cd))
        if lb is None or lb != lb:
            continue
        if abs(fr - lb) < 1e-6:
            same += 1
        else:
            diff.append((dt, cd, fr, lb))
    tot = same + len(diff)
    print("matched exactly %d / %d (%.1f%%)" % (same, tot, 100.0 * same / max(tot, 1)))
    if diff:
        d = np.array([abs(x[2] - x[3]) for x in diff])
        print("diff n %d · mean %.4f · median %.4f · >0.01 %.1f%%" % (len(diff), d.mean(), np.median(d), 100 * (d > 0.01).mean()))
        for row in diff[:5]:
            print("   %s %s dump %.4f panel %.4f" % row)


if __name__ == "__main__":
    main()

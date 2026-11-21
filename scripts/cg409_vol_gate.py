#!/usr/bin/env python3
"""cg409_vol_gate.py — 모델 점수 top-k 에 '저변동 게이트'를 얹으면 체결가능 순기대가 개선되는가.

배경(2026-11-22 실측 CG408): v6 패널(200종목·2025-08-04~2026-11-17)에서 룰 기반 선택 4축을
체결가능 순기대(fillable, k=5, h5, stride 5, 수수료 0.21%p)로 재니 풀평균(-0.302%p/세션) 대비
  value_cheap Δ-0.669(t -1.27) · momentum Δ-0.830(t -1.03) · supply Δ-0.347(t -0.71) = 전부 음(-),
  저변동(volatility_20d·atr·bb_width·volatility_60d)만 Δ+0.696(t 1.51) · 절대 순기대 +0.394%p.
  단 앞/뒤 반쪽 +0.838/-0.037 = 불안정 → 단독 신호 아님. 다음 질문: '모델 점수 위에 얹으면' 개선되는가.

이 스크립트는 모델 dump(exp/date/code/y_pred)를 읽어, 같은 (date,code)의 패널 저변동 순위가
하위 q(기본 50%)인 행만 남긴 arm 과 전체를 그대로 쓰는 control 을 각각 jsonl 로 쓴다.
점수는 건드리지 않는다(게이트만) — fillable_topk_expectancy 의 같은 런 짝 비교로 Δ 를 잰다.

사용(컨테이너 내부):
  python -u scripts/cg409_vol_gate.py --preds /app/reports/overnight/cg409_preds.jsonl --tag CO_rank_smooth_d1_h5 \
      --panel /app/app/models/wf/panel_prod200_v6.npz --out-arm /app/reports/overnight/cg409_gate_arm.jsonl \
      --out-control /app/reports/overnight/cg409_gate_ctl.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys

import numpy as np

VOL_FEATURES = ["volatility_20d", "atr", "bb_width", "volatility_60d"]


def vol_score_by_date(panel, feats, min_valid=2):
    d = np.load(panel, allow_pickle=True)
    X = d["X"]
    fn = [str(x) for x in d["feature_names"]]
    dates = np.array([str(x) for x in d["dates"]])
    codes = np.array([str(x).zfill(6) for x in d["codes"]])
    idx = [fn.index(f) for f in feats if f in fn]
    if len(idx) < min_valid:
        raise SystemExit(f"[FAIL] 패널에 저변동 피처 부족: {feats}")
    sub = X[:, idx].astype(np.float64)
    out = {}
    for dt in sorted(set(dates.tolist())):
        m = dates == dt
        block = sub[m]
        cs = codes[m]
        rs = np.zeros(len(block))
        cnt = np.zeros(len(block))
        for j in range(block.shape[1]):
            col = block[:, j]
            ok = np.isfinite(col)
            if ok.sum() < 10:
                continue
            pct = col[ok].argsort().argsort().astype(np.float64) / max(ok.sum() - 1, 1)
            rs[ok] += 1.0 - pct          # 값이 낮을수록(=저변동) 높은 점수
            cnt[ok] += 1
        for c_, s_, n_ in zip(cs, rs, cnt):
            if n_ >= min_valid:
                out[(dt, c_)] = s_ / n_
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--preds", required=True)
    ap.add_argument("--tag", required=True, help="preds jsonl 의 exp 값")
    ap.add_argument("--panel", required=True)
    ap.add_argument("--vol-features", default=",".join(VOL_FEATURES))
    ap.add_argument("--vol-q", type=float, default=0.5, help="저변동 하위 비율(게이트 통과)")
    ap.add_argument("--tag-arm", default="cg409_volgate")
    ap.add_argument("--tag-control", default="cg409_nogate")
    ap.add_argument("--out-arm", required=True)
    ap.add_argument("--out-control", required=True)
    a = ap.parse_args(argv)

    vs = vol_score_by_date(a.panel, [f.strip() for f in a.vol_features.split(",") if f.strip()])

    # 날짜별 게이트 컷(저변동 하위 q 경계)을 미리 계산 — 행마다 재계산하면 O(n^2)
    cut_by_date = {}
    by_date = {}
    for (dt, _c), v in vs.items():
        by_date.setdefault(dt, []).append(v)
    for dt, vals in by_date.items():
        vals.sort()
        cut_by_date[dt] = vals[max(int(len(vals) * (1.0 - a.vol_q)) - 1, 0)]

    kept = total = 0
    with open(a.preds, encoding="utf-8") as f, \
            open(a.out_arm, "w", encoding="utf-8") as fa, \
            open(a.out_control, "w", encoding="utf-8") as fc:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("exp") != a.tag:
                continue
            dt, code = str(r["date"]), str(r["code"]).zfill(6)
            total += 1
            base = {"fold": int(r.get("fold") or 0), "date": dt, "code": code,
                    "y_true": r.get("y_true"), "y_pred": float(r["y_pred"]),
                    "fwd_ret": r.get("fwd_ret")}
            fc.write(json.dumps({**base, "exp": a.tag_control}, ensure_ascii=False) + "\n")
            # 날짜 내 저변동 하위 q 만 통과
            cut = cut_by_date.get(dt)
            if cut is None:
                continue
            s = vs.get((dt, code))
            if s is None or s < cut:
                continue
            fa.write(json.dumps({**base, "exp": a.tag_arm}, ensure_ascii=False) + "\n")
            kept += 1

    print(f"preds 행 {total} · 게이트 통과 {kept} ({kept / max(total, 1) * 100:.1f}%) · vol_q {a.vol_q}")
    if total == 0:
        print(f"[FAIL] tag={a.tag} 행 없음", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

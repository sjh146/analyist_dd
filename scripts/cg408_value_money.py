#!/usr/bin/env python3
"""cg408_value_money.py — v6 패널의 밸류에이션 5종을 룰 기반 합성점수로 만들어
체결가능 순기대 A/B(fillable_topk_expectancy)에 넘길 dump(jsonl) 2개를 만든다.

왜: CG407 스크린(2026-11-22)에서 panel_prod200_v6(200종목·221피처)의 시간가변 피처 중
    일관되게 살아 있는 것은 밸류에이션 군뿐이었다 —
    value_psr IC -0.0293(t -5.82, 309일) · value_pcr -0.0263(t -5.28) · value_pbr -0.0221(t -3.70)
    · value_ev_ebit -0.0159(t -2.78) · value_pfcr -0.0173(t -4.28) (전부 fill 1.0, 종목상수 아님).
    IC 부호가 일관되게 '비쌀수록 미래수익 낮다' → 저평가(cheap) 상위가 돈이 되는지 직접 잰다.
    AUC(모델)가 아니라 룰 점수로 재는 이유: 모델 학습 없이 그 피처군 자체에 돈 엣지가 있는지
    먼저 가르기 위해서다(엣지 0 이면 모델을 얹어도 기대 이득 0).

점수: 날짜별 횡단면 백분위 순위의 평균(1 − rank_pct) → cheap 이 높다.
      유효 피처 3개 이상일 때만 점수 부여. 결측은 그 날짜 횡단면에서 제외.
대조군: 같은 (date, code) 의 결정적 해시 난수 점수(널) — 순위 무작위화.

사용(컨테이너 내부):
  python -u scripts/cg408_value_money.py --panel /app/app/models/wf/panel_prod200_v6.npz \
      --out-arm /app/reports/overnight/cg408_value_arm.jsonl \
      --out-control /app/reports/overnight/cg408_value_null.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys

import numpy as np

VALUE_FEATURES = ["value_pbr", "value_psr", "value_pcr", "value_pfcr", "value_ev_ebit"]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", required=True)
    ap.add_argument("--features", default=",".join(VALUE_FEATURES))
    ap.add_argument("--min-valid", type=int, default=3, help="점수 부여에 필요한 유효 피처 수")
    ap.add_argument("--min-fill", type=float, default=0.5, help="날짜별 최소 채움률")
    ap.add_argument("--invert", action="store_true",
                    help="점수 방향 반전(기본: 값이 낮을수록 높은 점수=cheap/저변동. 투입: 값이 높을수록 높은 점수)")
    ap.add_argument("--tag-arm", default="cash408_value_cheap")
    ap.add_argument("--tag-control", default="cash408_null_hash")
    ap.add_argument("--out-arm", required=True)
    ap.add_argument("--out-control", required=True)
    a = ap.parse_args(argv)

    d = np.load(a.panel, allow_pickle=True)
    X = d["X"]
    fn = [str(x) for x in d["feature_names"]]
    dates = np.array([str(x) for x in d["dates"]])
    codes = np.array([str(x).zfill(6) for x in d["codes"]])

    feats = [f.strip() for f in a.features.split(",") if f.strip()]
    idx = []
    for f in feats:
        if f not in fn:
            print(f"[FAIL] 피처 없음: {f}", file=sys.stderr)
            return 2
        idx.append(fn.index(f))
    sub = X[:, idx].astype(np.float64)

    uniq = sorted(set(dates.tolist()))
    fold_of = {dt: (1 if i < len(uniq) / 2 else 2) for i, dt in enumerate(uniq)}

    n_arm = n_ctl = 0
    skipped_dates = 0
    stats = []
    with open(a.out_arm, "w", encoding="utf-8") as fa, open(a.out_control, "w", encoding="utf-8") as fc:
        for dt in uniq:
            m = dates == dt
            block = sub[m]
            cs = codes[m]
            avail = np.isfinite(block)
            fill = avail.mean(axis=0)
            usable = [j for j in range(len(idx)) if fill[j] >= a.min_fill]
            if len(usable) < a.min_valid:
                skipped_dates += 1
                continue
            rank_sum = np.zeros(len(block), dtype=np.float64)
            cnt = np.zeros(len(block), dtype=np.int64)
            for j in usable:
                col = block[:, j]
                ok = np.isfinite(col)
                if ok.sum() < 10:
                    continue
                order = col[ok].argsort().argsort().astype(np.float64)
                pct = order / max(ok.sum() - 1, 1)          # 0=최소 → 1=최대
                r = np.full(len(block), np.nan)
                r[ok] = pct if a.invert else (1.0 - pct)     # 기본: 값 낮을수록 높은 점수
                rank_sum += np.where(np.isfinite(r), r, 0.0)
                cnt += ok.astype(np.int64)
            score = np.where(cnt >= a.min_valid, rank_sum / np.maximum(cnt, 1), np.nan)
            ok_rows = np.isfinite(score)
            if ok_rows.sum() < 10:
                skipped_dates += 1
                continue
            stats.append((dt, int(ok_rows.sum()), int(len(usable)), float(np.nanstd(score[ok_rows]))))
            for c_, s_ in zip(cs[ok_rows], score[ok_rows]):
                rec = {"exp": a.tag_arm, "fold": fold_of[dt], "date": dt, "code": c_,
                       "y_true": None, "y_pred": round(float(s_), 8), "fwd_ret": None}
                fa.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n_arm += 1
                h = hashlib.sha256(f"{dt}|{c_}|cg408".encode()).hexdigest()
                null = int(h[:12], 16) / float(16 ** 12)
                rec2 = {"exp": a.tag_control, "fold": fold_of[dt], "date": dt, "code": c_,
                        "y_true": None, "y_pred": round(null, 8), "fwd_ret": None}
                fc.write(json.dumps(rec2, ensure_ascii=False) + "\n")
                n_ctl += 1

    print(f"패널 {a.panel} · 피처 {feats} · 날짜 {len(stats)}개(건너뜀 {skipped_dates}) · "
          f"arm행 {n_arm} · control행 {n_ctl}")
    if stats:
        print(f"날짜범위 {stats[0][0]}~{stats[-1][0]} · 날짜당 중앙 종목수 {int(np.median([s[1] for s in stats]))}"
              f" · 사용피처 중앙 {int(np.median([s[2] for s in stats]))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

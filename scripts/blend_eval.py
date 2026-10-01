#!/usr/bin/env python3
"""blend_eval.py — 라벨 다양성 앙상블(두 모델) 짝 평가기 (2026-10-01 CG56, 이 역할 소유).

왜 필요한가
-----------
조정 축(피처변환·HP·유니버스·라벨·창·가중·정규화·k·부활 데이터)이 전부 사전문턱 +0.02 미달로
닫힌 상태에서, **아직 시험되지 않은 축**은 '서로 다른 라벨로 학습한 두 모델의 결합'이다.
배포 챔피언은 절대 h1 방향 라벨, cand_cg51 은 시장상대 h5 라벨로 학습됐고 CG53 실측에서
챔피언 0.5139±0.0063 vs 후보 0.5272±0.0124 (10시드 짝 Δ+0.0133, SE 0.0052) 였다 —
즉 두 모델은 같은 피처풀을 쓰지만 **오차가 완전히 겹치지 않는다**. 두 점수를 결합하면
분산이 줄어 문턱을 넘을 수 있는지가 이 실험의 질문이다.

규약 (사전 등록, 자기신고 금지)
-------------------------------
- 입력은 `champion_robust_eval.py --dump-preds` 가 남긴 jsonl (exp, fold, date, code, y_true,
  y_pred, fwd_ret). 두 모델을 **같은 런·같은 시드·같은 창**에서 덤프해야 짝이 성립한다.
- 결합은 **rank-평균**: (fold,date) 안에서 각 모델의 예측을 순위화한 뒤 두 순위의 평균을 쓴다.
  확률 스케일이 다른 두 모델(절대 h1 vs 시장상대 h5)을 그대로 평균하면 스케일이 큰 쪽이
  지배하므로 순위 기반 결합만 인정한다.
- 판정은 시드 단위 짝 Δ(blend − champ) 의 평균·SE·t·양(+) 시드 수와, 같은 런의 cand 대비 Δ.
- 창(폴드) 수가 부족하면 '창 수 부족' 을 함께 출력한다(CG38 교훈: 3창이면 SE 0.08 → 판정 불가).

사용 예
  python3 /app/scripts/blend_eval.py --dir /app/reports --prefix cg56 --seeds 0-9 \
      --champ-tag champion --cand-tag cand_cg51 --out /app/reports/cg56_summary.json
"""
import argparse
import json
import math
import os
import sys
from collections import defaultdict


def load_dump(path):
    """(fold, date, code) → (y_true, y_pred) 사전."""
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            key = (int(r.get("fold", 0)), str(r.get("date")), str(r.get("code")))
            y = r.get("y_true")
            if y is None or float(y) != float(y):  # NaN 라벨 제외
                continue
            out[key] = (float(y), float(r.get("y_pred", 0.0)), r.get("fwd_ret"))
    return out


def ranks_within(rows):
    """(key, score) 목록 → key → 순위(0..n-1, 동점은 평균순위)."""
    order = sorted(rows, key=lambda kv: kv[1])
    rank = {}
    i = 0
    n = len(order)
    while i < n:
        j = i
        while j + 1 < n and order[j + 1][1] == order[i][1]:
            j += 1
        avg = (i + j) / 2.0
        for k in range(i, j + 1):
            rank[order[k][0]] = avg
        i = j + 1
    return rank


def auc(y, s):
    """Mann-Whitney AUC (동점은 0.5 취급)."""
    pos = [ss for yy, ss in zip(y, s) if yy == 1]
    neg = [ss for yy, ss in zip(y, s) if yy != 1]
    if not pos or not neg:
        return None
    order = sorted(range(len(s)), key=lambda i: s[i])
    r = [0.0] * len(s)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and s[order[j + 1]] == s[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            r[order[k]] = avg
        i = j + 1
    rsum_pos = sum(r[i] for i in range(len(s)) if y[i] == 1)
    npos, nneg = len(pos), len(neg)
    return (rsum_pos - npos * (npos + 1) / 2.0) / (npos * nneg)


def window_aucs(champ, cand):
    """(fold, date) 별로 date 내 순위평균 결합 → fold 별 pooled AUC 3종."""
    by_fold_date = defaultdict(list)
    for key in champ:
        if key not in cand:
            continue
        fold, date, code = key
        by_fold_date[(fold, date)].append(key)
    blends = {}
    for (fold, date), keys in by_fold_date.items():
        rc = ranks_within([(k, champ[k][1]) for k in keys])
        rd = ranks_within([(k, cand[k][1]) for k in keys])
        for k in keys:
            blends[k] = (rc[k] + rd[k]) / 2.0
    per_fold = defaultdict(lambda: {"y": [], "a": [], "b": [], "c": [], "ret_a": [], "ret_b": [], "ret_c": []})
    for key, (y, pa, _fwd) in champ.items():
        if key not in cand:
            continue
        fold = key[0]
        pb = cand[key][1]
        per_fold[fold]["y"].append(y)
        per_fold[fold]["a"].append(pa)
        per_fold[fold]["b"].append(pb)
        per_fold[fold]["c"].append(blends[key])
    out = {}
    for fold, d in per_fold.items():
        if len(d["y"]) < 50:
            continue
        out[fold] = {
            "n": len(d["y"]),
            "champ": auc(d["y"], d["a"]),
            "cand": auc(d["y"], d["b"]),
            "blend": auc(d["y"], d["c"]),
        }
    return out


def agg(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None, None
    m = sum(vals) / len(vals)
    if len(vals) < 2:
        return m, None
    var = sum((v - m) ** 2 for v in vals) / (len(vals) - 1)
    return m, math.sqrt(var)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="dump jsonl 들이 있는 디렉터리(컨테이너 경로)")
    ap.add_argument("--prefix", required=True, help="dump 파일 접두어 (예: cg56)")
    ap.add_argument("--seeds", default="0-9", help="예: 0-9 또는 0,1,2")
    ap.add_argument("--champ-tag", default="champion")
    ap.add_argument("--cand-tag", default="cand")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    seeds = []
    for part in a.seeds.split(","):
        if "-" in part:
            lo, hi = part.split("-")
            seeds.extend(range(int(lo), int(hi) + 1))
        else:
            seeds.append(int(part))

    per_seed = []
    for s in seeds:
        fc = os.path.join(a.dir, f"{a.prefix}_{a.champ_tag}_s{s}.jsonl")
        fd = os.path.join(a.dir, f"{a.prefix}_{a.cand_tag}_s{s}.jsonl")
        if not (os.path.exists(fc) and os.path.exists(fd)):
            print(f"시드 {s}: 덤프 없음 ({fc} / {fd}) — 건너뜀", file=sys.stderr)
            continue
        champ, cand = load_dump(fc), load_dump(fd)
        common = set(champ) & set(cand)
        if len(common) < 200:
            print(f"시드 {s}: 공통 키 {len(common)}행 — 부족, 건너뜀", file=sys.stderr)
            continue
        wa = window_aucs(champ, cand)
        if not wa:
            print(f"시드 {s}: 유효 창 없음 — 건너뜀", file=sys.stderr)
            continue
        rows = sorted(wa)
        per_seed.append({
            "seed": s,
            "n_windows": len(rows),
            "rows": sum(wa[f]["n"] for f in rows),
            "champ": sum(wa[f]["champ"] for f in rows) / len(rows),
            "cand": sum(wa[f]["cand"] for f in rows) / len(rows),
            "blend": sum(wa[f]["blend"] for f in rows) / len(rows),
            "per_window": {str(f): wa[f] for f in rows},
        })
        print(json.dumps(per_seed[-1], ensure_ascii=False))

    if not per_seed:
        print(json.dumps({"error": "판정불가 — 유효 시드 없음"}, ensure_ascii=False))
        return 1

    blend = [r["blend"] for r in per_seed]
    champ = [r["champ"] for r in per_seed]
    cand = [r["cand"] for r in per_seed]
    d_bc = [b - c for b, c in zip(blend, champ)]
    d_bd = [b - c for b, c in zip(blend, cand)]
    mb, sb = agg(blend)
    mbc, sbc = agg(d_bc)
    mbd, sbd = agg(d_bd)
    n = len(per_seed)
    se = (sbc / math.sqrt(n)) if (sbc is not None and n > 1) else None
    t = (mbc / se) if (se and se > 0) else None
    sbd_se = (sbd / math.sqrt(n)) if (sbd is not None and n > 1) else None
    windows = min(r["n_windows"] for r in per_seed)
    summary = {
        "measured_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat(),
        "model_dir": f"blend(rank-avg)={a.champ_tag}+{a.cand_tag}",
        "protocol": (f"rank-avg blend of two label-diverse models · same run/seed/windows · "
                     f"{windows}창 · {n}시드 (champion_robust_eval --dump-preds)"),
        "metric_name": "blend_auc",
        "robust_auc": round(mb, 6) if mb is not None else None,
        "auc_std_across_folds": round(sb, 6) if sb is not None else None,
        "fold_means": [round(x, 6) for x in blend],
        "windows": [{"seed": r["seed"], "n_windows": r["n_windows"], "rows": r["rows"],
                     "champ": round(r["champ"], 6), "cand": round(r["cand"], 6),
                     "blend": round(r["blend"], 6)} for r in per_seed],
        "auc_pooled": None,
        "auc_per_date_mean": None,
        "rows_scored": sum(r["rows"] for r in per_seed),
        "dates_scored": None,
        "errors": [] if n >= 5 else ["시드 수 부족"],
        "n_seeds": n, "n_windows": windows,
        "champ_mean": round(agg(champ)[0], 6) if agg(champ)[0] is not None else None,
        "cand_mean": round(agg(cand)[0], 6) if agg(cand)[0] is not None else None,
        "paired": {
            "delta_blend_minus_champ_mean": round(mbc, 6) if mbc is not None else None,
            "se": round(se, 6) if se is not None else None,
            "t": round(t, 3) if t is not None else None,
            "pos_seeds": f"{sum(1 for x in d_bc if x > 0)}/{n}",
            "samples": [round(x, 6) for x in d_bc],
            "delta_blend_minus_cand_mean": round(mbd, 6) if mbd is not None else None,
            "se_vs_cand": round(sbd_se, 6) if sbd_se is not None else None,
            "pos_seeds_vs_cand": f"{sum(1 for x in d_bd if x > 0)}/{n}",
            "threshold": 0.02,
            "note": ("창 수·시드 수가 부족하면 판정 불가로 읽어라 — 3창 10시드 SE 0.005 수준은 "
                     "CG53 실측 기준. 배포에는 추론 경로 변경(두 모델 확률 평균) 승인이 필요하다."),
        },
        "arms": {
            "champ": {"model": a.champ_tag, "mean": round(agg(champ)[0], 6) if agg(champ)[0] is not None else None},
            "cand": {"model": a.cand_tag, "mean": round(agg(cand)[0], 6) if agg(cand)[0] is not None else None},
            "blend": {"method": "rank-avg within (fold,date)", "mean": round(mb, 6) if mb is not None else None},
        },
    }
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""precision@k · 기대수익 집계기 (2026-09-28 CG21 전용, 이 역할 소유).

왜 필요한가 — AUC 는 순위 지표라 **양성의 정의가 바뀌어도 값이 비교된다**. 라벨 꼬리를
q0.30 → q0.05 로 좁히면 "상위 5% vs 하위 5%"를 가르는 과제가 되어 AUC 가 올라가지만,
그 상승이 (a) 과제가 쉬워진 것인지 (b) 실제로 매수 대상(상위 k)을 더 잘 고르게 된 것인지는
AUC 만으로 판정할 수 없다. 트레이더가 사는 것은 상위 k 뿐이므로 최종 지표는 정밀도@k 와
바스켓 기대수익이다.

입력: `wf_label_sweep.py --dump-preds <path>` 가 남긴 jsonl
      (exp, fold, date, code, y_true, y_pred, fwd_ret).

판정 규약(사전 등록):
  - 짝 비교는 (fold, date) 단위. 평균 Δ 와 부호검정(양수 일수 / 전체) 을 함께 보고한다.
  - 라벨 q 가 다른 두 arm 을 비교할 때는 **반드시 --restrict-q 로 공통 후보집합**을 만든다.
    각 arm 의 라벨 통과 행 집합이 다르면 '상위 k 정밀도'의 분모가 달라져 비교가 성립하지 않는다.
  - 표준출력은 자기신고다. 판정용 수치는 --json-out 파일로도 남겨 재검증 가능하게 한다.

사용 예:
  python3 scripts/topk_precision.py /app/scripts/_preds_CG21.jsonl \
      --k 3,5,10 --arm CO_q05_h5 --control CO_core30_h5 --restrict-q 0.05 \
      --json-out data/reports/me_cycle/topk_CG21.json
"""
import argparse
import json
import math
import os
from collections import defaultdict


def load_rows(path):
    rows = []
    bad = 0
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                bad += 1
    return rows, bad


def quantile(vals, q):
    """선형보간 분위수(통계 라이브러리 의존 없이). vals 는 비어 있지 않아야 한다."""
    xs = sorted(vals)
    if len(xs) == 1:
        return xs[0]
    pos = q * (len(xs) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return xs[lo] * (1.0 - frac) + xs[hi] * frac


def binom_p_two_sided(k, n, p=0.5):
    """정확 이항검정(양측) — 부호검정 p값."""
    if n == 0:
        return None
    def pmf(i):
        return math.comb(n, i) * (p ** i) * ((1 - p) ** (n - i))
    obs = pmf(k)
    tol = obs * (1 + 1e-9)
    return min(1.0, sum(pmf(i) for i in range(n + 1) if pmf(i) <= tol))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("preds", help="--dump-preds 로 저장한 jsonl 경로")
    ap.add_argument("--k", default="3,5,10", help="쉼표 구분 상위 k (기본 3,5,10)")
    ap.add_argument("--arm", default=None, help="실험군 exp 이름 (미지정 시 최고 AUC 추정 불가 → 첫 exp)")
    ap.add_argument("--control", default=None, help="대조군 exp 이름")
    ap.add_argument("--restrict-q", type=float, default=None,
                    help="공통 후보집합: (fold,date) 별 fwd_ret 양쪽 꼬리 q 만 남겨 모든 arm 을 "
                         "같은 후보 위에서 채점한다(라벨 q 가 다른 arm 비교 시 필수)")
    ap.add_argument("--min-pool", type=int, default=20,
                    help="공통집합을 만들 때 (fold,date) 당 최소 후보 수 — 이보다 적으면 "
                         "분위 추정이 무의미해 건너뛴다(기본 20)")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    ks = [int(x) for x in args.k.split(",") if x.strip()]
    rows, bad = load_rows(args.preds)
    if not rows:
        print(f"레코드 없음: {args.preds}")
        return 1

    exps = []
    for r in rows:
        e = str(r.get("exp"))
        if e not in exps:
            exps.append(e)
    if args.arm and args.arm not in exps:
        print(f"--arm {args.arm} 이 덤프에 없다. 있는 exp: {exps}")
        return 1
    arm = args.arm or exps[0]
    control = args.control if (args.control in exps) else None

    # index[(exp)][(fold,date)][code] = (y_true, y_pred, fwd_ret)
    idx = defaultdict(lambda: defaultdict(dict))
    for r in rows:
        try:
            key = (int(r["fold"]), str(r["date"]))
            idx[str(r["exp"])][key][str(r["code"])] = (
                int(r["y_true"]), float(r["y_pred"]),
                float(r["fwd_ret"]) if r.get("fwd_ret") is not None else float("nan"))
        except Exception:
            continue

    # 공통 후보집합(선택): (fold,date) 별 fwd_ret 양쪽 꼬리만 유지
    kept_keys = defaultdict(lambda: defaultdict(set))
    if args.restrict_q is not None:
        q = float(args.restrict_q)
        all_keys = set()
        for e in idx:
            all_keys |= set(idx[e].keys())
        for key in all_keys:
            pool = {}
            for e in idx:
                for code, (_, _, fwd) in idx[e].get(key, {}).items():
                    if fwd == fwd:      # NaN 제외
                        pool[code] = fwd
            if len(pool) < args.min_pool:
                continue
            vals = list(pool.values())
            lo, hi = quantile(vals, q), quantile(vals, 1 - q)
            sel = {c for c, v in pool.items() if v <= lo or v >= hi}
            if not sel:
                continue
            for e in idx:
                s = {c for c in idx[e].get(key, {}) if c in sel}
                if s:
                    kept_keys[e][key] = s

    def basket(e, key, k):
        items = idx[e].get(key, {})
        keep = kept_keys[e].get(key) if args.restrict_q is not None else None
        cand = [(c, v) for c, v in items.items() if (keep is None or c in keep)]
        if len(cand) < k:
            return None
        cand.sort(key=lambda x: -x[1][1])          # y_pred 내림차순
        top = cand[:k]
        prec = sum(1 for _, v in top if v[0] == 1) / float(k)
        rets = [v[2] for _, v in top if v[2] == v[2]]
        return prec, (sum(rets) / len(rets) if rets else None)

    per_exp = {}
    for e in exps:
        agg = {k: {"prec_dates": [], "prec_folds": defaultdict(list),
                   "ret_dates": [], "ret_folds": defaultdict(list)} for k in ks}
        pool_sizes = []
        for key in idx[e]:
            items = idx[e].get(key, {})
            keep = kept_keys[e].get(key) if args.restrict_q is not None else None
            pool_sizes.append(sum(1 for c in items if (keep is None or c in keep)))
            for k in ks:
                b = basket(e, key, k)
                if b is None:
                    continue
                prec, ret = b
                agg[k]["prec_dates"].append(prec)
                agg[k]["prec_folds"][key[0]].append(prec)
                if ret is not None:
                    agg[k]["ret_dates"].append(ret)
                    agg[k]["ret_folds"][key[0]].append(ret)
        out = {}
        for k in ks:
            a = agg[k]
            pf = [sum(v) / len(v) for v in a["prec_folds"].values() if v]
            rf = [sum(v) / len(v) for v in a["ret_folds"].values() if v]
            out[k] = {
                "n_dates": len(a["prec_dates"]),
                "prec_mean": (sum(a["prec_dates"]) / len(a["prec_dates"])
                              if a["prec_dates"] else None),
                "prec_fold_mean": (sum(pf) / len(pf) if pf else None),
                "prec_fold_std": (math.sqrt(sum((x - sum(pf) / len(pf)) ** 2 for x in pf) / len(pf))
                                  if pf else None),
                "ret_mean": (sum(a["ret_dates"]) / len(a["ret_dates"])
                             if a["ret_dates"] else None),
                "ret_fold_mean": (sum(rf) / len(rf) if rf else None),
            }
        # 후보 풀 크기: k 가 풀 크기 이상이면 상위 k 선택이 무의미해진다(포화).
        out["pool_median"] = (sorted(pool_sizes)[len(pool_sizes) // 2]
                              if pool_sizes else None)
        per_exp[e] = out

    # ── 짝 비교: (fold,date) 단위 arm − control ────────────────────────────────
    # ⚠ 정밀도는 이산 지표라 동점(Δ=0)이 흔하다. 동점을 '음수'로 세면 부호검정이
    #   거짓 유의가 된다(실측: k=10 에서 전부 동점 → p=0.0000 이라는 무의미한 값).
    #   → 부호검정은 **동점 제외**(n_eff)로 하고, 동점 수를 함께 보고한다.
    paired = {}
    if control:
        for k in ks:
            diffs_p, diffs_r, folds_pos = [], [], defaultdict(int)
            folds_n = defaultdict(int)
            ties = 0
            for key in idx[arm]:
                if key not in idx[control]:
                    continue
                b1, b2 = basket(arm, key, k), basket(control, key, k)
                if not b1 or not b2:
                    continue
                diffs_p.append(b1[0] - b2[0])
                if b1[1] is not None and b2[1] is not None:
                    diffs_r.append(b1[1] - b2[1])
                folds_n[key[0]] += 1
                if b1[0] > b2[0]:
                    folds_pos[key[0]] += 1
                if b1[0] == b2[0]:
                    ties += 1
            n = len(diffs_p)
            n_eff = n - ties
            pos = sum(1 for d in diffs_p if d > 0)
            paired[k] = {
                "n_dates": n,
                "n_eff_sign": n_eff,
                "ties": ties,
                "prec_delta_mean": (sum(diffs_p) / n if n else None),
                "prec_delta_pos": pos,
                "prec_sign_p": binom_p_two_sided(pos, n_eff) if n_eff else None,
                "ret_delta_mean": (sum(diffs_r) / len(diffs_r) if diffs_r else None),
                "folds": {str(f): f"{folds_pos[f]}/{folds_n[f]}"
                          for f in sorted(folds_n)},
            }

    # ── 출력 ──────────────────────────────────────────────────────────────────
    print(f"preds={args.preds} rows={len(rows)} (파싱실패 {bad}) exps={exps}")
    mode = (f"공통 후보집합(양쪽 꼬리 q={args.restrict_q})" if args.restrict_q is not None
            else "각 arm 자체 후보집합(라벨 q 가 다르면 비교 금지)")
    print(f"채점 기준: {mode}")
    print(f"{'exp':16s} {'k':>3s} {'prec@k':>8s} {'prec_fold±std':>20s} {'fwd_ret':>10s} {'n':>7s}")
    for e in exps:
        for k in ks:
            o = per_exp[e][k]
            pm = f"{o['prec_mean']:.4f}" if o["prec_mean"] is not None else "n/a"
            if o["prec_fold_mean"] is not None:
                fs = f"{o['prec_fold_mean']:.4f}±{o['prec_fold_std']:.4f}"
            else:
                fs = "n/a"
            rm = f"{o['ret_mean']:+.4f}" if o["ret_mean"] is not None else "n/a"
            print(f"{e:16s} {k:3d} {pm:>8s} {fs:>20s} {rm:>10s} {o['n_dates']:7d}")
    for e in exps:
        print(f"  후보 풀 중앙값 {e}: {per_exp[e]['pool_median']} "
              f"(k 가 이 값 이상이면 포화 — 그 k 의 Δ 는 해석하지 말 것)")
    if paired:
        print(f"\n=== 짝 비교 {arm} − {control} ((fold,date) 단위) ===")
        for k in ks:
            p = paired[k]
            if p["prec_delta_mean"] is None:
                print(f"  k={k}: 측정 짝 없음")
                continue
            rm = (f"{p['ret_delta_mean']:+.4f}" if p["ret_delta_mean"] is not None else "n/a")
            ps = (f"{p['prec_sign_p']:.4f}" if p["prec_sign_p"] is not None else "n/a")
            print(f"  k={k}: Δprec {p['prec_delta_mean']:+.4f} "
                  f"(양수 {p['prec_delta_pos']}/{p['n_eff_sign']} 동점제외, 동점 {p['ties']}, "
                  f"부호검정 p={ps}) "
                  f"· Δfwd_ret {rm} · 폴드별 양수 {p['folds']}")

    if args.json_out:
        _d = os.path.dirname(args.json_out)
        if _d:
            os.makedirs(_d, exist_ok=True)
        with open(args.json_out, "w") as f:
            json.dump({"preds": args.preds, "rows": len(rows), "exps": exps,
                       "restrict_q": args.restrict_q, "arm": arm, "control": control,
                       "per_exp": per_exp, "paired": paired}, f,
                      ensure_ascii=False, indent=2)
        print(f"\njson: {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

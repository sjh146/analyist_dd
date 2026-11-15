"""체결가능 부분집합에서의 AUC — 라벨 꼬리(q0.05) 이득의 체결성 분해 (CG294).

왜: CG292 실측에서 q0.05 는 생산경로 AUC +0.067(8/10)인데 돈은 Δ−1.72%p(k=3, t −1.24)로
반대였다. 한 가설은 'AUC 이득이 체결 불가(저유동·상한가) 종목에서 나온다'이다 —
fillable_topk_expectancy 는 매수 바스켓을 **체결가능 행으로 먼저 거른 뒤** top-k 를 뽑으므로,
AUC 이득이 필터 밖 종목에 몰려 있으면 돈으로는 0 이 된다.

측정: 각 arm 의 라벨 집합에서 날짜별 횡단면 AUC 를 (a) 전체 행 (b) 체결가능 행만 으로 각각 계산해
폴드 평균을 낸다. 필터 파라미터는 fillable_topk_expectancy 기본값과 동일(1e9·day_chg<25·상한가 제외).

출력 스키마는 wf_sweep_summary 와 동일(results[].exp/desc/kind/horizon/folds{}.mean) —
구동기 판정기(judge_per)가 그대로 읽는다.
"""
import argparse
import importlib.util
import json
import os
import statistics as st
from collections import defaultdict

spec = importlib.util.spec_from_file_location("fte", "/app/scripts/fillable_topk_expectancy.py")
fte = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fte)


def auc_pairs(pairs):
    """pairs=[(score,label)] → AUC (Mann-Whitney, 동점 0.5). 라벨 두 클래스 필요."""
    n1 = sum(1 for _, y in pairs if y == 1)
    n0 = sum(1 for _, y in pairs if y == 0)
    if n1 == 0 or n0 == 0:
        return None
    order = sorted(range(len(pairs)), key=lambda i: pairs[i][0])
    ranks = [0.0] * len(pairs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and pairs[order[j + 1]][0] == pairs[order[i]][0]:
            j += 1
        r = (i + j) / 2.0 + 1.0   # 동점은 평균 순위
        for t in range(i, j + 1):
            ranks[order[t]] = r
        i = j + 1
    rank_sum_pos = sum(ranks[i] for i in range(len(pairs)) if pairs[i][1] == 1)
    return (rank_sum_pos - n1 * (n1 + 1) / 2.0) / (n1 * n0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preds", required=True, help="jsonl (exp/fold/date/code/y_true/y_pred)")
    ap.add_argument("--tags", default="cg292_q05,cg292_q30")
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--out", required=True, help="요약 JSON (wf_sweep_summary 스키마)")
    a = ap.parse_args()

    rows = []
    with open(a.preds, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("y_true") is None or r.get("y_pred") is None:
                continue
            rows.append({"exp": r["exp"], "fold": int(r.get("fold") or 0), "date": str(r["date"]),
                         "code": str(r["code"]).zfill(6), "y": int(r["y_true"]), "score": float(r["y_pred"])})
    if not rows:
        print("[FAIL] 라벨 있는 행 없음", flush=True)
        return 2
    tags = [t.strip() for t in a.tags.split(",") if t.strip()]
    codes = sorted({r["code"] for r in rows})
    dmin, dmax = min(r["date"] for r in rows), max(r["date"] for r in rows)
    conn = fte.db_connect()
    series = fte.price_series(conn, codes, dmin, dmax)
    # 체결성 플래그: enrich 가 붙이는 close/day_chg_pct/trading_value 를 그대로 재사용
    enr, drop = fte.enrich([{"code": r["code"], "date": r["date"], "fold": r["fold"],
                             "score": r["score"], "y": r["y"], "exp": r["exp"]} for r in rows],
                           series, a.horizon, "close_h")
    print(f"labeled rows={len(rows)} enriched={len(enr)} dropped={drop}", flush=True)

    import argparse as _ap
    ns = _ap.Namespace(exclude_limit_up=True, max_day_chg=25.0, min_value=1e9,
                       min_price=None, max_price=None)
    kept_idx = set()
    fill_ok = []
    for i, r in enumerate(enr):
        ok, _ = fte.apply_filters([r], ns, True)
        if ok:
            kept_idx.add(i)
            fill_ok.append(r)

    results = []
    per_arm = {}
    for tag in tags:
        arm_rows = [(i, r) for i, r in enumerate(enr) if r["exp"] == tag]
        for cond, sel in (("all", arm_rows), ("fillable", [(i, r) for i, r in arm_rows if i in kept_idx])):
            by_date = defaultdict(list)
            for _, r in sel:
                by_date[(r["fold"], r["date"])].append((r["score"], r["y"]))
            fold_aucs = defaultdict(list)
            for (fold, d), pairs in by_date.items():
                v = auc_pairs(pairs)
                if v is not None:
                    fold_aucs[fold].append(v)
            if not fold_aucs:
                continue
            means = {}
            for fold in sorted(fold_aucs):
                means[str(fold)] = {"mean": round(st.mean(fold_aucs[fold]), 4),
                                    "n_dates": len(fold_aucs[fold])}
            allmeans = [v["mean"] for v in means.values()]
            results.append({"exp": f"{tag}@{cond}", "desc": f"{tag} {'체결가능 행만' if cond=='fillable' else '전체 행'}",
                            "kind": "robust_auc", "horizon": a.horizon, "folds": means})
            per_arm[(tag, cond)] = allmeans
            print(f"{tag}@{cond}: folds={len(allmeans)} mean {st.mean(allmeans):.4f} "
                  f"±{st.pstdev(allmeans):.4f} min {min(allmeans):.4f} max {max(allmeans):.4f} "
                  f"rows={len(sel)}", flush=True)

    out = {"finished_at": None, "config": {"preds": a.preds, "horizon": a.horizon,
                                           "fillable_filter": {"min_value": 1e9, "max_day_chg": 25.0,
                                                               "exclude_limit_up": True}},
           "results": results}
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("wrote", a.out, flush=True)

    # 요약: arm 내부 (전체 − 체결가능) 낙폭 · arm 간(체결가능) 짝 Δ
    for tag in tags:
        if (tag, "all") in per_arm and (tag, "fillable") in per_arm:
            d = st.mean(per_arm[(tag, "fillable")]) - st.mean(per_arm[(tag, "all")])
            print(f"[분해] {tag}: 체결가능 − 전체 = {d:+.4f}")
    if len(tags) >= 2:
        t0, t1 = tags[0], tags[1]
        if (t0, "fillable") in per_arm and (t1, "fillable") in per_arm:
            d = [x - y for x, y in zip(per_arm[(t0, "fillable")], per_arm[(t1, "fillable")])]
            print(f"[짝Δ 체결가능] {t0}−{t1}: {st.mean(d):+.4f} ±{st.pstdev(d):.4f} "
                  f"승 {sum(1 for x in d if x > 0)}/{len(d)}")
        if (t0, "all") in per_arm and (t1, "all") in per_arm:
            d = [x - y for x, y in zip(per_arm[(t0, "all")], per_arm[(t1, "all")])]
            print(f"[짝Δ 전체] {t0}−{t1}: {st.mean(d):+.4f} ±{st.pstdev(d):.4f} "
                  f"승 {sum(1 for x in d if x > 0)}/{len(d)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

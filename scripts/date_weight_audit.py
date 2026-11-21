#!/usr/bin/env python3
"""날짜 가중 오염 감사 — pooled AUC vs 날짜균등 AUC.

왜 필요한가(2026-11-22 CG402 실측): 전방 성적표의 headline pooled AUC 0.5638 은
champ 8일 + v1.0 9일을 한 풀에 넣은 값이었고, champ 단독으로 자르면 pooled 0.5409 로
−0.0229 내려앉는데 날짜별 평균은 0.5158 → 0.5151 로 거의 그대로였다. 즉 pooled 는
**행수 많은 날짜의 AUC 를 더 무겁게** 센다. 우리 판정 프로토콜(폴드 AUC)도 같은
집계를 쓰므로, 폴드 Δ 가 +0.02 문턱 근처일 때 '날짜 가중' 이 얼마나 섞여 있는지
알아야 한다.

사용:
  python3 scripts/date_weight_audit.py <preds.jsonl> [<preds.jsonl> ...] [--json-out PATH]
  (컨테이너 안에서도 동작: python3 scripts/date_weight_audit.py /app/reports/overnight/x.jsonl)

출력: 파일별·폴드별로
  pooled  = 전체 행 AUC (현 프로토콜)
  eqdate  = 날짜별 AUC 의 단순평균 (행수 무시)
  diff    = pooled − eqdate
  날짜 행수 min/max, 유효 날짜수
의존성 없음(순수 파이썬, AUC 는 rank 기반 Mann-Whitney).
"""
import json
import sys
from collections import defaultdict


def auc_rank(pairs):
    """(y_true, y_pred) 리스트의 AUC — 동점은 평균순위로 처리."""
    n_pos = sum(1 for y, _ in pairs if y == 1)
    n_neg = len(pairs) - n_pos
    if n_pos == 0 or n_neg == 0:
        return None
    order = sorted(pairs, key=lambda t: t[1])
    ranks = [0.0] * len(order)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and order[j + 1][1] == order[i][1]:
            j += 1
        avg = (i + j) / 2.0 + 1.0  # 1-based 평균순위
        for k in range(i, j + 1):
            ranks[k] = avg
        i = j + 1
    sum_pos = sum(r for r, (y, _) in zip(ranks, order) if y == 1)
    return (sum_pos - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def load(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            y = d.get("y_true")
            p = d.get("y_pred")
            dt = d.get("date")
            if y is None or p is None or dt is None:
                continue
            rows.append((str(dt), int(y), float(p), d.get("fold")))
    return rows


def summarize(rows, label):
    """rows: (date, y, pred, fold) 리스트."""
    out = {"label": label, "rows": len(rows)}
    pooled = auc_rank([(y, p) for _, y, p, _ in rows])
    by_date = defaultdict(list)
    for dt, y, p, _ in rows:
        by_date[dt].append((y, p))
    per_date = {dt: auc_rank(v) for dt, v in by_date.items()}
    per_date = {k: v for k, v in per_date.items() if v is not None}
    out["pooled_auc"] = round(pooled, 4) if pooled is not None else None
    out["eqdate_auc"] = round(sum(per_date.values()) / len(per_date), 4) if per_date else None
    out["n_dates"] = len(per_date)
    out["n_dates_dropped"] = len(by_date) - len(per_date)
    counts = [len(v) for v in by_date.values()]
    out["rows_per_date_minmax"] = [min(counts), max(counts)] if counts else None
    if pooled is not None and per_date:
        out["diff"] = round(pooled - out["eqdate_auc"], 4)
    return out


def main(argv):
    files, json_out = [], None
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == "--json-out":
            i += 1
            json_out = argv[i]
        elif a.startswith("--"):
            print(f"unknown flag {a}", file=sys.stderr)
            return 2
        else:
            files.append(a)
        i += 1
    if not files:
        print(__doc__)
        return 2
    results = []
    for path in files:
        rows = load(path)
        if not rows:
            print(f"{path}: 행 없음(y_true/y_pred/date 필요)")
            continue
        whole = summarize(rows, "ALL")
        print(f"\n== {path}")
        print(f"   ALL  rows {whole['rows']} · dates {whole['n_dates']} · "
              f"pooled {whole['pooled_auc']} · eqdate {whole['eqdate_auc']} · "
              f"diff {whole.get('diff')} · rows/date {whole['rows_per_date_minmax']}")
        folds = sorted({f for _, _, _, f in rows if f is not None})
        fold_out = []
        for fold in folds:
            sub = [r for r in rows if r[3] == fold]
            s = summarize(sub, f"fold{fold}")
            fold_out.append(s)
            print(f"   fold{fold} rows {s['rows']} · dates {s['n_dates']} · "
                  f"pooled {s['pooled_auc']} · eqdate {s['eqdate_auc']} · diff {s.get('diff')}")
        results.append({"file": path, "all": whole, "folds": fold_out})
    if json_out:
        with open(json_out, "w", encoding="utf-8") as f:
            json.dump({"audited_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
                       "results": results}, f, ensure_ascii=False, indent=1)
        print(f"\nsaved: {json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

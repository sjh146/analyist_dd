#!/usr/bin/env python3
"""소비 문턱 정책 비교 계측기 — 절대문턱 vs 분위(top-k) 의 실현수익 짝 검정 (CG84).

배경(CG81·CG83 실측, 2026-10-03):
  · 소비자(swing)는 **절대문턱 0.55** 를 쓴다 → 모델 확률 스케일이 바뀌면 진입 경로가 조용히
    열리고 닫힌다(스윕 모델 0.55 초과 37.0% vs 라이브 배포 챔피언 0.0% · MT116 3세션 무진입).
  · 정직한 cross-fitted Platt 보정으로도 절대문턱은 성립하지 않는다 — 보정은 확률을 0.5 근처로
    압축해 0.55 초과가 37.0% → 6.2% 로 **줄어든다**(CG83). 즉 해법은 보정 배선이 아니라 정책이다.
  → 그래서 후보는 '그날 상위 k'(분위 정책). 그러나 **top-k 진입이 실제로 돈이 되는지**는 같은
    프로토콜로 아직 재지 않았다 — 'AUC·정밀도 개선 = 기대수익 개선'은 이 스택에서 실측으로
    기각된 적이 있다(2026-10-02: 8개 모델 중 양(+) 기대 0개).

측정(같은 프로토콜·같은 OOS 행):
  같은 (fold,date) 안에서 두 진입집합의 **평균 실현수익(fwd_ret)** 을 짝지어 비교한다.
      Δ(k) = mean_fwd_ret(top-k 진입) − mean_fwd_ret(절대문턱 진입)     [날짜별]
  raw 스케일과 **cross-fitted Platt** 스케일 각각 계산한다(현 배포는 보정 미배선 = raw 기준).
  유의성은 부호검정(양(+) 날짜 vs 음(−) 날짜, 동점 제외) + 양(+) 날짜 비율로 본다.

사전등록(CG84 success): k=3·5·10 중 **2개 이상**에서 (Δ>0) · (양(+) 날짜 ≥ 0.6) · (p < 0.05)
를 동시에 만족하면 '정책 교체의 실질 근거 있음', 아니면 '근거 없음(노이즈)' 로 종결한다.

⚠ 읽기 전용. AUC 판정이 아니다 — `per_exp` 를 만들지 않는다(scoreboard 가 arm 최고값으로
   오독하는 사고 방지, 2026-09-29 CG31).

용법(컨테이너):
  docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 python -u \\
      scripts/policy_compare_probe.py /app/reports/overnight/cg81_preds.jsonl \\
      --ks 3,5,10 --threshold 0.55 --json-out /app/reports/overnight/cg84_policy.json'
"""
import argparse
import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

# 컨테이너에서 `python scripts/policy_compare_probe.py` 로 돌려도 `import app` 이 되도록
# (스크립트는 /app/scripts/ → 두 단계 위가 /app). 호스트에서는 무해하다.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def _std(xs):
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def binom_two_sided(pos, neg):
    """부호검정 양측 p — 동점은 이미 제외된 상태로 넘긴다(pos + neg = n).

    표준 sign test: tail = P(X >= max(pos,neg)) under Binom(n, 0.5), p = min(1, 2*tail).
    """
    n = pos + neg
    if n <= 0:
        return 1.0
    hi = max(pos, neg)
    tail = sum(math.comb(n, i) for i in range(hi, n + 1)) / (2 ** n)
    return min(1.0, 2.0 * tail)


def pair_compare(groups, p, thr, k, min_rows=None):
    """(fold,date) 짝 비교 — 절대문턱 진입집합 vs top-k 진입집합의 평균 실현수익 Δ.

    groups: {(fold, date): [row_idx, ...]}   (행 인덱스)
    p:      행별 점수 리스트(스케일: raw 또는 보정 후)
    thr:    절대문턱
    k:      분위 정책의 날짜별 상위 개수
    min_rows: 그 날 최소 행수(기본 k) — 미만이면 그 날은 건너뛴다(포화 방지).

    반환: n_pairs(짝이 성립한 날 수) · mean_delta · std · se · pos/neg/ties · pos_rate ·
          p_value(부호검정) · 진입집합 평균 실현수익(raw_abs/raw_topk) · 진입 건수.
    """
    if min_rows is None:
        min_rows = k
    deltas, abs_all, top_all = [], [], []
    skipped = 0
    for _, idx in groups.items():
        vals = [(p[i], i) for i in idx]
        if len(vals) < max(min_rows, k):
            skipped += 1
            continue
        vals.sort(key=lambda t: -t[0])
        top_idx = [i for _, i in vals[:k]]
        abs_idx = [i for s, i in vals if s > thr]
        if not abs_idx or not top_idx:
            skipped += 1
            continue
        tr, ar = [], []
        for i in top_idx:
            v = _fwd(i)
            if v is not None:
                tr.append(v)
        for i in abs_idx:
            v = _fwd(i)
            if v is not None:
                ar.append(v)
        if not tr or not ar:
            skipped += 1
            continue
        d = _mean(tr) - _mean(ar)
        # 부동소수 잡음이 '정확히 같은 집합'을 양/음 부호로 바꾸지 않게 동점 처리(1e-12).
        if abs(d) < 1e-12:
            d = 0.0
        deltas.append(d)
        top_all.extend(tr)
        abs_all.extend(ar)

    n = len(deltas)
    pos = sum(1 for d in deltas if d > 0)
    neg = sum(1 for d in deltas if d < 0)
    ties = n - pos - neg
    return {
        "k": k,
        "n_pairs": n,
        "n_skipped_dates": skipped,
        "mean_delta": round(_mean(deltas), 6) if deltas else None,
        "std": round(_std(deltas), 6) if deltas else None,
        "se": round(_std(deltas) / math.sqrt(n), 6) if n > 1 else None,
        "pos": pos, "neg": neg, "ties": ties,
        "pos_rate": round(pos / n, 4) if n else None,
        "p_value": round(binom_two_sided(pos, neg), 6),
        "abs_mean_ret": round(_mean(abs_all), 6) if abs_all else None,
        "topk_mean_ret": round(_mean(top_all), 6) if top_all else None,
        "abs_n": len(abs_all), "topk_n": len(top_all),
    }


# 행 데이터는 main 에서 채운다(순수 함수 단위 테스트를 위해 전역으로 둔다).
_ROWS = []


def _fwd(i):
    v = _ROWS[i].get("fwd_ret")
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _group_rows(rows):
    g = defaultdict(list)
    for i, r in enumerate(rows):
        g[(int(r["fold"]), str(r["date"]))].append(i)
    return g


def _load_rows(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("y_pred") is None or r.get("date") is None or r.get("fold") is None:
                continue
            rows.append(r)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("preds")
    ap.add_argument("--ks", default="3,5,10")
    ap.add_argument("--threshold", type=float, default=0.55)
    ap.add_argument("--json-out", default="")
    ap.add_argument("--no-calibration", action="store_true",
                    help="보정 스케일을 계산하지 않는다(현 배포=raw 만 볼 때)")
    a = ap.parse_args()

    rows = _load_rows(a.preds)
    if not rows:
        print("행이 없다")
        return 1

    global _ROWS
    _ROWS = rows
    ks = [int(x) for x in a.ks.split(",") if x.strip()]
    groups = _group_rows(rows)
    p_raw = [float(r["y_pred"]) for r in rows]

    print(f"파일: {a.preds}")
    print(f"행 {len(rows)} · (fold,date) 그룹 {len(groups)} · ks {ks} · 문턱 {a.threshold}")

    result = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        "preds_file": a.preds,
        "n_rows": len(rows),
        "n_groups": len(groups),
        "threshold": a.threshold,
        "ks": ks,
        "scales": {},
    }

    result["scales"]["raw"] = {f"k{k}": pair_compare(groups, p_raw, a.threshold, k)
                               for k in ks}

    if not a.no_calibration:
        try:
            from app.calibration import PlattCalibrator
        except Exception as e:  # pragma: no cover
            print(f"app.calibration import 실패(보정 스케일 생략): {e}")
            PlattCalibrator = None
        if PlattCalibrator is not None:
            folds = sorted({int(r["fold"]) for r in rows})
            pred = [None] * len(rows)
            for kf in folds:
                tr = [i for i, r in enumerate(rows) if int(r["fold"]) != kf]
                te = [i for i, r in enumerate(rows) if int(r["fold"]) == kf]
                if not tr or not te:
                    continue
                cal = PlattCalibrator().fit([float(rows[i]["y_pred"]) for i in tr],
                                            [int(rows[i]["y_true"]) for i in tr])
                out = list(cal.calibrate([float(rows[i]["y_pred"]) for i in te]))
                for j, i in enumerate(te):
                    pred[i] = float(out[j])
            if all(x is not None for x in pred):
                result["scales"]["platt"] = {f"k{k}": pair_compare(groups, pred, a.threshold, k)
                                             for k in ks}
            else:
                result["calibration_error"] = "일부 행 미예측 — 보정 스케일 생략"

    # ── 사전등록 판정 입력(scale=raw: 현 배포 스케일) ───────────────────────────
    primary = result["scales"].get("raw") or {}
    min_pos_rate = 0.6
    max_p = 0.05
    passes = []
    for k in ks:
        st = primary.get(f"k{k}") or {}
        if st.get("mean_delta") is None:
            continue
        ok = (st["mean_delta"] > 0 and (st["pos_rate"] or 0) >= min_pos_rate
              and st["p_value"] < max_p)
        if ok:
            passes.append(k)
    result["primary_scale"] = "raw"
    result["criterion"] = {"min_pos_rate": min_pos_rate, "max_p": max_p, "min_k_pass": 2}
    result["k_passed"] = passes

    for scale, stats in result["scales"].items():
        print(f"\n[{scale}]")
        for k in ks:
            st = stats[f"k{k}"]
            md = st["mean_delta"]
            print(f"  k={k:<3} Δ {md if md is None else format(md, '+.4%')}"
                  f" · se {st['se']} · 양(+) {st['pos']}/{st['n_pairs']}"
                  f" (pos_rate {st['pos_rate']}) · p {st['p_value']}"
                  f" · 절대문턱 진입 {st['abs_n']}({st['abs_mean_ret']})"
                  f" vs top-k {st['topk_n']}({st['topk_mean_ret']})")
    print(f"\n사전등록 통과 k: {passes} (k=3·5·10 중 2개 이상이어야 '근거 있음')")

    if a.json_out:
        tmp = a.json_out + ".tmp"
        os.makedirs(os.path.dirname(a.json_out) or ".", exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        os.replace(tmp, a.json_out)
        print(f"\n요약 저장: {a.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

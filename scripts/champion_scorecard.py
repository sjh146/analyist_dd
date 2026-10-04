#!/usr/bin/env python3
"""배포 챔피언의 **검증 성적표**(validation scorecard)를 한 파일로 만든다 — MT49.

WHY (트레이더 환류 need-fold-scorecard, 2026-10-05)
- 트레이더 사이클은 계약 2번으로 "엔지니어 검증 성적표에 **폴드 통계·purge·승격 dry-run** 이
  있는가"를 확인한다(`scripts/trader_cycle.py::verify_model_handoff` — 원장 최근 5행 문자열 검사).
- 실측(2026-10-05 03:0x): 최근 원장 5행이 전부 `fillable_topk_expectancy`(돈 지표) 기록이라
  gaps = [mean_std, fold_win_rate, purge, promote_dryrun] → 트레이더가 매 사이클 같은 핸드오프를
  다시 올린다. 값이 없어서가 아니라 **성적표가 원장에 실려 있지 않아서**다.
- 그래서 ① 이 스크립트가 챔피언의 폴드 통계(다중창 크로스섹션 AUC)·purge 정책·마지막 승격
  dry-run 판정·돈 지표(robust_oos.json)를 한 JSON 으로 모으고, ② 구동기 `append_ledger` 가 그
  요약을 **모든 원장 기록의 `validation` 블록**으로 실어 최근 5행에 항상 남게 한다(멱등).

설계 원칙: 자기신고 금지 — 모든 값은 파일/원장에서 읽고 **출처(파일 경로·기록 id·측정시각)** 를
함께 적는다. 폴드 통계가 오래됐으면 그 사실이 보이게 둔다(값을 만들어 내지 않는다).

사용(호스트):
    python3 scripts/champion_scorecard.py
    python3 scripts/champion_scorecard.py --out data/reports/model_engineer_scorecard.json
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import statistics
from datetime import datetime

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_MODEL_DIR = os.path.join(PROJ, "services", "xgboost-ml", "app", "models", "champion")
DEFAULT_LEDGER = os.path.join(PROJ, "data", "reports", "model_engineer_ledger.jsonl")
DEFAULT_OUT = os.path.join(PROJ, "data", "reports", "model_engineer_scorecard.json")
# 챔피언 직접 측정 산출물 후보(champion_robust_eval payload — folds[].auc_mean 보유)
EVAL_GLOBS = [
    os.path.join(PROJ, "services", "xgboost-ml", "reports", "champion_robust_eval*.json"),
    os.path.join(PROJ, "services", "xgboost-ml", "app", "reports", "champion_robust_eval*.json"),
]


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _read_ledger(path):
    rows = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return rows


def _fold_stats_from_payload(d, source, measured_at):
    folds = [f for f in (d.get("folds") or []) if isinstance(f, dict)
             and isinstance(f.get("auc_mean"), (int, float))]
    means = [float(f["auc_mean"]) for f in folds] if folds else []
    if not means and isinstance(d.get("fold_means"), list):
        means = [float(x) for x in d["fold_means"] if isinstance(x, (int, float))]
    if not means:
        return None
    return {
        "protocol": d.get("protocol"),
        "folds": [round(m, 4) for m in means],
        "mean": round(statistics.mean(means), 4),
        "std": round(statistics.pstdev(means), 4) if len(means) > 1 else None,
        "min": round(min(means), 4), "max": round(max(means), 4),
        "fold_win_rate": round(sum(1 for m in means if m > 0.5) / len(means), 3),
        "n_folds": len(means),
        "measured_at": measured_at, "source": source,
    }


def _champion_dirish(s):
    s = str(s or "").rstrip("/")
    return s.endswith("champion") or s.endswith("champion_prev")


def best_fold_stats(ledger, model_dir_rel):
    """챔피언 자기 과제의 최신 폴드 통계 — 파일 산출물과 원장 기록을 모두 뒤져 최신을 고른다."""
    cands = []
    for pat in EVAL_GLOBS:
        for p in glob.glob(pat):
            d = _read_json(p)
            if not isinstance(d, dict) or not _champion_dirish(d.get("model_dir")):
                continue
            st = _fold_stats_from_payload(d, os.path.relpath(p, PROJ), d.get("measured_at"))
            if st:
                cands.append(st)
    for r in ledger:
        p = r.get("parsed") or {}
        if not isinstance(p, dict) or not _champion_dirish(p.get("model_dir")):
            continue
        st = _fold_stats_from_payload(p, f"ledger:{r.get('id')}", p.get("measured_at") or r.get("ts"))
        if st:
            cands.append(st)
    if not cands:
        return None
    # 최신 판정: measured_at 문자열(ISO) → 없으면 뒤 순서(원장은 append 순)
    return max(cands, key=lambda s: str(s.get("measured_at") or ""))


def last_promote_dryrun(ledger):
    for r in reversed(ledger):
        p = r.get("parsed") or {}
        if not isinstance(p, dict):
            continue
        if p.get("status") and ("candidate_auc" in p or "champion_baseline" in p):
            return {"status": p.get("status"), "reason": p.get("reason"),
                    "candidate_auc": p.get("candidate_auc"),
                    "champion_baseline": p.get("champion_baseline"),
                    "baseline_source": p.get("champion_baseline_source"),
                    "record_id": r.get("id"), "record_ts": r.get("ts")}
    return None


def last_purge_observed(ledger):
    """라벨 참조일 purge 의 마지막 실측치(n_label_ref_purged) — 없으면 None(정책만 기록)."""
    for r in reversed(ledger):
        p = r.get("parsed") or {}
        if not isinstance(p, dict):
            continue
        v = p.get("n_label_ref_purged")
        if isinstance(v, (int, float)):
            return {"n_label_ref_purged": int(v), "record_id": r.get("id"), "record_ts": r.get("ts")}
        res = p.get("results")
        if isinstance(res, list):
            for x in res:
                if isinstance(x, dict) and isinstance(x.get("n_label_ref_purged"), (int, float)):
                    return {"n_label_ref_purged": int(x["n_label_ref_purged"]),
                            "record_id": r.get("id"), "record_ts": r.get("ts")}
    return None


def last_purge_from_summaries(extra_globs=None):
    """요약 JSON(results[].n_label_ref_purged)에서 마지막 라벨참조일 purge 실측치를 읽는다.

    원장에는 parse_wf_sweep 이 만든 per_exp 만 실려 n_label_ref_purged 가 남지 않으므로
    (스킬: 요약 스키마의 부가 필드는 원장에 안 들어간다) 산출물 파일에서 직접 읽는다.
    """
    pats = list(extra_globs or []) + [
        os.path.join(PROJ, "services", "xgboost-ml", "reports", "overnight", "*summary*.json"),
        os.path.join(PROJ, "services", "xgboost-ml", "reports", "overnight", "wf_*.json"),
    ]
    best = None
    for pat in pats:
        for p in glob.glob(pat):
            d = _read_json(p)
            if not isinstance(d, dict):
                continue
            for x in (d.get("results") or []):
                if not isinstance(x, dict):
                    continue
                # n_label_ref_purged 는 results[] 최상위가 아니라 folds{foldN} 안에 있다(실측 2026-10-05).
                tot, nfold = 0, 0
                folds = x.get("folds") or {}
                for fv in folds.values():
                    if isinstance(fv, dict) and isinstance(fv.get("n_label_ref_purged"), (int, float)):
                        tot += int(fv["n_label_ref_purged"]); nfold += 1
                if not nfold and isinstance(x.get("n_label_ref_purged"), (int, float)):
                    tot, nfold = int(x["n_label_ref_purged"]), 1
                if not nfold:
                    continue
                cand = {"n_label_ref_purged": tot, "n_folds_counted": nfold,
                        "exp": x.get("exp"), "source": os.path.relpath(p, PROJ),
                        "finished_at": d.get("finished_at")}
                if best is None or str(cand.get("finished_at") or "") >= str(best.get("finished_at") or ""):
                    best = cand
    return best


def build(model_dir, ledger_path):
    insample = _read_json(os.path.join(model_dir, "robust_auc.json")) or {}
    oos = _read_json(os.path.join(model_dir, "robust_oos.json")) or {}
    auc_txt = None
    try:
        with open(os.path.join(model_dir, "auc.txt"), encoding="utf-8") as f:
            auc_txt = float(f.read().strip().split()[0])
    except (OSError, ValueError, IndexError):
        pass

    ledger = _read_ledger(ledger_path)
    fold_stats = best_fold_stats(ledger, os.path.relpath(model_dir, PROJ))
    purge_val = last_purge_observed(ledger)
    if purge_val is None:
        purge_val = last_purge_from_summaries()
    _proto = str((fold_stats or {}).get("protocol") or "")
    _h = re.search(r"h=(\d+)", _proto)
    purge = {
        "policy": "라벨 참조일 기준 h거래일 purge(wf_wave.py·wf_label_sweep.py) — 달력 h일이 아니라 "
                  "그 종목의 h번째 미래 '행'의 날짜 기준",
        "horizon": (int(_h.group(1)) if _h else 5),
        "observed": purge_val,
    }
    return {
        "metric_name": "champion_scorecard",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "champion_dir": os.path.relpath(model_dir, PROJ),
        "champion_auc_txt": auc_txt,
        "insample": {
            "robust_auc": insample.get("robust_auc"), "metric": insample.get("metric"),
            "protocol": insample.get("protocol"), "recorded_at": insample.get("recorded_at"),
            "model_aucs": insample.get("model_aucs"),
            "source": "services/xgboost-ml/app/models/champion/robust_auc.json",
            "note": "단일 분할 인샘플 — 승격·기준선 판단에 쓰지 않는다(하드룰 #1)",
        },
        "fold_stats": fold_stats or {"error": "챔피언 폴드 통계 산출물 없음"},
        "purge": purge,
        "promote_dryrun": last_promote_dryrun(ledger) or {"status": "기록 없음"},
        "money": {
            "metric": oos.get("metric"), "robust_auc": oos.get("robust_auc"),
            "auc_pooled": oos.get("auc_pooled"), "folds": oos.get("folds"),
            "horizon": oos.get("horizon"), "label_kind": oos.get("label_kind"),
            "expectancy_pct": oos.get("expectancy_pct"), "expectancy_t": oos.get("expectancy_t"),
            "n_sessions": oos.get("n_sessions"), "n_trades": oos.get("n_trades"),
            "halves": oos.get("halves"), "created_at": oos.get("created_at"),
            "source": "services/xgboost-ml/app/models/champion/robust_oos.json",
        },
    }


# ── 구동기(append_ledger)가 원장에 싣는 압축 블록 ────────────────────────────
CONTRACT_KEYS = ("folds", "mean", "std", "fold_win_rate", "purge", "promote_dryrun")


def validation_block(scorecard_path=None):
    """원장 기록마다 붙는 검증 블록(트레이더 계약 2번용). 파일이 없으면 None."""
    path = scorecard_path or DEFAULT_OUT
    d = _read_json(path)
    if not isinstance(d, dict):
        return None
    fs = d.get("fold_stats") or {}
    return {
        "scorecard": os.path.relpath(path, PROJ),
        "generated_at": d.get("generated_at"),
        "fold_stats": {k: fs.get(k) for k in
                       ("folds", "mean", "std", "min", "max", "fold_win_rate", "n_folds",
                        "measured_at", "source")},
        "purge": d.get("purge"),
        "promote_dryrun": d.get("promote_dryrun"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="배포 챔피언 검증 성적표 생성")
    ap.add_argument("--model-dir", default=DEFAULT_MODEL_DIR)
    ap.add_argument("--ledger", default=DEFAULT_LEDGER)
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args()

    rep = build(args.model_dir, args.ledger)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=2)

    fs = rep["fold_stats"]
    print(f"챔피언 성적표 → {args.out}")
    if "mean" in fs:
        print(f"  폴드 통계: {fs['folds']} · 평균 {fs['mean']}±{fs['std']} "
              f"(min {fs['min']} max {fs['max']}) · 폴드승률 {fs['fold_win_rate']} · "
              f"n_folds {fs['n_folds']} · 출처 {fs['source']} @{fs['measured_at']}")
    else:
        print(f"  폴드 통계 없음: {fs.get('error')}")
    pd = rep["promote_dryrun"]
    print(f"  승격 dry-run: status={pd.get('status')} · 기록 {pd.get('record_id')} @{pd.get('record_ts')}")
    print(f"  purge: 정책 기록 + 관측 {rep['purge'].get('observed')}")
    mn = rep["money"]
    print(f"  돈 지표: 순기대 {mn.get('expectancy_pct')}%p (t {mn.get('expectancy_t')}) · "
          f"세션 {mn.get('n_sessions')} · 소스 {mn.get('source')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

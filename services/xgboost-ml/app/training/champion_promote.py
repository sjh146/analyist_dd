"""Guarded champion promotion (challenger → champion).

WHY: the nightly pipeline used to run scripts/train_quick.py, which trained a
10-stock ensemble and wrote it straight into ``app/models/champion`` — any run
silently replaced the model that live swing/close screener inference reads.
There was no comparison against the incumbent, so a degenerate model (val AUC
0.43-0.50, near-constant all-DOWN probabilities) could become the champion.

FIX: retrain writes to a candidate directory, and this module decides whether to
promote. Promotion requires (a) every artifact to be present, (b) candidate's
comparable metric >= ``--min-auc``, and (c) that metric >= incumbent baseline +
``--min-improvement``. Otherwise the incumbent stays and the reason is logged.
The previous champion is preserved as ``champion_prev_<ts>`` (last 3 kept).

ROBUST BASELINE (2026-09-24): 단일 시드 ``auc.txt`` 는 운값이라 비교 기준으로 쓰면
승격이 잠긴다(실측: 같은 코드·같은 데이터로 0.4365 / 0.5790 / 0.6131 이 나왔다.
현 챔피언 auc.txt = 0.6131 이 그대로 비교 기준이 되어, walk-forward 0.54~0.58 인
개선 후보가 영원히 승격되지 않았다). 그래서
  * 후보 지표는 ``auc_mean``(다중 시드 평균)이 있으면 그것, 없으면 ``ensemble_auc``.
  * 챔피언 기준선은 ``champion/robust_auc.json``(승격 시 기록)이 있으면 그 값,
    없으면(레거시) ``min(auc.txt, --legacy-baseline-cap)`` — 즉 운값은 상한까지만 인정.
  * 승격 시 ``robust_auc.json`` 을 챔피언 디렉터리에 기록해 다음 비교를 동일 기준으로 한다.

Usage (in the xgboost-ml container, cwd ``/app``):
    python -m app.training.champion_promote \
        --candidate app/models/champion_cand \
        --champion app/models/champion \
        --min-auc 0.55 --summary-out app/reports/ml_result.json
"""

import argparse
import glob
import json
import logging
import os
import shutil
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

MODEL_FILES = ("xgboost_model.pkl", "lightgbm_model.pkl", "catboost_model.pkl")
CONTRACT_FILES = ("feature_names.json", "auc.txt")
KEEP_BACKUPS = 3


def _latest_training_result(candidate_dir: str) -> Optional[str]:
    paths = sorted(glob.glob(os.path.join(candidate_dir, "training-result-*.json")))
    return paths[-1] if paths else None


def _read_json(path: str) -> Dict:
    with open(path) as f:
        return json.load(f)


def _read_champion_auc(champion_dir: str) -> float:
    path = os.path.join(champion_dir, "auc.txt")
    try:
        with open(path) as f:
            return float(f.read().strip())
    except (OSError, ValueError):
        return 0.0


def _champion_baseline(champion_dir: str, legacy_cap: float) -> Dict:
    """비교 기준선 — 다중 시드 기록이 있으면 그것, 없으면 운값을 상한까지만 인정."""
    path = os.path.join(champion_dir, "robust_auc.json")
    try:
        data = _read_json(path)
        val = float(data.get("robust_auc"))
        return {"value": val, "source": "robust_auc.json",
                "protocol": data.get("protocol"), "recorded_at": data.get("recorded_at")}
    except (OSError, ValueError, TypeError, KeyError):
        pass
    raw = _read_champion_auc(champion_dir)
    return {"value": min(raw, legacy_cap), "source": "auc.txt(legacy, capped)",
            "raw_auc_txt": raw, "cap": legacy_cap}


def _missing(paths: List[str]) -> List[str]:
    return [os.path.basename(p) for p in paths if not os.path.exists(p)]


def _backup_champion(champion_dir: str) -> Optional[str]:
    """Move the incumbent aside so a bad promote can be reverted by hand."""
    if not os.path.isdir(champion_dir):
        return None
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    prev = os.path.join(os.path.dirname(champion_dir.rstrip("/")), f"champion_prev_{ts}")
    shutil.copytree(champion_dir, prev)
    logger.info("backed up incumbent champion -> %s", prev)

    parent = os.path.dirname(champion_dir.rstrip("/"))
    backups = sorted(glob.glob(os.path.join(parent, "champion_prev_*")))
    for stale in backups[:-KEEP_BACKUPS]:
        shutil.rmtree(stale, ignore_errors=True)
        logger.info("pruned old backup %s", stale)
    return prev


def promote(
    candidate_dir: str,
    champion_dir: str,
    min_auc: float = 0.55,
    min_improvement: float = 0.0,
    dry_run: bool = False,
    legacy_baseline_cap: float = 0.55,
    max_std: Optional[float] = 0.05,
) -> Dict:
    """Compare a trained candidate against the incumbent and promote if better.

    Returns a summary dict; ``promoted`` tells whether the champion was replaced.
    Never raises for a merely-worse candidate — that is a normal outcome.

    ``legacy_baseline_cap``: 챔피언에 다중 시드 기록이 없을 때 ``auc.txt`` 를 이 값까지만
    인정한다(단일 시드 운값이 승격을 잠그는 것을 막는다).
    ``max_std``: 다중 시드 std 가 이 값을 넘는 후보는 거부(None/0 이하면 비활성).
    """
    result: Dict = {
        "promoted": False,
        "candidate_dir": candidate_dir,
        "champion_dir": champion_dir,
        "min_auc": min_auc,
        "min_improvement": min_improvement,
        "legacy_baseline_cap": legacy_baseline_cap,
        "max_std": max_std,
    }

    latest_result = _latest_training_result(candidate_dir)
    required = [os.path.join(candidate_dir, n) for n in MODEL_FILES + CONTRACT_FILES]
    missing = _missing(required)
    if latest_result is None or missing:
        result["reason"] = (
            f"candidate incomplete (result_json={'none' if latest_result is None else 'ok'}, "
            f"missing={missing})"
        )
        logger.error("promote skipped: %s", result["reason"])
        result["status"] = "invalid_candidate"
        return result

    meta = _read_json(latest_result)
    # 후보 지표: 다중 시드 평균(auc_mean) 우선, 없으면 단일 시드 ensemble_auc.
    cand_auc = float(meta.get("auc_mean") or meta.get("ensemble_auc") or 0.0)
    cand_metric = "auc_mean" if meta.get("auc_mean") is not None else "ensemble_auc"
    baseline = _champion_baseline(champion_dir, legacy_baseline_cap)
    champ_auc = float(baseline["value"])
    result.update({
        "candidate_auc": round(cand_auc, 4),
        "candidate_metric": cand_metric,
        "candidate_auc_std": meta.get("auc_std"),
        "champion_auc_before": round(_read_champion_auc(champion_dir), 4),
        "champion_baseline": round(champ_auc, 4),
        "champion_baseline_source": baseline["source"],
        "model_aucs": meta.get("model_aucs", {}),
        "n_rows": meta.get("n_rows"),
        "n_features": meta.get("n_features"),
        "up_rate": meta.get("up_rate"),
        "retrained_at": meta.get("retrained_at"),
    })

    # auc.txt in the candidate must agree with the metadata we are about to trust.
    cand_auc_file = _read_champion_auc(candidate_dir)
    if abs(cand_auc_file - float(meta.get("ensemble_auc") or 0.0)) > 0.02:
        result["status"] = "invalid_candidate"
        result["reason"] = (
            f"candidate auc.txt ({cand_auc_file:.4f}) disagrees with "
            f"training-result ensemble_auc ({float(meta.get('ensemble_auc') or 0.0):.4f})"
        )
        logger.error("promote skipped: %s", result["reason"])
        return result

    if cand_auc < min_auc:
        result["status"] = "kept_incumbent"
        result["reason"] = f"candidate {cand_metric} {cand_auc:.4f} < floor {min_auc:.2f}"
        logger.warning("promote skipped: %s", result["reason"])
        return result

    # 안정성: 다중 시드 std 가 있으면 과변동 후보는 거부한다(운으로 오른 후보 방지).
    cand_std = meta.get("auc_std")
    if max_std is not None and cand_std is not None and float(cand_std) > max_std:
        result["status"] = "kept_incumbent"
        result["reason"] = (f"candidate auc_std {float(cand_std):.4f} > max_std "
                            f"{max_std:.4f} (시드 간 변동이 큼)")
        logger.warning("promote skipped: %s", result["reason"])
        return result

    if cand_auc < champ_auc + min_improvement - 1e-9:
        result["status"] = "kept_incumbent"
        result["reason"] = (
            f"candidate {cand_metric} {cand_auc:.4f} did not beat champion baseline "
            f"{champ_auc:.4f} ({baseline['source']}) (+{min_improvement:.4f} required)"
        )
        logger.warning("promote skipped: %s", result["reason"])
        return result

    if dry_run:
        result["status"] = "would_promote"
        result["reason"] = "dry-run: incumbent left untouched"
        logger.info("dry-run: would promote %.4f over baseline %.4f (%s)",
                    cand_auc, champ_auc, baseline["source"])
        return result

    backup = _backup_champion(champion_dir)
    os.makedirs(champion_dir, exist_ok=True)
    for name in MODEL_FILES + CONTRACT_FILES:
        shutil.copy2(os.path.join(candidate_dir, name), os.path.join(champion_dir, name))
    shutil.copy2(latest_result, os.path.join(champion_dir, os.path.basename(latest_result)))
    # 다음 비교를 동일 기준으로 하도록 승격 지표를 기록한다.
    with open(os.path.join(champion_dir, "robust_auc.json"), "w") as f:
        json.dump({
            "robust_auc": round(cand_auc, 6),
            "metric": cand_metric,
            "auc_std": cand_std,
            "protocol": f"{cand_metric} on candidate val split (n_rows={meta.get('n_rows')})",
            "model_aucs": meta.get("model_aucs", {}),
            "recorded_at": datetime.now().isoformat(timespec="seconds"),
            "replaced_baseline": round(champ_auc, 6),
            "replaced_source": baseline["source"],
        }, f, ensure_ascii=False, indent=2)

    result.update({
        "promoted": True,
        "status": "promoted",
        "auc": round(cand_auc, 4),
        "champion_auc_after": round(cand_auc, 6),
        "backup_dir": backup,
        "reason": (f"candidate {cand_metric} {cand_auc:.4f} >= baseline {champ_auc:.4f} "
                   f"({baseline['source']}) and >= floor {min_auc:.2f}"),
    })
    logger.info("PROMOTED candidate (%s=%.4f) over baseline (%.4f, %s)",
                cand_metric, cand_auc, champ_auc, baseline["source"])
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="Guarded champion promotion")
    ap.add_argument("--candidate", default="app/models/champion_cand")
    ap.add_argument("--champion", default="app/models/champion")
    ap.add_argument("--min-auc", type=float, default=0.55,
                    help="거부선: 이 값 미만 후보는 절대 승격하지 않는다")
    ap.add_argument("--min-improvement", type=float, default=0.0,
                    help="현 챔피언 대비 최소 개선폭 (0.0 = 동률까지 허용)")
    ap.add_argument("--legacy-baseline-cap", type=float, default=0.55,
                    help="챔피언에 다중 시드 기록이 없을 때 auc.txt 를 인정하는 상한 "
                         "(단일 시드 운값이 승격을 잠그지 못하게 한다)")
    ap.add_argument("--max-std", type=float, default=0.05,
                    help="후보 auc_std 상한(초과 시 거부). 0 이하면 비활성")
    ap.add_argument("--summary-out", default="app/reports/ml_result.json")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")

    result = promote(
        candidate_dir=args.candidate,
        champion_dir=args.champion,
        min_auc=args.min_auc,
        min_improvement=args.min_improvement,
        dry_run=args.dry_run,
        legacy_baseline_cap=args.legacy_baseline_cap,
        max_std=args.max_std if args.max_std > 0 else None,
    )

    if args.summary_out:
        os.makedirs(os.path.dirname(args.summary_out) or ".", exist_ok=True)
        payload = {
            "auc": result.get("candidate_auc"),
            "candidate_metric": result.get("candidate_metric"),
            "promoted": result["promoted"],
            "status": result.get("status"),
            "reason": result.get("reason"),
            "champion_auc_before": result.get("champion_auc_before"),
            "champion_baseline": result.get("champion_baseline"),
            "champion_baseline_source": result.get("champion_baseline_source"),
            "champion_auc_after": result.get("champion_auc_after"),
            "model_aucs": result.get("model_aucs", {}),
            "n_rows": result.get("n_rows"),
            "n_features": result.get("n_features"),
            "up_rate": result.get("up_rate"),
            "retrained_at": result.get("retrained_at"),
            "decided_at": datetime.now().isoformat(timespec="seconds"),
        }
        with open(args.summary_out, "w") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 5 if result.get("status") == "invalid_candidate" else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Guarded champion promotion (challenger → champion).

WHY: the nightly pipeline used to run scripts/train_quick.py, which trained a
10-stock ensemble and wrote it straight into ``app/models/champion`` — any run
silently replaced the model that live swing/close screener inference reads.
There was no comparison against the incumbent, so a degenerate model (val AUC
0.43-0.50, near-constant all-DOWN probabilities) could become the champion.

FIX: retrain writes to a candidate directory, and this module decides whether to
promote. Promotion requires (a) every artifact to be present, (b) candidate val
AUC >= ``--min-auc``, and (c) candidate val AUC >= incumbent ``auc.txt`` +
``--min-improvement``. Otherwise the incumbent stays and the reason is logged.
The previous champion is preserved as ``champion_prev_<ts>`` (last 3 kept).

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
) -> Dict:
    """Compare a trained candidate against the incumbent and promote if better.

    Returns a summary dict; ``promoted`` tells whether the champion was replaced.
    Never raises for a merely-worse candidate — that is a normal outcome.
    """
    result: Dict = {
        "promoted": False,
        "candidate_dir": candidate_dir,
        "champion_dir": champion_dir,
        "min_auc": min_auc,
        "min_improvement": min_improvement,
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
    cand_auc = float(meta.get("ensemble_auc") or 0.0)
    champ_auc = _read_champion_auc(champion_dir)
    result.update({
        "candidate_auc": round(cand_auc, 4),
        "champion_auc_before": round(champ_auc, 4),
        "model_aucs": meta.get("model_aucs", {}),
        "n_rows": meta.get("n_rows"),
        "n_features": meta.get("n_features"),
        "up_rate": meta.get("up_rate"),
        "retrained_at": meta.get("retrained_at"),
    })

    # auc.txt in the candidate must agree with the metadata we are about to trust.
    cand_auc_file = _read_champion_auc(candidate_dir)
    if abs(cand_auc_file - cand_auc) > 0.02:
        result["status"] = "invalid_candidate"
        result["reason"] = (
            f"candidate auc.txt ({cand_auc_file:.4f}) disagrees with "
            f"training-result ensemble_auc ({cand_auc:.4f})"
        )
        logger.error("promote skipped: %s", result["reason"])
        return result

    if cand_auc < min_auc:
        result["status"] = "kept_incumbent"
        result["reason"] = f"candidate AUC {cand_auc:.4f} < floor {min_auc:.2f}"
        logger.warning("promote skipped: %s", result["reason"])
        return result

    if cand_auc < champ_auc + min_improvement - 1e-9:
        result["status"] = "kept_incumbent"
        result["reason"] = (
            f"candidate AUC {cand_auc:.4f} did not beat incumbent {champ_auc:.4f}"
            f" (+{min_improvement:.4f} required)"
        )
        logger.warning("promote skipped: %s", result["reason"])
        return result

    if dry_run:
        result["status"] = "would_promote"
        result["reason"] = "dry-run: incumbent left untouched"
        logger.info("dry-run: would promote %.4f over %.4f", cand_auc, champ_auc)
        return result

    backup = _backup_champion(champion_dir)
    os.makedirs(champion_dir, exist_ok=True)
    for name in MODEL_FILES + CONTRACT_FILES:
        shutil.copy2(os.path.join(candidate_dir, name), os.path.join(champion_dir, name))
    shutil.copy2(latest_result, os.path.join(champion_dir, os.path.basename(latest_result)))

    result.update({
        "promoted": True,
        "status": "promoted",
        "auc": round(cand_auc, 4),
        "champion_auc_after": round(_read_champion_auc(champion_dir), 6),
        "backup_dir": backup,
        "reason": f"candidate AUC {cand_auc:.4f} >= {champ_auc:.4f} and >= floor {min_auc:.2f}",
    })
    logger.info("PROMOTED candidate (auc=%.4f) over incumbent (auc=%.4f)", cand_auc, champ_auc)
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="Guarded champion promotion")
    ap.add_argument("--candidate", default="app/models/champion_cand")
    ap.add_argument("--champion", default="app/models/champion")
    ap.add_argument("--min-auc", type=float, default=0.55,
                    help="거부선: 이 값 미만 후보는 절대 승격하지 않는다")
    ap.add_argument("--min-improvement", type=float, default=0.0,
                    help="현 챔피언 대비 최소 개선폭 (0.0 = 동률까지 허용)")
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
    )

    if args.summary_out:
        os.makedirs(os.path.dirname(args.summary_out) or ".", exist_ok=True)
        payload = {
            "auc": result.get("candidate_auc"),
            "promoted": result["promoted"],
            "status": result.get("status"),
            "reason": result.get("reason"),
            "champion_auc_before": result.get("champion_auc_before"),
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

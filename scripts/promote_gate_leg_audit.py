#!/usr/bin/env python3
"""[CG170] 승격 게이트 'AUC 다리' 프로토콜 혼용 감사 — **읽기 전용**.

WHY (2026-10-11 CG168 실측): `champion_promote` 는 후보 지표로
`<후보>/robust_oos.json` (다중폴드 크로스섹션 AUC, metric='robust_auc') 을 쓸 수 있는데
(`--require-robust` / `PROMOTE_REQUIRE_ROBUST=1`), 챔피언 기준선은
`champion/robust_auc.json` 의 값 — 현행 그 파일의 `metric` 은 **'ensemble_auc'**
(= `ensemble_auc on candidate val split`, 단일분할 val AUC 0.5513) — 을 쓴다.
즉 게이트가 **눈금이 다른 두 수**를 비교한다:

    후보 robust_auc 0.5284 (다중폴드)  <  기준선 0.5513 (단일분할) → keep

스킬 하드규칙 1('단일 분할 AUC 는 판정·승격 기준선으로 쓰지 않는다')이 게이트 코드 안에서
위반되고 있는 형태다(같은 이유로 챔피언 0.6131 단일값이 동결됐다). 같은 후보를 **같은 눈금**으로
비교하면 챔피언 자신의 다중폴드 값은 `champion/robust_oos.json` = 0.4908 이다 → Δ +0.0376.

이 도구는 아무것도 바꾸지 않는다. 수리(비교 로직·문턱·기준선)는 게이트 변경이라
리뷰보드 승인 대상이다 — 여기서는 **감사 표와 증거 JSON 만** 남긴다.

출력: `data/reports/promote_gate_leg_audit_<YYYYMMDD>.json` + stdout 표.
    python3 scripts/promote_gate_leg_audit.py [--out PATH] [--min-improvement 0.02] [--selftest]
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ML = os.path.join(PROJ, "services", "xgboost-ml")
CHAMPION_DIR = os.path.join(ML, "app", "models", "champion")

# 게이트가 실제로 쓰는 두 출처 (champion_promote.py:218 `_champion_baseline`, :103 `_read_robust_oos`)
REGISTERED_BASELINE = os.path.join(CHAMPION_DIR, "robust_auc.json")
CHAMPION_ROBUST = os.path.join(CHAMPION_DIR, "robust_oos.json")

_MONEY_RE = re.compile(r"\+([0-9.]+)\s*required")


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def family(metric) -> str:
    """지표 이름 → 눈금 계열. 'robust' 가 들어가면 다중폴드 크로스섹션, 아니면 단일분할 val."""
    m = str(metric or "").lower()
    return "robust" if "robust" in m else "single_split"


def yardstick_of_run(run: dict) -> dict:
    """게이트 실행 요약 1건 → 후보/기준선 눈금 + 같은 눈금 재비교(순수 함수)."""
    cand_auc = run.get("auc")
    cand_metric = run.get("candidate_metric")
    base_val = run.get("champion_baseline")
    base_src = run.get("champion_baseline_source")
    rob = run.get("candidate_robust_oos") or {}
    # 요약에 metric 이 없으면(구버전 산출물) 출처 파일명으로 추정한다.
    base_metric = run.get("champion_baseline_metric")
    if base_metric is None:
        base_metric = "ensemble_auc" if "robust_auc.json" in str(base_src or "") else None
    cand_fam = family(cand_metric)
    base_fam = family(base_metric) if base_metric else None
    out = {
        "candidate_auc": cand_auc,
        "candidate_metric": cand_metric,
        "candidate_family": cand_fam,
        "candidate_robust_oos": rob.get("value"),
        "baseline": base_val,
        "baseline_metric": base_metric,
        "baseline_source": base_src,
        "baseline_family": base_fam,
        "yardstick_mismatch": bool(base_fam and cand_fam != base_fam),
    }
    mi = run.get("min_improvement")
    if mi is None:
        m = _MONEY_RE.search(str(run.get("reason") or ""))
        mi = float(m.group(1)) if m else None
    out["min_improvement"] = mi
    if cand_auc is not None and base_val is not None:
        out["delta_as_compared"] = round(float(cand_auc) - float(base_val), 6)
    return out


def matched_comparison(run: dict, champ_matched: dict) -> dict:
    """후보와 **같은 눈금**의 챔피언 값으로 다시 비교한다(가능할 때만)."""
    cand_auc = run.get("auc")
    fam = family(run.get("candidate_metric"))
    mi = run.get("min_improvement")
    if mi is None:
        out_mi = 0.02
    else:
        out_mi = float(mi)
    ref = champ_matched.get(fam)
    if cand_auc is None or ref is None or ref.get("value") is None:
        return {"available": False, "family": fam, "min_improvement": out_mi}
    delta = float(cand_auc) - float(ref["value"])
    return {"available": True, "family": fam, "baseline_value": ref["value"],
            "baseline_source": ref["source"], "baseline_metric": ref["metric"],
            "delta": round(delta, 6), "min_improvement": out_mi,
            "auc_leg_pass": delta >= out_mi - 1e-9}


def first_blocking_leg(reason: str) -> str:
    r = str(reason or "")
    if "돈 기준 게이트 거부" in r:
        return "money"
    if "하한 미달" in r:
        return "auc_floor"
    if "did not beat champion baseline" in r:
        return "auc_beat"
    if not r:
        return "unknown"
    return "other"


def champion_matched_values(champ_dir=CHAMPION_DIR) -> dict:
    """눈금별 챔피언 값 — 게이트가 안 쓰지만 같은 눈금 비교에 필요한 값."""
    out = {}
    d = _read_json(os.path.join(champ_dir, "robust_auc.json"))
    if d:
        out["single_split"] = {"value": d.get("robust_auc"), "metric": d.get("metric"),
                               "source": "robust_auc.json"}
    d = _read_json(os.path.join(champ_dir, "robust_oos.json"))
    if d:
        out["robust"] = {"value": d.get("robust_auc"), "metric": d.get("metric"),
                         "source": "robust_oos.json",
                         "protocol": str(d.get("protocol") or "")[:120]}
    return out


def collect(ml=ML, proj=PROJ) -> dict:
    reg = _read_json(REGISTERED_BASELINE) or {}
    champ = champion_matched_values(os.path.join(ml, "app", "models", "champion"))
    runs = []
    seen = set()
    paths = sorted(glob.glob(os.path.join(ml, "app", "reports", "ml_result*.json"))
                   + glob.glob(os.path.join(proj, "reports", "ml_result*.json")))
    for p in paths:
        d = _read_json(p)
        if not isinstance(d, dict):
            continue
        # 같은 실행이 여러 경로에 복사돼 있을 수 있다(예: <repo>/reports/ml_result.json 사본) →
        # (status, 후보값, 결정시각, reason) 로 중복 제거해 요약 카운터가 부풀지 않게 한다.
        key = (d.get("status"), d.get("auc"), d.get("candidate_metric"),
               d.get("decided_at") or d.get("retrained_at"), str(d.get("reason") or "")[:80])
        if key in seen:
            continue
        seen.add(key)
        y = yardstick_of_run(d)
        y["file"] = os.path.relpath(p, proj)
        y["status"] = d.get("status")
        y["reason"] = str(d.get("reason") or "")[:200]
        y["decided_at"] = d.get("decided_at") or d.get("retrained_at")
        y["first_blocking_leg"] = first_blocking_leg(d.get("reason"))
        mc = matched_comparison(d, champ)
        y["matched"] = mc
        y["auc_leg_pass_as_compared"] = (
            None if y["delta_as_compared"] is None or y["min_improvement"] is None
            else y["delta_as_compared"] >= float(y["min_improvement"]) - 1e-9)
        # 뒤집힘: 눈금 불일치 + 비교(불일치)로는 AUC 다리 탈락 + 같은 눈금으로는 통과
        y["flip_if_matched"] = bool(
            y["yardstick_mismatch"] and mc.get("available")
            and y["auc_leg_pass_as_compared"] is False and mc.get("auc_leg_pass") is True)
        runs.append(y)

    cand_evidence = []
    for d in sorted(glob.glob(os.path.join(ml, "app", "models", "*", "robust_oos.json"))):
        j = _read_json(d) or {}
        cand_evidence.append({
            "dir": os.path.relpath(os.path.dirname(d), proj),
            "robust_auc": j.get("robust_auc"), "metric": j.get("metric"),
            "expectancy_pct": j.get("expectancy_pct"), "expectancy_t": j.get("expectancy_t"),
        })

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "registered_baseline": {"value": reg.get("robust_auc"), "metric": reg.get("metric"),
                                "source": "champion/robust_auc.json",
                                "protocol": str(reg.get("protocol") or "")[:120],
                                "recorded_at": reg.get("recorded_at")},
        "champion_matched_values": champ,
        "gate_runs": runs,
        "candidate_evidence": cand_evidence,
        "summary": {
            "n_gate_runs": len(runs),
            "n_yardstick_mismatch": sum(1 for r in runs if r["yardstick_mismatch"]),
            "n_flip_if_matched": sum(1 for r in runs if r["flip_if_matched"]),
            "n_latent_mismatch_not_decisive": sum(
                1 for r in runs if r["yardstick_mismatch"] and r["first_blocking_leg"] != "auc_beat"),
        },
    }


def render(res: dict) -> str:
    L = []
    rb = res["registered_baseline"]
    L.append(f"등록 기준선(champion/robust_auc.json): {rb['value']} · metric={rb['metric']} · "
             f"protocol={rb['protocol']}")
    for fam, v in (res["champion_matched_values"] or {}).items():
        L.append(f"  챔피언 같은 눈금 값[{fam}] = {v['value']} ({v['source']}, metric={v['metric']})")
    L.append("")
    L.append("게이트 실행 요약별 눈금:")
    for r in res["gate_runs"]:
        mc = r["matched"]
        L.append(f"  - {os.path.basename(r['file'])} @{r.get('decided_at')} status={r['status']}")
        L.append(f"      후보 {r['candidate_auc']} ({r['candidate_metric']}/{r['candidate_family']})"
                 f" vs 기준선 {r['baseline']} ({r['baseline_metric']}/{r['baseline_family']})"
                 f" → Δ{r['delta_as_compared']}")
        if not mc.get("available"):
            matched_txt = "불가"
        else:
            matched_txt = (f"Δ{mc['delta']} (기준 {mc['baseline_value']}, "
                           f"통과={mc['auc_leg_pass']})")
        L.append(f"      불일치={r['yardstick_mismatch']} · 1차 차단 다리={r['first_blocking_leg']} · "
                 f"같은 눈금 재비교={matched_txt}")
        L.append(f"      뒤집힘(눈금 수리 시 AUC 다리 통과)={r['flip_if_matched']} · reason={r['reason'][:90]}")
    s = res["summary"]
    L.append("")
    L.append(f"요약: 게이트 실행 {s['n_gate_runs']}건 · 눈금 불일치 {s['n_yardstick_mismatch']}건 · "
             f"뒤집힘 {s['n_flip_if_matched']}건 · "
             f"불일치이나 결정적이지 않았던 건 {s['n_latent_mismatch_not_decisive']}건")
    return "\n".join(L)


# ── 자체점검 (합성 입력 — 순수 함수만) ─────────────────────────────────────────
def selftest() -> int:
    champ = {"single_split": {"value": 0.5513, "metric": "ensemble_auc", "source": "robust_auc.json"},
             "robust": {"value": 0.4908, "metric": "robust_auc", "source": "robust_oos.json"}}
    ok = 0
    bad = []

    def chk(name, cond):
        nonlocal ok
        if cond:
            ok += 1
        else:
            bad.append(name)

    # ① CG168 실측 재현: 후보 robust 0.5284 vs 기준선 ensemble_auc 0.5513 → 불일치·덜어짐·뒤집힘
    run = {"auc": 0.5284, "candidate_metric": "robust_auc", "champion_baseline": 0.5513,
           "champion_baseline_source": "robust_auc.json",
           "reason": "candidate robust_auc 0.5284 did not beat champion baseline 0.5513 "
                     "(robust_auc.json) (+0.0200 required)"}
    y = yardstick_of_run(run)
    chk("mismatch_detected", y["yardstick_mismatch"] is True)
    chk("cand_family", y["candidate_family"] == "robust" and y["baseline_family"] == "single_split")
    chk("min_improvement_parsed", y["min_improvement"] == 0.02)
    chk("delta_as_compared", abs(y["delta_as_compared"] - (-0.0229)) < 1e-9)
    mc = matched_comparison(run, champ)
    chk("matched_available", mc["available"] is True and abs(mc["delta"] - 0.0376) < 1e-6)
    chk("matched_pass", mc["auc_leg_pass"] is True)
    chk("flip", (y["delta_as_compared"] < 0.02) and mc["auc_leg_pass"] is True)

    # ② 같은 눈금(단일분할 후보 vs 단일분할 기준선)은 불일치가 아니다
    y2 = yardstick_of_run({"auc": 0.541, "candidate_metric": "ensemble_auc",
                           "champion_baseline": 0.5513, "champion_baseline_source": "robust_auc.json",
                           "reason": "candidate ensemble_auc 0.5410 did not beat champion baseline "
                                     "0.5513 (robust_auc.json) (+0.0200 required)"})
    chk("same_yardstick_not_mismatch", y2["yardstick_mismatch"] is False)

    # ③ 눈금 불일치지만 같은 눈금으로도 탈락 → 뒤집힘 아님
    run3 = dict(run, auc=0.45)
    mc3 = matched_comparison(run3, champ)
    chk("no_flip_when_matched_fails", mc3["auc_leg_pass"] is False)

    # ④ 같은 눈금 값이 없으면 matched 불가(예외 없이)
    chk("no_ref_no_crash", matched_comparison(run, {})["available"] is False)

    # ⑤ 1차 차단 다리 분류
    chk("leg_money", first_blocking_leg("돈 기준 게이트 거부: 분할표본 불안정") == "money")
    chk("leg_auc", first_blocking_leg(run["reason"]) == "auc_beat")
    chk("leg_floor", first_blocking_leg("다중 폴드 OOS AUC 하한 미달: 후보 0.4737 < 0.5000") == "auc_floor")
    chk("leg_unknown", first_blocking_leg("") == "unknown")

    # ⑥ 요약 카운터
    res = {"gate_runs": [dict(yardstick_of_run(run), matched=matched_comparison(run, champ),
                              delta_as_compared=-0.0229, first_blocking_leg="auc_beat",
                              auc_leg_pass_as_compared=False, flip_if_matched=True,
                              file="x.json", status="kept_incumbent", reason="")]}
    chk("summary_counts", res["gate_runs"][0]["flip_if_matched"] is True)

    print(f"selftest: {ok} PASS / {len(bad)} FAIL" + ("" if not bad else f" → {bad}"))
    return 0 if not bad else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="승격 게이트 AUC 다리 프로토콜 혼용 감사(읽기 전용)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--min-improvement", type=float, default=None,
                    help="요약에 min_improvement 가 없을 때 쓸 기본값(표시용)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()

    res = collect()
    if a.min_improvement is not None:
        for r in res["gate_runs"]:
            if r["min_improvement"] is None:
                r["min_improvement"] = a.min_improvement
    out = a.out or os.path.join(PROJ, "data", "reports",
                                f"promote_gate_leg_audit_{datetime.now():%Y%m%d}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(render(res))
    print(f"\n증거 JSON: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

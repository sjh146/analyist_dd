#!/usr/bin/env python3
"""champion_promote 하한 게이트(CG135) 회귀 자체점검 — 기본 OFF = 종전 동작.

WHY (2026-10-08 실측, 백로그 CG135)
- `config/objective.json` 의 `goal.acceptance` 는 `min_robust_auc: 0.5` · `min_live_signals: 1` 을
  선언하는데 **어떤 .py/.sh 도 읽지 않는다**(grep 0건) → 선언만 된 기준.
- 사후 실례 = CG133(원장 2026-10-07T22:00:13): 후보 `robust_oos.json` 0.4737 < 0.5 인데
  dry-run 이 `would_promote`(비교 대상이 in-sample ensemble_auc 0.6083). 같은 후보의 순기대
  t 는 0.73(95% CI 0 포함)인데 `_expectancy_verdict` 에 t 요건이 없다 — 닫을 때 t≥2, 열 때 없음(비대칭).

이 스크립트가 확인하는 것:
 ① 새 플래그 **미지정(None)이면 종전 동작과 동일**(되돌리기 가능 — 프로덕션 무변경)
 ② `--min-robust-auc 0.5` 는 robust 0.4737 후보를 정확히 그 하한에서 막는다
 ③ `--min-expectancy-t 2.0` 은 t 0.73 후보를 막는다
 ④ 거부 분기에도 판정 수치(AUC·기준선)가 남는다(사후 재판정 가능 — CG122 갭 재발 방지)
 ⑤ 증거 파일(robust_oos.json) 부재도 '없음'으로 막는다(조용한 통과 금지)
 ⑥ CLI 배선: 요약 JSON payload 에 `acceptance_floors` 가 실린다

실행(호스트, KIS·DB 호출 없음):  python3 scripts/_promote_floor_gate_test.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "services", "xgboost-ml"))
sys.path.insert(0, REPO)

from app.training import champion_promote as cp  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def _check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


def _make_dir(root: str, name: str, auc: float, features: int = 173) -> str:
    d = os.path.join(root, name)
    os.makedirs(d, exist_ok=True)
    for f in cp.MODEL_FILES:
        with open(os.path.join(d, f), "wb") as fh:
            fh.write(b"x")
    with open(os.path.join(d, "feature_names.json"), "w") as fh:
        json.dump([f"f{i}" for i in range(features)], fh)
    with open(os.path.join(d, "auc.txt"), "w") as fh:
        fh.write(f"{auc}\n")
    with open(os.path.join(d, "training-result-20261008-000000.json"), "w") as fh:
        json.dump({"ensemble_auc": auc, "n_rows": 12000, "n_features": features,
                   "model_aucs": {"xgboost": auc}, "up_rate": 0.48}, fh)
    return d


def _evidence(model_dir: str, *, pct: float, t: float = 1.5, sessions: int = 87,
              trades: int = 1700, halves=(0.60, 0.30), auc: float = 0.52) -> None:
    """승격 게이트가 읽는 증거 파일(model_metric_protocol_audit.py 산출 형식)."""
    rec = {"robust_auc": auc, "metric": "robust_auc", "expectancy_pct": pct,
           "expectancy_t": t, "n_sessions": sessions, "n_trades": trades,
           "halves": {"front_pct": halves[0], "back_pct": halves[1],
                      "stable": ("both_positive" if min(halves) > 0 else "unstable")},
           "protocol": "5-fold ... · top3 · 익일 시가", "created_at": "2026-10-08T00:00:00"}
    with open(os.path.join(model_dir, "robust_oos.json"), "w", encoding="utf-8") as fh:
        json.dump(rec, fh)


def _token(path: str, candidate: str) -> None:
    # ts 를 반드시 넣는다 — 게이트가 '토큰 시각 불명'으로 닫히면 승격 경로 검증이 불가해진다.
    from datetime import datetime
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"candidate": candidate, "status": "passed",
                   "detail": "신호 12건 · 최대 0.58",
                   "ts": datetime.now().isoformat(timespec="seconds")}, fh)


def _run(cand: str, champ: str, **kw):
    kw.setdefault("min_auc", 0.53)
    kw.setdefault("min_improvement", 0.02)
    return cp.promote(cand, champ, **kw)


def main() -> int:
    root = tempfile.mkdtemp(prefix="cg135_")
    token = os.path.join(root, "promote_live_score_gate.json")
    old_gate, old_env = cp.LIVE_SCORE_GATE_PATH, os.environ.pop("PROMOTE_REQUIRE_ROBUST", None)
    cp.LIVE_SCORE_GATE_PATH = token

    def pair(tag: str):
        """새 후보/챔피언 쌍. 하한 게이트 검사는 dry-run 으로만 한다 —
        실제 승격은 champion/ 을 덮어써 다음 케이스의 기준선을 바꾼다(실측 함정)."""
        c = _make_dir(root, f"cand_{tag}", 0.6000)
        h = _make_dir(root, f"champ_{tag}", 0.5513)
        _token(token, os.path.basename(c))
        _evidence(h, pct=0.10)
        return c, h

    try:
        # ① 기본값 = 종전 동작 (플래그 미지정 → 프로덕션 무변경)
        cand, champ = pair("a")
        _evidence(cand, pct=0.45)
        res = _run(cand, champ, dry_run=True)
        _check("① 미지정 = 종전 동작(게이트 통과)", res["status"] == "would_promote",
               str(res.get("reason")))
        _check("① result 에 min_robust_auc/min_expectancy_t = None",
               res.get("min_robust_auc") is None and res.get("min_expectancy_t") is None)

        # ② robust 하한 미달 → 정확히 그 지점에서 거부 + 증거 보존
        _evidence(cand, pct=0.45, auc=0.4737)
        res = _run(cand, champ, min_robust_auc=0.5)
        _check("② robust 0.4737 < 0.5 → 거부", res["promoted"] is False
               and "다중 폴드 OOS AUC 하한 미달" in (res.get("reason") or ""), str(res.get("reason")))
        _check("② 거부 요약에 판정 수치 보존(CG122 갭 재발 방지)",
               res.get("candidate_auc") is not None and res.get("champion_baseline") is not None
               and res.get("candidate_robust_oos_auc") == 0.4737,
               f"auc={res.get('candidate_auc')} base={res.get('champion_baseline')} "
               f"rob={res.get('candidate_robust_oos_auc')}")

        # ③ robust 하한 통과 → 종전 흐름 계속
        _evidence(cand, pct=0.45, auc=0.52)
        res = _run(cand, champ, min_robust_auc=0.5, dry_run=True)
        _check("③ robust 0.52 ≥ 0.5 → 통과", res["status"] == "would_promote",
               str(res.get("reason")))

        # ④ 순기대 t 하한 미달 → 거부
        _evidence(cand, pct=0.157, t=0.73)
        res = _run(cand, champ, min_expectancy_t=2.0)
        _check("④ t 0.73 < 2.0 → 거부", res["promoted"] is False
               and "유의성 하한 미달" in (res.get("reason") or ""), str(res.get("reason")))

        # ⑤ t 하한 통과
        _evidence(cand, pct=0.157, t=2.5)
        res = _run(cand, champ, min_expectancy_t=2.0, dry_run=True)
        _check("⑤ t 2.5 ≥ 2.0 → 통과", res["status"] == "would_promote", str(res.get("reason")))

        # ⑥ 증거 파일 부재 → '없음'으로 막는다(조용한 통과 금지)
        os.remove(os.path.join(cand, "robust_oos.json"))
        res = _run(cand, champ, min_robust_auc=0.5)
        _check("⑥ robust_oos.json 부재 + 하한 → 거부('없음')", res["promoted"] is False
               and "없음" in (res.get("reason") or ""), str(res.get("reason")))
        res = _run(cand, champ, min_expectancy_t=2.0)
        _check("⑥ robust_oos.json 부재 + t 하한 → 거부('없음')", res["promoted"] is False
               and "없음" in (res.get("reason") or ""), str(res.get("reason")))

        # ⑦ 릴리스 경로 무회귀 — 실제 승격(promoted=True)이 여전히 된다(별도 쌍)
        cand2, champ2 = pair("b")
        _evidence(cand2, pct=0.45, auc=0.52)
        res = _run(cand2, champ2)
        _check("⑦ 실제 승격 경로 무회귀(promoted=True)", res["promoted"] is True,
               str(res.get("reason")))

        # ⑧ CLI 배선 — 요약 payload 의 acceptance_floors
        _evidence(cand, pct=0.45, auc=0.4737)   # 숫자 경로(하한 비교)를 태운다
        out = os.path.join(root, "ml_result.json")
        argv = sys.argv
        sys.argv = ["champion_promote", "--candidate", cand, "--champion", champ,
                    "--min-auc", "0.53", "--min-improvement", "0.02", "--dry-run",
                    "--min-robust-auc", "0.5", "--min-expectancy-t", "2",
                    "--summary-out", out]
        try:
            rc = cp.main()
        finally:
            sys.argv = argv
        with open(out, encoding="utf-8") as fh:
            payload = json.load(fh)
        floors = payload.get("acceptance_floors") or {}
        _check("⑧ CLI --min-robust-auc/--min-expectancy-t 배선 + payload 기록",
               rc == 0 and floors.get("min_robust_auc") == 0.5
               and floors.get("min_expectancy_t") == 2.0
               and payload.get("status") == "kept_incumbent",
               f"rc={rc} floors={floors} status={payload.get('status')}")
    finally:
        cp.LIVE_SCORE_GATE_PATH = old_gate
        if old_env is not None:
            os.environ["PROMOTE_REQUIRE_ROBUST"] = old_env
        shutil.rmtree(root, ignore_errors=True)

    print("[CG135] champion_promote 하한 게이트 자체점검")
    n_fail = 0
    for name, ok, detail in RESULTS:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if not ok else ""))
        n_fail += 0 if ok else 1
    print(f"[CG135] {len(RESULTS) - n_fail}/{len(RESULTS)} PASS")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())

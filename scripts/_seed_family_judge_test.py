#!/usr/bin/env python3
"""자가점검: 다중 시드 짝 판정(CG50) — 집계기 + 구동기 파서·판정기.

이 스택에는 pytest 가 없다 → 순수 파이썬 PASS/FAIL + sys.exit(1) 형태로 쓴다.
호스트에서 돈다(numpy 불필요 — 집계기도 구동기도 stdlib).

검증 항목:
  1. 집계기: 3시드 짝 Δ(전부 양(+)) → delta_mean·pos_seeds·se·t 정확.
  2. 구동기 parse_by_metric('champion_seed_family') + judge_by_metric → '신호있음'.
  3. Δ 가 문턱 미달(+0.01) → '노이즈'.
  4. 평균은 문턱 초과이나 부호 뒤섞임 → '노이즈(부호 불일치)'.
  5. 시드 2개 → '판정불가'(SE 과대 — CG38 교훈).
  6. summary_path 가 커맨드의 `--agg-out` 을 집어 호스트 경로로 변환.
  7. 요약 mtime <= 실행 시작 시각 → '요약 미갱신' 오류(옛 결과 오독 방지).
  8. parsed 에 per_exp 가 없다(스코어보드 arm 최고값 오독 방지 — CG31 사고 회귀).
  9. --agg-out 없이 metric 만 주면 판정불가(예외 아님 — 크래시 금지 계약).

실행: python3 scripts/_seed_family_judge_test.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import model_engineer_cycle as m  # noqa: E402

FAILS: list[str] = []
PASSES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSES if cond else FAILS).append(f"{name}{(' — ' + detail) if detail else ''}")
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")


def eval_json(path: str, auc: float, model_dir: str) -> None:
    """champion_robust_eval.py 가 쓰는 요약 스키마를 모방한 JSON 을 만든다."""
    folds = [{"fold": i + 1, "window": [f"d{i}", f"e{i}"], "n_dates": 10,
              "auc_mean": round(auc - 0.01, 4), "auc_std": 0.02} for i in range(3)]
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"measured_at": "2026-10-01T05:00:00", "model_dir": model_dir,
                   "protocol": "3-fold 연속 시간창, h=5 시장상대 중앙값 라벨, purge=5",
                   "metric": "cross_sectional_auc_mean", "robust_auc": auc,
                   "auc_std_across_folds": 0.03, "folds": folds,
                   "auc_pooled": auc, "rows_scored": 1800}, f, ensure_ascii=False)


def run_agg(tmp: str, base: list[float], cand: list[float], tag: str,
            extra: list[str] | None = None) -> tuple[int, str, dict | None]:
    paths = {"champ": [], "cand": []}
    for i, a in enumerate(base):
        p = os.path.join(tmp, f"{tag}_champ_s{i}.json")
        eval_json(p, a, "app/models/champion")
        paths["champ"].append(p)
    for i, a in enumerate(cand):
        p = os.path.join(tmp, f"{tag}_cand_s{i}.json")
        eval_json(p, a, "app/models/champion_cand")
        paths["cand"].append(p)
    out = os.path.join(tmp, f"{tag}_agg.json")
    cmd = [sys.executable, os.path.join(HERE, "champion_seed_family_agg.py"), "--agg-out", out,
           "--arm", "champ", *paths["champ"], "--arm", "cand", *paths["cand"]]
    if extra:
        cmd += extra
    r = subprocess.run(cmd, capture_output=True, text=True)
    doc = json.load(open(out, encoding="utf-8")) if os.path.exists(out) else None
    return r.returncode, (r.stdout + r.stderr), doc


def main() -> int:
    tmp = tempfile.mkdtemp()
    try:
        print("== 1. 집계기: 3시드 전부 양(+) ==")
        rc, out, doc = run_agg(tmp, [0.50, 0.52, 0.48], [0.53, 0.55, 0.51], "sig")
        check("집계기 rc=0", rc == 0, out.strip()[-200:])
        p = (doc or {}).get("paired", {})
        check("delta_mean == +0.0300", p.get("delta_mean") == 0.03, str(p.get("delta_mean")))
        check("pos_seeds == 3/3", p.get("pos_seeds") == "3/3", str(p.get("pos_seeds")))
        check("se == 0.0 (Δ 분산 0)", p.get("se") == 0.0, str(p.get("se")))
        check("집계 verdict = 신호있음", str(p.get("verdict", "")).startswith("신호있음"),
              str(p.get("verdict")))
        check("arms 요약에 champion/cand 평균",
              (doc or {}).get("arms", {}).get("champ", {}).get("mean") == 0.5
              and (doc or {}).get("arms", {}).get("cand", {}).get("mean") == 0.53,
              str((doc or {}).get("arms")))

        print("== 2. 구동기 파서·판정기 ==")
        agg_path = os.path.join(tmp, "sig_agg.json")
        item = {"id": "CG51", "metric": "champion_seed_family",
                "command": f"python scripts/champion_seed_family_agg.py --agg-out {agg_path}",
                "success": "짝 Δ ≥ +0.02 · 양(+) 전부"}
        parsed = m.parse_by_metric(item, m.summary_path("champion_seed_family", item["command"]), 0.0)
        check("parse 오류 없음", not parsed.get("error"), str(parsed.get("error")))
        check("parsed 에 per_exp 없음(스코어보드 오독 방지)", "per_exp" not in parsed)
        check("parsed.robust_auc = 챌린저 평균 0.53", parsed.get("robust_auc") == 0.53,
              str(parsed.get("robust_auc")))
        verdict, detail, delta = m.judge_by_metric(item, parsed)
        check("판정 = 신호있음", verdict == "신호있음", f"{verdict} | {detail}")
        check("delta = +0.03", delta == 0.03, str(delta))
        check("detail 에 SE·양(+) 포함", "SE" in detail and "3/3" in detail, detail[:120])

        print("== 3. Δ 문턱 미달 ==")
        _, _, doc3 = run_agg(tmp, [0.50, 0.52, 0.48], [0.51, 0.53, 0.49], "noise")
        p3 = (doc3 or {}).get("paired", {})
        check("집계 delta_mean == +0.01", p3.get("delta_mean") == 0.01, str(p3.get("delta_mean")))
        v3, d3, _ = m.judge_by_metric(item, m.parse_champion_seed_family(
            os.path.join(tmp, "noise_agg.json"), 0.0))
        check("판정 = 노이즈", v3 == "노이즈", f"{v3} | {d3}")

        print("== 4. 평균 문턱 초과·부호 뒤섞임 ==")
        _, _, doc4 = run_agg(tmp, [0.50, 0.52, 0.48], [0.55, 0.50, 0.51], "mixed")
        p4 = (doc4 or {}).get("paired", {})
        check("delta_mean == +0.02 (문턱과 동일)", p4.get("delta_mean") == 0.02,
              str(p4.get("delta_mean")))
        check("pos_seeds == 2/3", p4.get("pos_seeds") == "2/3", str(p4.get("pos_seeds")))
        v4, d4, _ = m.judge_by_metric(item, m.parse_champion_seed_family(
            os.path.join(tmp, "mixed_agg.json"), 0.0))
        check("판정 = 노이즈(부호 불일치)", v4 == "노이즈" and "부호 불일치" in d4, f"{v4} | {d4}")

        print("== 5. 시드 2개 ==")
        _, _, doc5 = run_agg(tmp, [0.50, 0.52], [0.55, 0.57], "two")
        v5, d5, _ = m.judge_by_metric(item, m.parse_champion_seed_family(
            os.path.join(tmp, "two_agg.json"), 0.0))
        check("판정 = 판정불가", v5 == "판정불가", f"{v5} | {d5}")
        check("판정불가 사유에 시드 수 표기", "2<3" in d5, d5[:140])

        print("== 6. summary_path — --agg-out 추출 ==")
        sp = m.summary_path("champion_seed_family",
                            "docker exec x sh -c 'cd /app && python scripts/a.py "
                            "--agg-out /app/reports/cg51_seed_family.json'")
        check("/app → 호스트 경로 변환",
              sp.endswith("services/xgboost-ml/reports/cg51_seed_family.json"),
              sp)
        check("--agg-out 없으면 빈 경로(예외 금지)",
              m.summary_path("champion_seed_family", "python x.py") == "")
        v6, d6, _ = m.judge_by_metric(item, m.parse_by_metric(
            {"metric": "champion_seed_family", "command": "python x.py"}, "", 0.0))
        check("경로 없음 → 판정불가(크래시 아님)", v6 == "판정불가", f"{v6} | {d6}")

        print("== 7. 낡은 요약(mtime <= 실행 시작) ==")
        stale = m.parse_champion_seed_family(agg_path, time.time() + 10)
        check("요약 미갱신으로 오류 반환", "미갱신" in str(stale.get("error")),
              str(stale.get("error"))[:80])
        check("판정불가로 귀결", m.judge_by_metric(item, stale)[0] == "판정불가")

        print("== 8. arm 1개면 오류 ==")
        r = subprocess.run([sys.executable, os.path.join(HERE, "champion_seed_family_agg.py"),
                            "--agg-out", os.path.join(tmp, "x.json"), "--arm", "champ",
                            os.path.join(tmp, "sig_champ_s0.json")],
                           capture_output=True, text=True)
        check("arm 1개 → rc=2", r.returncode == 2, f"rc={r.returncode}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n총 {len(PASSES)} PASS / {len(FAILS)} FAIL")
    for f in FAILS:
        print("  FAIL:", f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""--universe-seed 플래그 e2e 자체점검(컨테이너에서 실행).

왜: CG46 실측에서 단일 유니버스의 짝 Δ(+0.0226)가 나왔지만, 유니버스 정체만 바꿔도 폴드 평균이
Δ0.0287 움직인다(CG13) — 여러 시드에서 부호가 유지되는지 확인해야 승격 근거가 된다. 그 배관 검증.
"""
import json
import subprocess
import sys

FAIL = 0
HOST_REPORTS = "services/xgboost-ml/app/reports"

TMPL = ("cd /app && OMP_NUM_THREADS=2 timeout 900 python -u scripts/champion_robust_eval.py "
        "--model-dir app/models/champion --universe training --label-kind rel --horizon 5 "
        "--folds 5 --dates-per-fold 2 --stocks 25 --train-start 2026-06-25 --train-end 2026-09-23 "
        "--universe-seed {seed} --out app/reports/_useed_{seed}{tag}.json")
# 주의: --folds 1 로 줄이면 창이 1개뿐이라 학습구간과 겹쳐 전부 제외되고 "OOS 창 0개" rc=2 가 된다.
# 창 경계는 (folds, dates-per-fold) 로 정해지므로 folds=5 를 유지해야 3개 OOS 창이 남는다.


def sh(cmd, env_extra=""):
    return subprocess.run(["docker", "exec", "stock_xgboost_ml", "sh", "-c", cmd],
                          capture_output=True, text=True)


def run(seed, tag=""):
    p = sh(TMPL.format(seed=seed, tag=tag))
    try:
        with open(f"{HOST_REPORTS}/_useed_{seed}{tag}.json") as f:
            return json.load(f), p.returncode
    except Exception as e:
        return None, f"rc={p.returncode} {e} {p.stdout[-200:]} {p.stderr[-200:]}"


def check(name, cond, extra=""):
    global FAIL
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f" — {extra}"))
    if not cond:
        FAIL += 1


h = sh("cd /app && python scripts/champion_robust_eval.py --help")
check("--universe-seed 가 CLI 에 노출된다", "--universe-seed" in h.stdout, h.stdout[-300:])

a, ra = run(0)
b, rb = run(1)
check("seed0 실행 성공", a is not None and ra == 0, ra)
check("seed1 실행 성공", b is not None and rb == 0, rb)

if a and b:
    ua, ub = a.get("universe", {}), b.get("universe", {})
    check("universe.seed 가 0/1 로 기록된다", ua.get("seed") == 0 and ub.get("seed") == 1, f"{ua} {ub}")
    check("두 arm 모두 ETF/ETN 0종목", ua.get("n_etf_etn") == 0 and ub.get("n_etf_etn") == 0, f"{ua} {ub}")
    check("두 arm 모두 채점 행 > 0", a.get("rows_scored", 0) > 0 and b.get("rows_scored", 0) > 0, "")

    probe = sh("cd /app && PYTHONPATH=/app LIM=25 python scripts/_useed_probe.py")
    print(probe.stdout.strip() or probe.stderr.strip()[-300:])
    check("seed0 재현 · seed0≠seed1 · seed0≠seed2",
          "seed0 재현(len, 동일여부): 25 True" in probe.stdout
          and "seed0 vs seed1 교집합: 25" not in probe.stdout
          and "seed0 vs seed2 교집합: 25" not in probe.stdout, probe.stdout[-200:])

c2, rc2 = run(0, tag="r")
if c2 is not None:
    check("같은 seed 두 번 → 견고 AUC 동일(결정적)",
          abs((a or {}).get("robust_auc", -1) - c2.get("robust_auc", -2)) < 1e-9,
          f"{a and a.get('robust_auc')} vs {c2.get('robust_auc')}")
else:
    check("같은 seed 두 번 → 재현", False, str(rc2))

print(f"\n{'ALL PASS' if not FAIL else str(FAIL) + ' FAIL'}")
sys.exit(1 if FAIL else 0)

#!/usr/bin/env python3
"""CG135 배선 패치 검증 — `data/reports/cg135_promote_flags_floor_wiring.patch` (오프라인, 실행/승격 없음).

WHY (2026-10-08, 엔지니어)
- `config/objective.json` 의 `goal.acceptance.min_robust_auc(0.5)`·`min_live_signals(1)` 은 **선언만**
  되고 어떤 코드도 읽지 않아, robust AUC 0.4737 후보가 돈 게이트를 통과했다(CG133 실측).
  `champion_promote` 쪽 하한 플래그(`--min-robust-auc`·`--min-expectancy-t`, 기본 None = 프로덕션
  비트 동일)는 CG135 에서 이미 구현·검증됐다(_promote_floor_gate_test.py 11/11). 남은 것은
  `scripts/objective.py::promote_flags` 가 그 플래그를 **내보내는** 배선뿐인데, objective.py 는
  보호경로(protected_paths.py)라 이 역할이 직접 고칠 수 없다 → 승인 즉시 `git apply` 한 줄로
  끝나도록 패치를 만들어 두고, 여기서 (a) 클린 적용 (b) 게이트 OFF 에서 무변경(비트 동일)
  (c) 게이트 ON 에서 하한이 실제로 나감 (d) 내보낸 플래그가 전부 champion_promote 가 받는 인자인가
  를 오프라인으로 증명한다.

사용: python3 scripts/_cg135_wiring_verify.py
"""
from __future__ import annotations

import copy
import importlib.util
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
REL_OBJ = "scripts/objective.py"
REL_TEST = "tests/test_objective.py"
SRC_OBJ = REPO / REL_OBJ
PATCH = REPO / "data/reports/cg135_promote_flags_floor_wiring.patch"
CHAMP = REPO / "services/xgboost-ml/app/training/champion_promote.py"


def load_module(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def supported_args() -> set[str]:
    """champion_promote argparse 가 실제로 받는 `--flag` 집합(소스에서 직접 파싱)."""
    txt = CHAMP.read_text(encoding="utf-8")
    return set(re.findall(r'add_argument\(\s*"(--[a-z0-9\-]+)"', txt))


def base_obj(**gates) -> dict:
    """objective.json 스키마 최소형 — gates 만 바꿔 끼운다."""
    real = json.loads((REPO / "config/objective.json").read_text(encoding="utf-8"))
    obj = copy.deepcopy(real)
    obj.setdefault("gates", {}).update(gates)
    return obj


def heads(flags: list[str]) -> list[str]:
    return [f.split()[0] for f in flags]


def main() -> int:
    results: list[tuple[str, bool]] = []
    if not PATCH.exists():
        print(f"[FAIL] 패치 파일 없음: {PATCH}")
        return 1

    # 1) 현 파일에 클린 적용되는가(승인 즉시 한 줄 적용 가능)
    r = subprocess.run(["git", "apply", "--check", "-p1", str(PATCH)],
                       cwd=str(REPO), capture_output=True, text=True)
    results.append(("현 파일에 git apply --check 통과(승인 즉시 적용 가능)", r.returncode == 0))
    if r.returncode != 0:
        print(r.stderr)

    # 2) 게이트 OFF(현행 objective.json 그대로) → 현행/패치본 출력이 완전히 같은가
    mod_now = load_module(SRC_OBJ, "objective_now")
    flags_now = mod_now.promote_flags(base_obj())
    print(f"[현행] gates(promote_require_robust={mod_now.load().get('gates', {}).get('promote_require_robust')}) "
          f"→ {flags_now}")

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="cg135verify_"))
    try:
        for rel in (REL_OBJ, REL_TEST):
            dst = tmp / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / rel, dst)
        ap = subprocess.run(["patch", "-p1", "-s", "-i", str(PATCH)], cwd=str(tmp),
                            capture_output=True, text=True)
        results.append(("패치가 사본에 조용히 적용", ap.returncode == 0))
        if ap.returncode != 0:
            print(ap.stdout, ap.stderr)
            return 1

        mod_fix = load_module(tmp / REL_OBJ, "objective_fix")
        flags_fix_default = mod_fix.promote_flags(base_obj())
        print(f"[수리] 게이트 OFF 그대로 → {flags_fix_default}")
        results.append(("게이트 OFF: 패치 전후 출력 동일(프로덕션 비트 동일)",
                        flags_fix_default == flags_now))

        # 3) 게이트 ON → 하한이 실제로 나가는가
        off_flags = mod_now.promote_flags(base_obj(promote_require_robust=True))
        print(f"[현행] promote_require_robust=True → {off_flags}")
        results.append(("현행 결함 재현: 게이트를 켜도 min_robust_auc 가 강제되지 않는다",
                        "--min-robust-auc" not in heads(off_flags)))

        on_flags = mod_fix.promote_flags(base_obj(promote_require_robust=True))
        print(f"[수리] promote_require_robust=True → {on_flags}")
        results.append(("수리: --require-robust 와 함께 --min-robust-auc 0.5 가 나간다",
                        "--require-robust" in heads(on_flags)
                        and "--min-robust-auc 0.5" in on_flags))

        t_flags = mod_fix.promote_flags(base_obj(promote_require_robust=True,
                                                 promote_require_expectancy_t=True))
        print(f"[수리] +promote_require_expectancy_t=True → {t_flags}")
        results.append(("수리: --min-expectancy-t 2.0 이 나간다",
                        "--min-expectancy-t 2.0" in t_flags))
        t_off = mod_fix.promote_flags(base_obj())
        results.append(("수리: t 게이트 미선언이면 --min-expectancy-t 는 나가지 않는다",
                        "--min-expectancy-t" not in heads(t_off)))

        # 4) 선언 키가 실제로 소비되는가 — acceptance 키별 grep 소비처(이 패치 후 기준)
        acc_keys = json.loads((REPO / "config/objective.json").read_text(encoding="utf-8"))["goal"]["acceptance"]
        src_all = (tmp / REL_OBJ).read_text(encoding="utf-8") + CHAMP.read_text(encoding="utf-8")
        for k in ("min_robust_auc", "min_expectancy_pct", "min_sample_sessions", "min_sample_trades"):
            results.append((f"acceptance.{k} 가 코드에서 읽힌다", f'"{k}"' in src_all or f"'{k}'" in src_all))
        results.append(("acceptance 키 목록 기록(은퇴 판정용)",
                        isinstance(acc_keys, dict) and "min_robust_auc" in acc_keys))

        # 5) 내보낸 플래그 전부가 champion_promote 가 받는 인자인가
        sup = supported_args()
        bad = [f for f in on_flags + t_flags if f.split()[0] not in sup]
        print(f"[검사] champion_promote 지원 인자 {len(sup)}종 · 미지원 = {bad or '없음'}")
        results.append(("내보낸 플래그 전부 champion_promote 지원 인자",
                        not bad and "--min-robust-auc" in sup and "--min-expectancy-t" in sup))

        # 6) 회귀 테스트의 allowed 집합이 갱신됐는가(계약 변경 동반)
        test_txt = (tmp / REL_TEST).read_text(encoding="utf-8")
        results.append(("tests/test_objective.py allowed 집합에 신규 플래그 반영",
                        '"--min-robust-auc"' in test_txt and '"--min-expectancy-t"' in test_txt))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    n_fail = 0
    for name, ok in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        n_fail += 0 if ok else 1
    print()
    print(f"[CG135-WIRING] {len(results) - n_fail}/{len(results)} PASS")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())

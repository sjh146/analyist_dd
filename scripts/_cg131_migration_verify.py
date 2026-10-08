#!/usr/bin/env python3
"""CG131 이관 패치 검증 — `data/reports/cg131_scoreboard_migration.patch` (읽기 전용, 파일 미변경).

WHY (2026-10-08, 엔지니어)
- 북극성 헤드라인은 "로버스트 0.5519(TR_rank_h5) vs 기준선 0.5406 → Δ0.0113 · 19사이클 무개선"이다.
  그런데 (a) 비교 패널 panel_420_asofpatch.npz 는 as-of 수리 이전 빌드(panel_leak_gate → LEAKY),
  (b) 헤드라인 arm TR_rank_h5 는 횡단면 rank 변환이라 배포 추론 경로(app/inference/predictor.py:137,
  종목 단위 스트리밍)에서 재현 불가다. 이관은 스코어보드 프로토콜 변경 = 리뷰보드 승인 대상이므로
  이 역할이 직접 적용하지 않는다 → 승인 즉시 `git apply` 한 줄로 끝나도록 패치를 만들어 두고,
  여기서 (a) 클린 적용 (b) 청정 패널 기준선 값으로 헤드라인이 실제로 바뀌는가 (c) rank arm 이
  대조가능 후보에서 빠지는가 (d) 짝 회귀 테스트가 패치본에서 통과하는가를 오프라인으로 증명한다.

사용: python3 scripts/_cg131_migration_verify.py
"""
from __future__ import annotations

import importlib.util
import pathlib
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
REL_Q = "scripts/quant_scoreboard.py"
REL_T = "scripts/_scoreboard_comparability_test.py"
PATCH = REPO / "data/reports/cg131_scoreboard_migration.patch"
P = {"kind": "quantile", "horizon": 5}


def load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def repoint(mod, out_dir: pathlib.Path) -> None:
    """패치 사본의 PROJ 파생 경로를 실제 레포로 되돌린다(사본 위치 때문에 원장을 못 읽는 것 방지)."""
    mod.PROJ = str(REPO)
    mod.ME_LEDGER = str(REPO / "data/reports/model_engineer_ledger.jsonl")
    mod.RES_LEDGER = str(REPO / "data/reports/researcher_ledger.jsonl")
    mod.DQ_GLOB = str(REPO / "data/reports/dq_snapshots/dq_*.json")
    mod.ARM_CONFIGS_PATH = str(REPO / "scripts/wf_label_sweep.py")
    mod.OUT_JSON = str(out_dir / "quant_scoreboard.json")
    mod.OUT_MD = str(out_dir / "QUANT_SCOREBOARD.md")
    mod._ARM_TAGS_CACHE = None
    mod._UNIV_CACHE = None


def main() -> int:
    results: list[tuple[str, bool]] = []
    if not PATCH.exists():
        print(f"[FAIL] 패치 파일 없음: {PATCH}")
        return 1

    r = subprocess.run(["git", "apply", "--check", "-p1", str(PATCH)],
                       cwd=str(REPO), capture_output=True, text=True)
    results.append(("현 파일에 git apply --check 통과(승인 즉시 적용 가능)", r.returncode == 0))
    if r.returncode != 0:
        print(r.stderr)
        return 1

    # ① 현행(미적용) — 결함 재현: 기준선이 누수 패널 값이고, rank arm 이 헤드라인
    cur = load("qs_current", REPO / REL_Q)
    cur_st = cur.engineer_stanza()
    print(f"[현행] 기준선 {cur_st['baseline']} ({cur_st['comparability']['panels_comparable']}) "
          f"→ 헤드라인 {cur_st['best_robust']} ({cur_st['best_exp']}) Δ{cur_st['delta']} "
          f"· 무개선 꼬리 {cur_st['no_improve_streak']}")
    okc, whyc = cur.arm_comparability("TR_rank_h5", P)
    results.append(("현행 결함 재현: 기준선 = 누수 패널 0.5406", cur.BASELINE_ROBUST == 0.5406))
    results.append(("현행 결함 재현: rank arm(TR_rank_h5) 이 대조가능으로 계상된다",
                    okc is True))
    results.append(("현행 결함 재현: 헤드라인이 비배포 rank arm",
                    cur_st.get("best_exp") == "TR_rank_h5"))

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="cg131verify_"))
    try:
        for rel in (REL_Q, REL_T):
            dst = tmp / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / rel, dst)
        ap = subprocess.run(["patch", "-p1", "-s", "-i", str(PATCH)], cwd=str(tmp),
                            capture_output=True, text=True)
        results.append(("패치가 사본에 조용히 적용", ap.returncode == 0))
        if ap.returncode != 0:
            print(ap.stdout, ap.stderr)
            return 1

        mod = load("quant_scoreboard", tmp / REL_Q)      # 테스트가 import 하는 이름
        repoint(mod, tmp)
        st = mod.engineer_stanza()
        print(f"[수리] 기준선 {st['baseline']} ({st['comparability']['panels_comparable']}) "
              f"→ 헤드라인 {st['best_robust']} ({st['best_exp']}) Δ{st['delta']} "
              f"· 무개선 꼬리 {st['no_improve_streak']}")

        results.append(("수리: 기준선 = 청정 패널 실측 0.5302", mod.BASELINE_ROBUST == 0.5302))
        results.append(("수리: 헤드라인 패널이 청정(prod200)으로 고정",
                        st["comparability"]["panels_comparable"] == ["panel_prod200.npz"]))
        results.append(("수리: 헤드라인 arm 이 배포 가능(rank 아님)",
                        "rank" not in str(st.get("best_exp"))))
        results.append(("수리: Δ 가 새 기준선 기준으로 계산됨",
                        st["delta"] == round(float(st["best_robust"]) - 0.5302, 4)))
        results.append(("수리: 대조가능 arm 이 0개가 아니다",
                        (st.get("comparability") or {}).get("arms_comparable", 0) > 0))
        results.append(("수리: 최고 '대조 불가' arm 각주는 보존(Q5s_120_150)",
                        (st.get("best_excluded") or {}).get("arm") == "Q5s_120_150"))

        okn, whyn = mod.arm_comparability("TR_rank_h5", P)
        print(f"[수리] TR_rank_h5 → ok={okn} {whyn}")
        results.append(("수리: rank 변환 arm 이 대조 제외", okn is False and "rank" in whyn))
        okc2, whyc2 = mod.arm_comparability("LS_quant_q30_h5", P)
        results.append(("수리: 기준선 arm 자체는 그대로 대조가능", okc2 is True and whyc2 == ""))

        # ② 짝 회귀 테스트(계약 변경 동반분)를 패치본으로 실행
        tpath = tmp / REL_T
        g = {"__file__": str(tpath), "__name__": "cg131_patched_test"}
        code = compile(tpath.read_text(encoding="utf-8"), str(tpath), "exec")
        exec(code, g)                      # __main__ 가드가 아니라 자동 실행되지 않는다
        rc = g["main"]()
        results.append(("패치본에서 짝 회귀 테스트(_scoreboard_comparability_test) ALL PASS",
                        rc == 0))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    n_fail = 0
    for name, ok in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        n_fail += 0 if ok else 1
    print()
    print(f"[CG131-MIGRATION] {len(results) - n_fail}/{len(results)} PASS")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())

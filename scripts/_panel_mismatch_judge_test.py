#!/usr/bin/env python3
"""자체점검 — 패널 스냅샷 불일치 → '측정무효' 판정 가드 (2026-11-12 CG199 실사고).

왜: CG199 는 앵커를 옮긴 줄 알았지만 build_panel 이 완성 패널을 그대로 재사용해 CG195 와
비트동일한 값(ctl 0.5106 · arm 0.5162)을 냈고, 구동기는 그것을 '노이즈(측정됨)'로 기록했다.
노이즈와 측정무효는 다른 주장이다 — 전자는 '축을 닫아도 된다', 후자는 '아무것도 모른다'.
실행: python3 scripts/_panel_mismatch_judge_test.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import model_engineer_cycle as M  # noqa: E402

PASS = []


def check(name, cond, detail=""):
    PASS.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + (f" — {detail}" if detail else ""))


def main():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "run.log")
        with open(p, "w", encoding="utf-8") as f:
            f.write("label_sweep start\n")
            f.write("⚠ panel meta 불일치(스냅샷이 다름) — 이 npz 는 지금 요청과 다른 조건으로 "
                    "만들어졌다: end_date: meta=2026-11-09 request=2026-11-05 → 재빌드하거나 "
                    "요청을 맞춰라\n")
            f.write("CG199_ctl_h5 AUC mean=0.5106\n")
        got = M._panel_snapshot_mismatch(p)
        check("① 불일치 줄 검출", "panel meta 불일치" in got, got[:80])

        q = os.path.join(td, "ok.log")
        with open(q, "w", encoding="utf-8") as f:
            f.write("panel meta OK (code_sig=abc · universe=curated)\n"
                    "panel as-of 슬라이스: 2026-11-09 → 2026-11-05 rows 54497 → 54xxx\n")
        check("② 정상 로그(슬라이스 포함) → 가드 미발동", M._panel_snapshot_mismatch(q) == "",
              M._panel_snapshot_mismatch(q)[:80])
        check("③ 없는 파일 → 빈 문자열(크래시 금지)", M._panel_snapshot_mismatch(q + ".nope") == "")

    ok = all(PASS)
    print(f"\n=== {sum(PASS)}/{len(PASS)} PASS ===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

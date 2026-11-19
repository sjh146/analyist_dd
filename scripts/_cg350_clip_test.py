#!/usr/bin/env python3
"""CG350 검증 — '학습구간 이후 부분창' 절단 옵션 패치의 오프라인 회귀 테스트.

패치 산출물(data/reports/cg350_partial_window.patch)을 임시 사본에 적용한 뒤
_split_oos 를 직접 실행해 5가지를 확인한다. 본체 파일은 건드리지 않는다.

  1. clip_partial=False(기본) 결과가 패치 전과 **비트 동일**
  2. clip_partial=True 면 학습구간을 가로지르는 창이 train_end+1 부터로 절단된다
  3. 같은 --train-end 를 준 두 arm 은 같은 절단 창 서명을 갖는다(window_mismatch 없음)
  4. train_end 이후 꼬리가 없는 창은 절단되지 않고 제외로 남는다
  5. 창 전체가 train_end 이후면 절단 없이 그대로 채점된다

실행: python3 scripts/_cg350_clip_test.py
"""
from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "scripts" / "champion_robust_eval.py"
PATCH = ROOT / "data" / "reports" / "cg350_partial_window.patch"

GRID = [
    ["2026-01-16", "2026-02-26"],
    ["2026-02-27", "2026-04-09"],
    ["2026-04-10", "2026-05-22"],
    ["2026-05-23", "2026-07-03"],
    ["2026-09-11", "2026-11-11"],
]


def _load(path: pathlib.Path):
    src = path.read_text()
    m = re.search(r"def _split_oos\(.*?\n(?=def )", src, re.S)
    if not m:
        raise SystemExit(f"_split_oos 를 찾지 못했습니다: {path}")
    ns: dict = {}
    exec(m.group(0), ns)  # noqa: S102 — 자기 소유 스크립트의 순수 함수만 실행
    return ns["_split_oos"]


def main() -> int:
    if not PATCH.exists():
        print(f"FAIL 패치 없음: {PATCH}")
        return 1
    chk = subprocess.run(["git", "apply", "--check", "-p1", str(PATCH)],
                         cwd=str(ROOT), capture_output=True, text=True)
    print(("PASS" if chk.returncode == 0 else "FAIL") + f" 0) git apply --check rc={chk.returncode}"
          + ("" if chk.returncode == 0 else f" :: {chk.stderr.strip()[:200]}"))
    if chk.returncode != 0:
        return 1

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="cg350_"))
    try:
        dst = tmp / "champion_robust_eval.py"
        shutil.copy(SRC, dst)
        ap = subprocess.run(["patch", "-p0", str(dst), "-i", str(PATCH)], cwd=str(tmp),
                            capture_output=True, text=True)
        # diff 헤더 경로가 scripts/... 이므로 -p1 로 재시도
        if ap.returncode != 0:
            shutil.copy(SRC, dst)
            ap = subprocess.run(["patch", "-p1", str(dst), "-i", str(PATCH)], cwd=str(tmp),
                                capture_output=True, text=True)
        if ap.returncode != 0:
            print(f"FAIL 패치 적용 실패: {ap.stderr.strip()[:300]}")
            return 1

        old = _load(SRC)
        new = _load(dst)
        ts, te = "2026-07-03", "2026-10-01"
        fails = 0

        o_keep, o_ex = old(GRID, ts, te)
        n_keep, n_ex = new(GRID, ts, te)
        c1 = (n_keep == o_keep and n_ex == o_ex)
        fails += 0 if c1 else 1
        print(f"{'PASS' if c1 else 'FAIL'} 1) OFF 비트동일 — OOS 창 {len(o_keep)}개, 제외 {len(o_ex)}개")

        on_keep, _ = new(GRID, ts, te, clip_partial=True)
        clipped = [w for w in on_keep if w[0] == "2026-10-02"]
        c2 = len(clipped) == 1 and clipped[0] == ["2026-10-02", "2026-11-11"] and len(on_keep) == len(o_keep) + 1
        fails += 0 if c2 else 1
        print(f"{'PASS' if c2 else 'FAIL'} 2) ON 절단 창 {clipped or '-'} (전체 {len(on_keep)}개)")

        a = new(GRID, ts, te, clip_partial=True)[0]
        b = new(GRID, ts, te, clip_partial=True)[0]
        c3 = a == b
        fails += 0 if c3 else 1
        print(f"{'PASS' if c3 else 'FAIL'} 3) 두 arm 같은 서명 — {[w[0] + '~' + w[1] for w in a]}")

        late, _ = new(GRID, "2026-07-03", "2026-11-20", clip_partial=True)
        c4 = all(w[-1] <= "2026-11-20" for w in late) and len(late) == 3
        fails += 0 if c4 else 1
        print(f"{'PASS' if c4 else 'FAIL'} 4) 꼬리 없으면 제외 유지 — OOS 창 {len(late)}개")

        after = [w for w in new(GRID, "2026-07-03", "2026-07-03", clip_partial=True)[0]
                 if w[0] == "2026-09-11"]
        c5 = after == [["2026-09-11", "2026-11-11"]]
        fails += 0 if c5 else 1
        print(f"{'PASS' if c5 else 'FAIL'} 5) 학습 이후 창은 절단 없음 — {after}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"== CG350 {5 - fails}/5 PASS ==")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

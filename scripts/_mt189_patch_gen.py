#!/usr/bin/env python3
"""MT189 준비물 생성기 — trader-agent 의 RunnerConfig.screeners 기본값을 3-스크리너로 맞추는
**적용 가능한 패치**와, 현행/패치본을 대조하는 오프라인 검증을 만든다.

trader-agent 파일을 **수정하지 않는다**: 패치 파일만 data/reports 에 쓰고,
검증은 임시 사본으로만 한다(승인 전 적용 금지).
"""
import difflib
import pathlib
import subprocess
import sys

REPO = pathlib.Path('/mnt/c/Users/jhshi/analyist_dd/trader-agent')
SRC = REPO / 'runner/config.py'
OUT_PATCH = pathlib.Path('/home/jhshi/analyist_dd/data/reports/mt189_daytrading_wiring.patch')

OLD = (
    '    # --- screeners (v1 trades close + swing only) --------------------- #\n'
    '    screeners: List[str] = field(default_factory=lambda: ["close", "swing"])\n'
)
NEW = (
    '    # --- screeners ---------------------------------------------------- #\n'
    '    # 2026-10-07 사용자 승인(경로별 슬롯 swing 3 / daytrading 2)의 후속 배선.\n'
    '    # trader_core.Config.screeners 기본값(["close","swing","daytrading"])과 일치시킨다 —\n'
    '    # 종전 ["close","swing"] 는 러너 기본값이 engine_config() 로 엔진 기본값을 덮어써\n'
    '    # daytrading 후보가 계획 단계에서 평가조차 되지 않았다(MT189).\n'
    '    screeners: List[str] = field(default_factory=lambda: ["close", "swing", "daytrading"])\n'
)


def main() -> int:
    src = SRC.read_text(encoding='utf-8')
    n = src.count(OLD)
    print('anchor hits:', n, '(기대 1)')
    if n != 1:
        return 1
    mod = src.replace(OLD, NEW)
    diff = ''.join(difflib.unified_diff(
        src.splitlines(True), mod.splitlines(True),
        fromfile='a/runner/config.py', tofile='b/runner/config.py'))
    OUT_PATCH.write_text(diff, encoding='utf-8')
    print('patch ->', OUT_PATCH, '(%d bytes)' % len(diff))

    r = subprocess.run(['git', 'apply', '--check', '-p1', str(OUT_PATCH)],
                       cwd=str(REPO), capture_output=True, text=True)
    print('git apply --check rc=%s %s%s' % (r.returncode, r.stdout, r.stderr))
    return 0 if r.returncode == 0 else 1


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env bash
# u3_launcher_detach.sh — 대기형 런처를 **새 세션**으로 격리해 띄운다.
#
# WHY: 크론 턴이 끝나면 하네스가 그 턴의 자식 프로세스 그룹을 정리할 수 있고, 그러면 대기형
# 런처가 함께 죽는다 → 부하가 내려가도 아무도 시작하지 않아 밤이 통째로 빈다(2026-09-25 실측:
# 21:00 강제 시작 실패 후 11시간 빌드가 밤새 시작 못 할 뻔했다). `setsid --fork` 는 새 세션·
# 새 프로세스 그룹을 만들고 부모는 **즉시** 반환하므로, 부모가 죽어도 런처는 init 밑에서 계속 돈다.
set -uo pipefail
if [ -z "${U3_DETACHED:-}" ]; then
    export U3_DETACHED=1
    exec setsid --fork bash "$0" "$@"
fi
exec bash /home/jhshi/analyist_dd/scripts/u3_launcher.sh

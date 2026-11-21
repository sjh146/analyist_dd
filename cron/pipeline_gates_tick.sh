#!/bin/bash
# pipeline_gates_tick.sh — 일일 파이프라인 관문 점검 래퍼 (평일 23:05)
#
# 규약(레포의 다른 틱 래퍼와 동일):
#   ① 정상이면 **아무것도 출력하지 않는다**(빈 stdout = 전달 없음)
#   ② 감사 스크립트의 종료코드 2(실패)를 항상 0 으로 정규화한다
#      — 그대로 두면 스케줄러가 '잡 실패'로 해석해 별도 오류 알림을 보낸다
#   ③ 출력은 그날 관문 FAIL 상세만 남긴다(정상 로그로 덮이지 않게)
#
# 대상(2026-11-21 배선): G1 단타 후보 생성 · G2 close 상한가 제외 메타 ·
#                        G3 피드 신선도 · G4 당일 일봉 재시도 판정
set -uo pipefail
REPO=/home/jhshi/analyist_dd
out=$(cd "$REPO" && /usr/bin/python3 scripts/verify_daily_pipeline_gates.py 2>&1)
rc=$?
if [ "$rc" -ne 0 ]; then
  printf '%s\n' "$out"
fi
exit 0

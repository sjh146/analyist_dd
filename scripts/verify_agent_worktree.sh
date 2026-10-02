#!/usr/bin/env bash
# scripts/verify_agent_worktree.sh — 위임 에이전트(Claude Code/OpenCode) 산출물 검증기.
#
# WHY (2026-10-02): 위임 규약은 "검증(테스트/실행 출력) 없는 산출물은 master 병합 금지"인데,
# 사람이 worktree마다 들어가 확인하는 절차가 없으면 검증이 형식이 된다. 이 스크립트는
#  ①변경 파일 목록 ②파이썬 구문검사 ③추가된 테스트 실행 ④에이전트 보고서 존재/크기
# 를 한 번에 출력해 병합 판단을 기계적으로 만든다.
#
# 사용: bash scripts/verify_agent_worktree.sh /home/jhshi/wt/<name> [추가 테스트 경로...]
# 종료코드: 0 검증 통과 / 2 실패(구문오류·테스트 실패·보고서 없음)
set -uo pipefail
WT="${1:?worktree 경로 필요}"
shift || true
PY=/usr/bin/python3
FAIL=0

echo "=== 1. 변경 파일 ($WT) ==="
cd "$WT" || exit 2
BASE="$(git merge-base HEAD master 2>/dev/null || echo master)"
git status --short
echo "--- master 대비 diff 요약"
git diff --stat "$BASE" HEAD 2>/dev/null | tail -5 || true
N_CHANGED=$(git status --short | wc -l)
echo "변경 파일 수: $N_CHANGED"
[ "$N_CHANGED" -eq 0 ] && { echo "★ 변경 없음 — 위임이 산출물을 남기지 않았다"; FAIL=1; }

echo
echo "=== 2. 파이썬 구문검사 ==="
BAD=0
while IFS= read -r f; do
  [ -f "$f" ] || continue
  case "$f" in
    *.py) if ! "$PY" -m py_compile "$f" 2>/dev/null; then echo "  구문오류: $f"; BAD=$((BAD+1)); FAIL=1; fi ;;
    *.sh) if ! bash -n "$f" 2>/dev/null; then echo "  쉘 구문오류: $f"; BAD=$((BAD+1)); FAIL=1; fi ;;
  esac
done < <(git status --short | awk '{print $2}')
echo "  구문오류 $BAD건"

echo
echo "=== 3. 테스트 ==="
TESTS=("$@")
if [ "${#TESTS[@]}" -eq 0 ]; then
  while IFS= read -r t; do TESTS+=("$t"); done < <(git status --short | awk '{print $2}' | grep -E '^tests/.*test_.*\.py$')
fi
if [ "${#TESTS[@]}" -gt 0 ]; then
  TFAIL=0
  for t in "${TESTS[@]}"; do
    if [ -f "$t" ]; then
      out="$("$PY" -m pytest "$t" -q 2>&1 | tail -2)"
      echo "  $t → $out"
      echo "$out" | grep -qE "failed|error" && TFAIL=1
    fi
  done
  [ "$TFAIL" -eq 1 ] && { echo "★ 테스트 실패"; FAIL=1; }
else
  echo "  (추가/변경된 테스트 파일 없음 — 검증 근거 부족으로 볼 수 있음)"
fi

echo
echo "=== 4. 에이전트 보고서 ==="
find . -maxdepth 3 -name "agent_*.md" -newer .git/HEAD -print 2>/dev/null | while read -r r; do
  printf "  %s (%s bytes)\n" "$r" "$(wc -c < "$r")"
  sed -n '1,12p' "$r" | sed 's/^/    /'
done
[ -z "$(find . -maxdepth 3 -name 'agent_*.md' 2>/dev/null)" ] && { echo "★ 보고서(agent_*.md) 없음"; FAIL=1; }

echo
if [ "$FAIL" -eq 0 ]; then echo "VERDICT: PASS(병합 검토 가능)"; else echo "VERDICT: FAIL(병합 보류)"; fi
exit $([ "$FAIL" -eq 0 ] && echo 0 || echo 2)

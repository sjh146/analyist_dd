#!/usr/bin/env bash
# ask_claude.sh — 지적 작업을 Claude Code(+gstack)에 위임한다. **단독 백엔드**(2026-10-05 opencode 제거).
#
# 역할 분담 (3안 하이브리드)
#   Hermes     : 오케스트레이션·스케줄·게이트·검증·보고 (크론 5개)
#   이 스크립트 : 코드 리뷰 / 근본원인 조사 / 스펙 같은 **지적 작업** 위임 + 파일 증거화
#   1차 백엔드  : Claude Code + gstack (Anthropic 프로토콜 → DeepSeek)
#   백엔드      : Claude Code 단독. 실패하면 폴백 없이 중단(rc=1) — 원인을 고치고 재실행한다.
#
# WHY 폴백 + 무출력 감시: 위임이 한쪽 장애로 멈추면 자율 루프가 멈춘다. 실측 2026-09-25:
#   Claude Code 리뷰가 6분36초 경과에 **CPU 22초**(=대부분 API/도구 대기)로 출력 0바이트였다.
#   전체 타임아웃만 있으면 그 대기를 전부 소진한다 → **무출력 STALL 초과 시 즉시 중단·폴백**.
#
# 사용:
#   scripts/ask_claude.sh review      reports/claude_review_<대상>.md  "프롬프트"
#   scripts/ask_claude.sh investigate reports/claude_inv_<대상>.md     "프롬프트"
#   scripts/ask_claude.sh spec        docs/spec_<기능>.md              "프롬프트"
#   scripts/ask_claude.sh free        /tmp/out.md                      "프롬프트"
#
# 환경변수:
#   ASK_CLAUDE_TIMEOUT   백엔드당 총 상한(기본 600초)
#   ASK_CLAUDE_STALL_S   무출력 허용 시간(기본 180초) — 넘으면 중단하고 다음 백엔드로
#   ASK_CLAUDE_PROJ      작업 디렉터리(기본 /home/jhshi/analyist_dd)
#   ASK_CLAUDE_MIN_BYTES 성공으로 볼 최소 출력(기본 40)
#   ASK_CLAUDE_CLAUDE_BIN  테스트용 교체(구 ASK_CLAUDE_OPENCODE_BIN 은 제거됨)
#

# ── 금지 경로 가드 (2026-10-04) ────────────────────────────────────────────────
# 저작 대상이 실주문·정책 값·자격증명에 닿으면 **위임 자체를 거부**한다.
# 목록은 scripts/protected_paths.py 단일 진실원(개선 오케스트레이터와 공유).
# review/investigate 는 읽고 보고만 하므로 출력 경로만 보고, 파일을 쓰는 build 는 프롬프트(=[구현 대상])까지 본다.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/protected_paths.py" ]; then
  if [ "${1:-}" = "build" ]; then
    GUARD_ARGS=("${1:-}" "${2:-}" "${3:-}")
  else
    GUARD_ARGS=("${1:-}" "${2:-}")
  fi
  GUARD_MSG="$(/usr/bin/python3 "$SCRIPT_DIR/protected_paths.py" "${GUARD_ARGS[@]}" 2>&1)"
  GUARD_RC=$?
  if [ "$GUARD_RC" -ne 0 ]; then
    echo "[ask_claude] $GUARD_MSG" >&2
    echo "[ask_claude] 위임 거부 — 이 변경은 사람 승인 대상이다(needs_human)." >&2
    exit 2
  fi
fi

# 산출물: $OUT(결과) · $OUT.backend(어느 백엔드가 답했는지) · $OUT.stderr(.oc)
# 종료코드: 성공 0 / 두 백엔드 모두 실패 1
set -uo pipefail

MODE="${1:?mode 필요: review|investigate|spec|free}"
OUT="${2:?출력 파일 경로 필요}"
PROMPT="${3:?프롬프트 필요}"
TIMEOUT_S="${ASK_CLAUDE_TIMEOUT:-600}"
STALL_S="${ASK_CLAUDE_STALL_S:-180}"
PROJ="${ASK_CLAUDE_PROJ:-/home/jhshi/analyist_dd}"
CLAUDE_DS="${ASK_CLAUDE_CLAUDE_BIN:-$HOME/.local/bin/claude-ds}"
# 위임 에이전트가 읽어야 하는 외부 디렉터리 — trader-agent(피드/주문 경로 계약)가 리포 밖에 있다.
# 2026-09-25 실측: 이 경로가 차단돼 리뷰가 "win_rate 단위 근거를 못 찾음"으로 남겼다(실제로는
# trader_core/engine.py:859 에 정규화 코드가 있었다). 그래서 --add-dir 로 열어 준다.
EXTRA_DIR="${ASK_CLAUDE_EXTRA_DIR:-/mnt/c/Users/jhshi/analyist_dd/trader-agent}"
# MIN_BYTES: "빈 출력/에러 스텁"만 걸러내는 보조 장치다. 40 으로 두면 정상적인 짧은 답
# (예: 15바이트)을 실패로 오판한다(실측 2026-09-25) → 8 로 낮춘다. 진짜 실패는 대개
# 비정상 종료코드나 무출력으로 드러난다.
MIN_BYTES="${ASK_CLAUDE_MIN_BYTES:-8}"

case "$MODE" in
  review)
    PREAMBLE="gstack 의 /review 방법론으로 검토하라. CI 를 통과하지만 운영에서 터지는 버그를 찾는 것이 목적이다. 스타일 지적은 금지. 각 발견에 파일:라인 근거를 붙이고, 확신도(높음/중간/낮음)를 표시하라. **파일을 수정하지 말고 보고만 하라.**"
    OC_AGENT="explore"; READONLY=1 ;;
  investigate)
    PREAMBLE="gstack 의 /investigate 방법론을 따르라. 수정 없이 근본 원인을 먼저 규명한다: 관측된 증상 → 재현/증거 → 원인 후보 → 각 후보를 반증할 수 있는 확인 방법 → 최소 결론. 추측과 사실을 명시적으로 구분하라. **파일을 수정하지 말고 보고만 하라.**"
    OC_AGENT="explore"; READONLY=1 ;;
  spec)
    PREAMBLE="gstack 의 /spec 방법론으로 요구사항을 실행 가능한 스펙으로 정리하라: 목표, 비목표, 입력/출력 계약, 실패 모드, 검증 방법(테스트·측정), 롤백. 모호한 부분은 '미확정'으로 남기고 질문으로 나열하라."
    OC_AGENT=""; READONLY=0 ;;
  free)
    PREAMBLE="" ; OC_AGENT=""; READONLY=0 ;;
  build)
    # 실제 구현(저작) 모드 — **이 모드만 파일 쓰기가 허용된다**.
    # WHY(2026-09-25 사용자 승인): 자율 루프는 "등록된 명령을 실행·판정"만 할 수 있어 새 파이프라인을
    # 쓰지 못한다. 그래서 build형 백로그 항목(R10~R12: 피처 생성기 등)은 이 모드로 위임해 저작하고,
    # 산출물은 **구문검사 + 항목 check** 로 검증한 뒤에만 완료로 기록한다(검증 없는 자율 저작 금지).
    PREAMBLE="다음 스펙을 **실제로 구현하라**(파일 쓰기 허용). 규칙: ① 파일 상단 docstring 에 WHY 와 실측 근거를 남긴다 ② 추측 금지 — DB·파일에서 실제 값을 조회해 그 수치를 근거로 쓰고, 조회 명령을 주석에 남긴다 ③ 멱등하게 만든다(재실행 안전: DELETE 후 적재 또는 중복 무시) ④ 자기신고를 남긴다(소스 수신/파서 생성/실제 저장 3분리 — 가능하면 scripts/dq_claim.py 의 record_claim 재사용) ⑤ 기존 파일은 꼭 필요할 때만 최소 수정하고, 무엇을 왜 바꿨는지 보고에 적는다 ⑥ **git 명령은 실행하지 말라**(커밋은 오케스트레이터가 한다) ⑦ 검증 명령을 실제로 실행하고 그 출력을 보고에 포함하라 ⑧ 리포 밖에 파일을 만들지 말라."
    OC_AGENT=""; READONLY=0; ALLOW_WRITE=1 ;;
  *) echo "ask_claude: 알 수 없는 모드 '$MODE'" >&2; exit 2 ;;
esac

FULL_PROMPT="$PREAMBLE

---
작업 디렉터리: $PROJ
$PROMPT"

mkdir -p "$(dirname "$OUT")"
TMP="$OUT.tmp.$$"
BACKENDS=()

# 백엔드 1회 실행 — **출력 증가도 CPU 진행도** STALL_S 동안 없을 때만 중단한다.
# 출력 파일만 보면 안 된다: opencode 의 formatted 출력처럼 결과를 끝에 몰아서 내는 백엔드는
# '무출력'처럼 보이지만 정상 동작 중이다(오살 위험). CPU 시간(utime+stime)이 함께 멈춰야 진짜 정지다.
_cpu_jiffies() { awk '{print $14+$15}' "/proc/$1/stat" 2>/dev/null || echo 0; }

run_bounded() {
  local tag="$1"; shift
  local err="$1"; shift
  "$@" >"$TMP" 2>"$err" &
  local pid=$!
  local waited=0 stalled=0 last_bytes=0 last_cpu=0
  while kill -0 "$pid" 2>/dev/null; do
    sleep 10; waited=$((waited + 10))
    local now_bytes now_cpu
    now_bytes=$(wc -c <"$TMP" 2>/dev/null || echo 0)
    now_cpu=$(_cpu_jiffies "$pid")
    if [ "$now_bytes" -gt "$last_bytes" ] || [ "$now_cpu" -gt "$last_cpu" ]; then
      last_bytes=$now_bytes; last_cpu=$now_cpu; stalled=0
    else
      stalled=$((stalled + 10))
    fi
    if [ "$waited" -ge "$TIMEOUT_S" ] || [ "$stalled" -ge "$STALL_S" ]; then
      echo "ask_claude: [$tag] 중단 — 경과 ${waited}s, 정지 ${stalled}s (출력 ${now_bytes}b, cpu ${now_cpu})" >&2
      kill -TERM "$pid" 2>/dev/null
      sleep 3; kill -KILL "$pid" 2>/dev/null
      wait "$pid" 2>/dev/null
      return 124
    fi
  done
  wait "$pid"
  return $?
}

# ── 1차: Claude Code (+gstack) ────────────────────────────────────────────────
if [ -x "$CLAUDE_DS" ]; then
  CLAUDE_ARGS=(-p "$FULL_PROMPT")
  # review/investigate 는 읽기 전용으로 제한한다 — 쓰기 시도로 인한 권한 대기/부작용 차단.
  [ "$READONLY" -eq 1 ] && CLAUDE_ARGS=(--allowedTools "Read" "Grep" "Glob" "${CLAUDE_ARGS[@]}")
  # build 는 저작이 목적이므로 쓰기 도구를 **명시 허용**한다 — 허용하지 않으면 권한 프롬프트에서
  # 멈춰 무출력 타임아웃이 난다(설계상 사람이 없는 자율 실행이므로 프롬프트에 기댈 수 없다).
  [ "${ALLOW_WRITE:-0}" -eq 1 ] && CLAUDE_ARGS=(--allowedTools "Read" "Grep" "Glob" "Edit" "Write" "Bash" "${CLAUDE_ARGS[@]}")
  # 실측(2026-10-05): --allowedTools 만으로는 비대화형(-p)에서 Write 가 **실제로 실행되지 않았다**
  # (응답 텍스트만 내고 파일 없음 → 위임이 소득 0. 그래서 Claude Code 는 저작 작업에서 0바이트에 가까웠다).
  # 사람이 없는 자율 실행이라 권한 프롬프트에 기댈 수 없으므로 명시적으로 권한을 건너뛴다.
  # 안전은 다른 층이 담당한다: 금지경로 가드(scripts/protected_paths.py) + worktree 격리(improve_dispatch).
  [ "${ALLOW_WRITE:-0}" -eq 1 ] && CLAUDE_ARGS=(--dangerously-skip-permissions "${CLAUDE_ARGS[@]}")
  # 리포 밖 계약 코드(trader-agent)도 읽을 수 있게 열어 준다.
  [ -d "$EXTRA_DIR" ] && CLAUDE_ARGS=(--add-dir "$EXTRA_DIR" "${CLAUDE_ARGS[@]}")
  echo "ask_claude: [1차] Claude Code mode=$MODE timeout=${TIMEOUT_S}s stall=${STALL_S}s" >&2
  run_bounded claude "$OUT.stderr" "$CLAUDE_DS" "${CLAUDE_ARGS[@]}"
  RC=$?
  BYTES=$(wc -c <"$TMP" 2>/dev/null || echo 0)
  BACKENDS+=("claude:rc=$RC,${BYTES}b")
  if [ "$RC" -eq 0 ] && [ "$BYTES" -ge "$MIN_BYTES" ]; then
    mv "$TMP" "$OUT"
    printf 'backend=claude\n%s\n' "${BACKENDS[*]}" > "$OUT.backend"
    echo "ask_claude: 완료(Claude Code) → $OUT ($BYTES bytes)"
    exit 0
  fi
  echo "ask_claude: [1차 실패] rc=$RC ${BYTES}bytes → opencode 폴백" >&2
  rm -f "$TMP"
else
  BACKENDS+=("claude:missing")
fi

# ── 2차: opencode 폴백 제거 (2026-10-05, 사용자 지시: claude code 오케스트레이션만 사용) ──
# 실측: 1차 Claude Code 의 저작 실패는 **권한 차단**이 원인이었고, --dangerously-skip-permissions
# 로 수리했다(검증: backend=claude · rc=0 · 2086바이트 · py_compile 통과 · CCPROBE_OK).
# 즉 폴백이 필요했던 이유가 제거되었으므로 2차 백엔드를 없앤다. 되돌리려면 /tmp/ask_claude.bak 참조.
BACKENDS+=("opencode:removed")
echo "ask_claude: [2차 백엔드 없음] Claude Code 실패 → 위임 중단(rc=1). 원인을 고치고 재실행하라." >&2
exit 1

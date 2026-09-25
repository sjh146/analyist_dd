#!/usr/bin/env bash
# ask_claude.sh — 지적 작업을 Claude Code(+gstack)에 위임하고, 실패하면 opencode 로 자동 폴백.
#
# 역할 분담 (3안 하이브리드)
#   Hermes     : 오케스트레이션·스케줄·게이트·검증·보고 (크론 5개)
#   이 스크립트 : 코드 리뷰 / 근본원인 조사 / 스펙 같은 **지적 작업** 위임 + 파일 증거화
#   1차 백엔드  : Claude Code + gstack (Anthropic 프로토콜 → DeepSeek)
#   2차 백엔드  : opencode (OpenAI 프로토콜 → DeepSeek) — 1차가 실패할 때만
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
#   ASK_CLAUDE_CLAUDE_BIN / ASK_CLAUDE_OPENCODE_BIN  테스트용 교체
#
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

# ── 2차: opencode ────────────────────────────────────────────────────────────
OC_BIN="${ASK_CLAUDE_OPENCODE_BIN:-$(command -v opencode || echo "$HOME/.local/bin/opencode")}"
if [ -x "$OC_BIN" ]; then
  OC_ARGS=(run --pure --dir "$PROJ")
  [ -n "$OC_AGENT" ] && OC_ARGS+=(--agent "$OC_AGENT")
  OC_ARGS+=("$FULL_PROMPT")
  echo "ask_claude: [2차] opencode mode=$MODE agent=${OC_AGENT:-default}" >&2
  run_bounded opencode "$OUT.stderr.oc" "$OC_BIN" "${OC_ARGS[@]}"
  RC=$?
  BYTES=$(wc -c <"$TMP" 2>/dev/null || echo 0)
  BACKENDS+=("opencode:rc=$RC,${BYTES}b")
  if [ "$RC" -eq 0 ] && [ "$BYTES" -ge "$MIN_BYTES" ]; then
    mv "$TMP" "$OUT"
    printf 'backend=opencode\n%s\n' "${BACKENDS[*]}" > "$OUT.backend"
    echo "ask_claude: 완료(opencode 폴백) → $OUT ($BYTES bytes)"
    exit 0
  fi
  rm -f "$TMP"
else
  BACKENDS+=("opencode:missing")
fi

printf 'backend=none\n%s\n' "${BACKENDS[*]}" > "$OUT.backend"
echo "ask_claude: 두 백엔드 모두 실패 — 위임 없이 본업 계속 (${BACKENDS[*]})" >&2
exit 1

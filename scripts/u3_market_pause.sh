#!/usr/bin/env bash
# u3_market_pause.sh — U3(995일 창 패널 빌드) 장중 보호 정지. 1회성, 평일 장 시작 전(08:05) 호출.
#
# 왜 필요한가: U3 는 실측 20.8시간(0.42 pair/s, 32,626 페어) 작업이라 주말 저녁에 시작하면
#   월요일 장중(08:30 스윙 파이프라인·08:40 피드 발행)과 4코어를 그대로 경쟁한다.
#   사용자 정책 = "장중엔 트레이더·피드가 CPU 우선". 구동기의 가드는 '시작'만 막고
#   이미 돌고 있는 긴 작업은 막지 못하므로, 시작 전에 멈추는 것이 유일한 방법이다.
#   체크포인트(panel_995.npz.rows.pkl, 500페어 간격)가 있어 손실은 ≤500페어(≈20분)다.
#
# 하는 일:
#   1) panel_995 빌드 프로세스만 골라 SIGTERM → (5초) → 잔존 시 SIGKILL  (TR3 등 다른 sweep 은 건드리지 않는다)
#   2) 남은 예상 시간을 로그 실측 속도로 계산해 QUANT_MODEL_BACKLOG.json 의 U3.est_minutes 를 갱신
#      → 구동기의 ETA 가드가 "월 16:00 시작 시 화요일 장중까지 넘어간다"를 이유로 밤 창까지 대기하게 만든다.
#   3) 결과 요약을 stdout 으로 (크론 no_agent 잡이 그대로 전달)
#
# 사용: bash scripts/u3_market_pause.sh [--dry-run]
set -uo pipefail

PROJ=/home/jhshi/analyist_dd
CONTAINER=stock_xgboost_ml
LOGDIR=$PROJ/data/reports/me_cycle/logs
WSL=/mnt/c/Windows/System32/wsl.exe
TOTAL_DEFAULT=32626
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

cd "$PROJ" || { echo "U3 정지: 프로젝트 경로 없음 ($PROJ)"; exit 1; }

# ── 1) 대상 프로세스 (panel_995 빌드만) ──────────────────────────────────────
pids=$(docker top "$CONTAINER" 2>/dev/null \
  | awk '/wf_label_sweep/ && /panel_995\.npz/ {print $2}' | sort -u | tr '\n' ' ')
pids=${pids% }

if [ -z "$pids" ]; then
  echo "U3 장중 정지: 대상 없음 (panel_995 빌드 미실행) — 조치 없음"
  exit 0
fi

# ── 2) 진행률/속도 (로그 실측 — 자기신고 금지) ──────────────────────────────
last=""
while read -r f; do
  line=$(grep -h "Build progress:" "$f" 2>/dev/null | tail -1)
  [ -n "$line" ] && last="$line" && break
done < <(ls -t "$LOGDIR"/me_cycle_U3_*.log 2>/dev/null)

done_pairs=$TOTAL_DEFAULT; total_pairs=$TOTAL_DEFAULT; rate=0.42
if [ -n "$last" ]; then
  done_pairs=$(printf '%s' "$last"  | sed -n 's/.*Build progress: \([0-9]*\)\/\([0-9]*\).*/\1/p')
  total_pairs=$(printf '%s' "$last" | sed -n 's/.*Build progress: \([0-9]*\)\/\([0-9]*\).*/\2/p')
  r=$(printf '%s' "$last" | sed -n 's/.* \([0-9.]*\) pair\/s.*/\1/p')
  [ -n "$r" ] && rate="$r"
  done_pairs=${done_pairs:-0}; total_pairs=${total_pairs:-$TOTAL_DEFAULT}
fi
rem_min=$(awk -v t="$total_pairs" -v d="$done_pairs" -v r="$rate" \
  'BEGIN{ if (r<=0) r=0.42; m=(t-d)/r/60; if (m<5) m=5; printf "%d", m+5 }')  # +5분 여유

if [ "$DRY" = "1" ]; then
  echo "U3 장중 정지 [DRY-RUN]"
  echo "  대상 pid: $pids"
  echo "  진행: ${done_pairs}/${total_pairs} 페어 · 속도 ${rate} pair/s · 남은 예상 ${rem_min}분"
  echo "  (실행하면 SIGTERM → 5초 → SIGKILL, est_minutes=$rem_min 갱신)"
  exit 0
fi

# ── 3) 정지 ────────────────────────────────────────────────────────────────
"$WSL" -u root -- kill -TERM $pids 2>/dev/null
sleep 5
left=$(docker top "$CONTAINER" 2>/dev/null | awk '/wf_label_sweep/ && /panel_995\.npz/ {print $2}' | sort -u | tr '\n' ' ')
left=${left% }
killed9=""
if [ -n "$left" ]; then
  "$WSL" -u root -- kill -9 $left 2>/dev/null
  sleep 2
  killed9=" (+SIGKILL $left)"
  left=$(docker top "$CONTAINER" 2>/dev/null | awk '/wf_label_sweep/ && /panel_995\.npz/ {print $2}' | sort -u | tr '\n' ' ')
  left=${left% }
fi
stop_state="정지 확인"
[ -n "$left" ] && stop_state="⚠ 잔존 pid $left"

# ── 4) est_minutes 갱신 (구동기 ETA 가드가 장중 재시작을 막게) ────────────────
U3_REM=$rem_min /usr/bin/python3 - <<'PYEOF'
import json, os, re
p = "/home/jhshi/analyist_dd/docs/QUANT_MODEL_BACKLOG.json"
rem = int(os.environ["U3_REM"])
b = json.load(open(p, encoding="utf-8"))
for it in b["items"]:
    if it.get("id") == "U3":
        it["est_minutes"] = rem
        it["status"] = "pending"
        note = it.get("note") or ""
        if "장중 보호 정지" not in note:
            it["note"] = ("장중 보호 정지로 남은 작업 기준 est_minutes=%d 로 하향(2026-09-28 08:05). "
                          "전체 재빌드는 est 1250분." % rem) + (" / " + note if note else "")
        print("est_minutes = %d" % rem)
        break
else:
    print("U3 항목 없음 — est 미갱신")
b["updated_at"] = "2026-09-28T08:05:00+09:00"
tmp = p + ".tmp"
json.dump(b, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
os.replace(tmp, p)
PYEOF

echo "U3 장중 정지 완료"
echo "  정지 pid: $pids$killed9 — $stop_state"
echo "  진행: ${done_pairs}/${total_pairs} 페어 (체크포인트 보존, 손실 최대 500페어 ≈ 20분)"
echo "  est_minutes → ${rem_min}분 (구동기 ETA 가드가 장중 재시작을 차단)"
echo "  다음 재개: 월 15:30 이후 틱. 21:00 시작 시 화 02:00경 종료(장중 회피)"

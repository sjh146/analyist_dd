#!/usr/bin/env bash
# u3_launcher.sh — 995일 창 패널 빌드(U3)를 "재생성 창 이후 · 개장 전" 좁은 틈에 자동 착수시킨다.
#
# 왜 런처가 필요한가 (2026-09-28 실측):
#   · U3 은 남은 17,576 페어 ÷ 0.368 pair/s = 13.3시간 → 평일 장외 창(20:35~09:00)보다 길다.
#     그래서 20:00 컨테이너 재생성 직후에 시작해 컨테이너 timeout 42,000초(11.7시간)로
#     **개장(09:00) 전에 반드시 끝나게** 자르고, 2밤에 나눠 완주한다(체크포인트 500페어).
#   · 크론 틱(--tick)은 --force 를 못 쓰므로 ETA 가드가 U3 를 건너뛴다(설계대로).
#     장외의 부하 가드는 --force 로만 뚫리므로 런처가 그 역할을 맡는다.
#   · 구간 고정(--end-date 2026-09-27)이 없으면 end_date 가 매일 밀려 체크포인트가 폐기된다
#     (실측: 고정하면 "체크포인트 재개: processed=15000/32576 (46.0%)").
#
# 중지: 이 스크립트의 pid 를 kill (pgrep -f u3_launcher.sh)
LOG=/home/jhshi/analyist_dd/data/reports/me_cycle/u3_launcher.log
PANEL=/home/jhshi/analyist_dd/services/xgboost-ml/app/models/wf/panel_995.npz
cd /home/jhshi/analyist_dd || exit 1

echo "[$(date '+%F %T')] u3_launcher 시작 (pid $$) — 창 20:35~21:00, 종료 상한 08:40" >> "$LOG"
while true; do
  if [ -f "$PANEL" ]; then
    echo "[$(date '+%F %T')] panel_995.npz 완성 — 런처 종료" >> "$LOG"
    exit 0
  fi
  hhmm=$(date +%H%M)
  weekday=$(date +%u)
  # 착수 창: 평일·주말 모두 20:35~21:00 (11.7시간 실행 → 08:20 종료). 주말은 여유가 있으면 더 일찍도 가능.
  in_window=0
  if [ "$hhmm" -ge 2035 ] && [ "$hhmm" -le 2100 ]; then in_window=1; fi
  if [ "$weekday" -ge 6 ] && [ "$hhmm" -ge 0300 ] && [ "$hhmm" -le 0800 ]; then in_window=1; fi

  if [ "$in_window" = "1" ]; then
    if pgrep -f "[w]f_label_sweep.py --panel /app/app/models/wf/panel_995" >/dev/null; then
      echo "[$(date '+%F %T')] 이미 빌드가 돌고 있다 — 대기" >> "$LOG"
    elif [ -f /home/jhshi/analyist_dd/data/reports/me_cycle/running.pid ]; then
      echo "[$(date '+%F %T')] 사이클 lock 존재 — 대기" >> "$LOG"
    else
      out=$(/usr/bin/python3 scripts/model_engineer_cycle.py --start U3 --force 2>&1)
      rc=$?
      echo "[$(date '+%F %T')] --start U3 --force rc=$rc :: $out" >> "$LOG"
      sleep 120
      if pgrep -f "[w]f_label_sweep.py --panel /app/app/models/wf/panel_995" >/dev/null; then
        echo "[$(date '+%F %T')] 빌드 기동 확인 ✓" >> "$LOG"
      else
        echo "[$(date '+%F %T')] 기동 실패 — 다음 주기에 재시도" >> "$LOG"
      fi
    fi
  fi
  sleep 240
done

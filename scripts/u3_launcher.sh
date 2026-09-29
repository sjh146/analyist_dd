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

echo "[$(date '+%F %T')] u3_launcher 시작 (pid $$) — 창 20:35~21:15, 종료 상한 08:55" >> "$LOG"
while true; do
  # 종료 조건 = 패널 완성 **AND** U3 스윕이 rc=0 으로 기록됨(교착 방지 수리 · 2026-09-29 실측).
  #  종전 조건은 '패널 npz 존재'만 봤다. 그런데 패널은 빌드 **끝**에 저장되고 스윕은 그 **뒤**에
  #  시작되므로, 개장 전 컨테이너 timeout(08:35:55)에 스윕이 잘려 죽는 밤에는 패널만 남고 결과가
  #  없다 → 런처가 스스로 종료 → 틱은 est 1410분 때문에 ETA 가드로 U3 를 계속 건너뛴다(장시간
  #  항목은 틱이 --force 를 못 쓴다) → **스윕을 아무도 시작하지 않는 교착**. 패널이 캐시되면
  #  wf_wave.build_panel 이 `panel cache 재사용` 으로 즉시 넘어가 재실행 비용은 스윕뿐이므로,
  #  런처는 살아남아 다음 창(20:35~21:15)에 그 스윕을 착수시켜야 한다.
  if [ -f "$PANEL" ]; then
    u3_done=$(/usr/bin/python3 /home/jhshi/analyist_dd/scripts/u3_done_check.py 2>/dev/null)
    if [ "$u3_done" = "1" ]; then
      echo "[$(date '+%F %T')] panel_995.npz 완성 + U3 rc=0 기록 — 런처 종료" >> "$LOG"
      exit 0
    fi
    if [ "$last_panel_phase" != "sweep" ]; then
      echo "[$(date '+%F %T')] panel_995.npz 는 있으나 U3 rc=0 기록 없음 — 스윕 재착수 대기(런처 유지)" >> "$LOG"
      last_panel_phase="sweep"
    fi
  fi
  hhmm=$(date +%H%M)
  weekday=$(date +%u)
  # 착수 창: 평일·주말 모두 20:35~21:15 (11.7시간 실행 → 최대 08:55 종료, 개장 09:00 전).
  # 2026-09-28 확장(21:00→21:15): 다른 역할(리서처) 사이클이 락을 잡으면 --start 가 rc=3 으로
  # 거부되는데, 창이 25분뿐이면 락 경합 한 번에 그날 밤을 통째로 잃는다. 15분을 더 벌어 재시도
  # 횟수를 6→11회로 늘린다(늦게 시작해도 08:55 이전 종료라 장중 진입은 없다).
  in_window=0
  if [ "$hhmm" -ge 2035 ] && [ "$hhmm" -le 2115 ]; then in_window=1; fi
  if [ "$weekday" -ge 6 ] && [ "$hhmm" -ge 0300 ] && [ "$hhmm" -le 0800 ]; then in_window=1; fi

  if [ "$in_window" = "1" ]; then
    # ── 피처 코드 프리즈 프리플라이트 (2026-09-29 실측) ─────────────────────────────
    # 09-28 밤 빌드가 354분·32,576/32,576(100%)를 채운 뒤 "빌드 중 피처 코드 변경"으로 저장이
    # 거부되어 전량 소실됐다(01:48 커밋 c4b431c 가 feature_pipeline.py·market_features.py 수정).
    # 편집이 진행 중이면 시작해도 결과가 백지가 된다 → feature_engine/processors 최신 mtime 이
    # 120분보다 최근이면 이번 주기는 건너뛴다(편집이 멈추면 다음 주기에 자동 착수).
    newest=$(find services/xgboost-ml/app/feature_engine services/xgboost-ml/app/processors \
             -name '*.py' -printf '%T@\n' 2>/dev/null | sort -n | tail -1 | cut -d. -f1)
    if [ -n "$newest" ]; then
      age_min=$(( ( $(date +%s) - newest ) / 60 ))
      if [ "$age_min" -lt 120 ]; then
        if [ "$last_skip_min" != "$age_min" ]; then
          echo "[$(date '+%F %T')] 피처 코드가 ${age_min}분 전에 수정됨(<120분) — 프리즈 대기, 착수하지 않음" >> "$LOG"
          last_skip_min=$age_min
        fi
        sleep 240
        continue
      fi
    fi
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

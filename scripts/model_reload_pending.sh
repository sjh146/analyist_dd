#!/bin/sh
# model_reload_pending.sh — 프로덕션 승격을 **서빙 프로세스에 실제로 반영**하는 지연 재기동.
#
# WHY (2026-10-01 22:0x 실측, 이 스크립트를 만든 이유)
#  ① 저녁 파이프라인(scripts/full_pipeline_dd.sh L334)은 `champion_promote` 로 champion/ 을 교체할 뿐
#     `docker restart stock_xgboost_ml` 을 하지 않는다(스크립트에 restart/reload 0건).
#  ② xgboost-ml 서비스는 HTTP 라우트가 없고(스케줄러형), `initialize()` 에서 startup 시 **1회**
#     `self.model.load(MODEL_PATH/xgboost_model.pkl)` 만 한다 — 재로드 경로 없음.
#  실측: champion/xgboost_model.pkl mtime = 2026-10-01 21:26 KST(오늘 승격) 인데 app.main 프로세스
#     시작이 11:06 → 서빙 중인 모델은 **09-23 학습분(0.551318)** 이다. 재기동 전까지 다음 19:00 예측도
#     옛 챔피언으로 돈다.
#  그렇다고 즉시 재기동하면 **돌고 있는 실험을 SIGKILL(137) 한다**(스킬에 기록된 20:00 재생성 사고와 동형).
#  → 구동기 락·컨테이너 학습 프로세스가 **모두 빈 시각**에만, 창(06:00~07:30 KST) 안에서 재기동한다.
#  창 안에서 조건이 안 되면 재기동하지 않고 로그만 남긴다(다음 사이클/사람이 판단).
set -u
PROJ=/home/jhshi/analyist_dd
LOG="$PROJ/data/reports/me_cycle/model_reload_$(date +%Y%m%d).log"
log() { echo "[$(date '+%F %T')] $*" >> "$LOG"; }

champ_mtime() { stat -c %Y "$PROJ/services/xgboost-ml/app/models/champion/xgboost_model.pkl" 2>/dev/null || echo 0; }
ctr_started_epoch() {
  s=$(docker inspect --format '{{.State.StartedAt}}' stock_xgboost_ml 2>/dev/null) || return
  date -d "$s" +%s 2>/dev/null
}
cycle_busy() {   # 구동기(모델엔지니어/리서처) 락 — 살아있는 pid 일 때만
  /usr/bin/python3 - <<'PY' 2>/dev/null
import sys
sys.path.insert(0, "/home/jhshi/analyist_dd/scripts")
try:
    import model_engineer_cycle as m
    print("busy" if m.running_pid() else "free")
except Exception:
    print("free")
PY
}
train_busy() {   # 컨테이너 안 학습/평가 (busybox ps -ef 사용 — ps -eo 는 조용히 실패한다)
  docker exec stock_xgboost_ml ps -ef 2>/dev/null | grep -E "retrain_champion|champion_robust_eval|wf_label_sweep|wf_wave|blend_eval|cg5[0-9]_run" | grep -v grep | head -1
}

now=$(date +%s)
t730=$(date -d "$(date +%F) 07:30" +%s)
if [ "$now" -lt "$t730" ]; then DEADLINE=$t730; else DEADLINE=$(date -d "tomorrow 07:30" +%s); fi

if [ "$(champ_mtime)" -le "$(ctr_started_epoch || echo 0)" ]; then
  log "재기동 불필요 — 챔피언 mtime <= 컨테이너 시작 시각(이미 로드됨)"
  exit 0
fi
log "재기동 대기 시작 — champion mtime=$(date -d @$(champ_mtime) '+%F %T') > 컨테이너 시작=$(date -d @$(ctr_started_epoch || echo 0) '+%F %T') · 데드라인=$(date -d @$DEADLINE '+%F %T')"

while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  [ "$(champ_mtime)" -le "$(ctr_started_epoch || echo 0)" ] && { log "이미 다른 경로로 로드됨 → 종료"; exit 0; }
  H=$(date +%H%M); H=${H#0}; [ -z "$H" ] && H=0
  if [ "$H" -ge 600 ] && [ "$H" -le 730 ]; then
    cb=$(cycle_busy); tb=$(train_busy)
    if [ "$cb" = "free" ] && [ -z "$tb" ]; then
      log "조건 충족(락 없음·학습 없음) → docker restart stock_xgboost_ml"
      if docker restart stock_xgboost_ml >> "$LOG" 2>&1; then
        sleep 20
        ns=$(ctr_started_epoch || echo 0)
        if [ "$ns" -gt "$(champ_mtime)" ]; then
          log "완료 — 재시작 $(date -d @$ns '+%F %T') > 챔피언 mtime / 로드 AUC=$(docker exec stock_xgboost_ml cat /app/app/models/champion/auc.txt 2>/dev/null)"
          exit 0
        fi
        log "경고: 재시작했으나 StartedAt 이 챔피언 mtime 보다 이르다 — 확인 필요"
        exit 0
      fi
      log "오류: docker restart 실패"
      exit 1
    fi
    log "보류 — 사이클=$cb · 학습프로세스=$(echo "$tb" | cut -c1-80)"
  fi
  sleep 120
done
log "데드라인까지 조건 미충족 — 재기동하지 않고 종료(다음 사이클/사람이 판단)"
exit 0

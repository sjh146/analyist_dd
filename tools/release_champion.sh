#!/usr/bin/env bash
# 릴리스 절차(챔피언 교체) — 동결 → 백업 → dry-run → 사전검사 → 적용(승인) → 15분 검증 → 롤백.
# 역할: quant-release (docs/QUANT_ROLE_PLAN_V2.md §5.5). 승인 경계: --apply 없이는 아무것도 바꾸지 않는다.
#
# 사용:
#   tools/release_champion.sh --candidate app/models/champion_cand              # 리허설(dry-run+사전검사)
#   tools/release_champion.sh --candidate app/models/champion_cand --with-probe # +라이브 스코어 프로브(90초)
#   tools/release_champion.sh --candidate app/models/champion_cand --apply      # 실제 승격(대표 승인 후)
#   tools/release_champion.sh --candidate app/models/champion_prev_<ts> --min-improvement -0.01 --apply   # 롤백
#
# 산출물: docs/releases/<날짜>-champion-<note>.md · 적용 시 백업 디렉터리(champion_prev_<ts>) 자동 생성.
set -uo pipefail
REPO=/home/jhshi/analyist_dd
CAND=""; APPLY=0; MINIMP=0.02; WITH_PROBE=0; SKIP_PRECHECK=0; AUTO_ROLLBACK=0; NOTE=champion
while [ $# -gt 0 ]; do
  case "$1" in
    --candidate) CAND="${2:-}"; shift 2;;
    --apply) APPLY=1; shift;;
    --min-improvement) MINIMP="${2:-0.02}"; shift 2;;
    --with-probe) WITH_PROBE=1; shift;;
    --skip-precheck) SKIP_PRECHECK=1; shift;;
    --auto-rollback) AUTO_ROLLBACK=1; shift;;
    --note) NOTE="${2:-champion}"; shift 2;;
    -h|--help) sed -n '2,14p' "$0"; exit 0;;
    *) echo "알 수 없는 인자: $1"; exit 64;;
  esac
done
[ -n "$CAND" ] || { echo "필수: --candidate <컨테이너 경로> (예: app/models/champion_cand)"; exit 64; }

DATE=$(date +%F); LOG=$(mktemp /tmp/release_champion_XXXX.log)
DOC="$REPO/docs/releases/${DATE}-champion-${NOTE}.md"
mkdir -p "$REPO/docs/releases"
BLOCK=0
say(){ echo "$@" | tee -a "$LOG"; }
step(){ echo; say "── $* ──"; }

# 1) 동결 — 소비자 목록(추정 금지, grep 실측)
step "1. 동결(소비자 목록)"
say "$(cd "$REPO" && grep -rl 'models/champion' --include=*.py --include=*.sh --include=*.md . 2>/dev/null | head -20)"

# 2) 백업 — 직전 챔피언 정보 + 승격이 만들 백업 디렉터리 규칙
step "2. 백업"
say "현 챔피언 auc=$(cat "$REPO/services/xgboost-ml/app/models/champion/auc.txt" 2>/dev/null)"
say "직전 백업들: $(ls -1d "$REPO"/services/xgboost-ml/app/models/champion_prev_* 2>/dev/null | tail -2 | xargs -n1 basename | tr '\n' ' ')"
say "승격 시 champion_promote 가 champion_prev_<ts> 로 현 챔피언을 자동 백업한다."

# 3) dry-run — 게이트 상태를 문자열 그대로 인용
step "3. dry-run(champion_promote)"
DRY=$(docker exec -w /app stock_xgboost_ml python -m app.training.champion_promote \
        --candidate "$CAND" --champion app/models/champion --min-improvement "$MINIMP" \
        --summary-out /tmp/release_dryrun.json --dry-run 2>&1)
say "$DRY" | grep -aE '"(status|reason|candidate_auc|champion_auc_before|n_features)"' | tee -a "$LOG"
DRY_STATUS=$(printf '%s' "$DRY" | sed -n 's/.*"status": *"\([^"]*\)".*/\1/p' | tail -1)
say "dry-run status = ${DRY_STATUS:-unknown}"

# 4) 사전검사 — 감사·계약·(선택)라이브 스코어 프로브
if [ "$SKIP_PRECHECK" -eq 0 ]; then
  step "4. 사전검사(release_precheck)"
  PRE_ARGS=(--target champion --candidate "$CAND" --min-improvement "$MINIMP")
  [ "$WITH_PROBE" -eq 1 ] && PRE_ARGS+=(--with-probe)
  if ! python3 "$REPO/tools/release_precheck.py" "${PRE_ARGS[@]}" 2>&1 | tee -a "$LOG"; then
    BLOCK=1
  fi
else
  step "4. 사전검사(건너뜀: --skip-precheck)"; say "경고: 사전검사를 건너뛰었다."
fi

if [ "$BLOCK" -ne 0 ]; then
  say
  say "⛔ 사전검사 BLOCK — 릴리스 중단. (프로브가 없으면 --with-probe 로 라이브 스코어 분포를 확인하라)"
  say "로그: $LOG"
  exit 2
fi

# 5) 적용 — 승인(--apply) 없이는 여기서 종료
if [ "$APPLY" -eq 0 ]; then
  step "5. 적용 보류(--apply 없음)"
  say "리허설 통과. 실제 승격 명령:"
  say "  tools/release_champion.sh --candidate $CAND --min-improvement $MINIMP --apply"
  { echo "# 릴리스(리허설) $DATE — champion/$NOTE"; echo; echo '```'; cat "$LOG"; echo '```'; } > "$DOC"
  say "릴리스 문서: $DOC"
  exit 0
fi

step "5. 적용"
OUT=$(docker exec -w /app stock_xgboost_ml python -m app.training.champion_promote \
        --candidate "$CAND" --champion app/models/champion --min-improvement "$MINIMP" \
        --summary-out app/reports/ml_result.json 2>&1)
say "$OUT" | tail -20 | tee -a "$LOG"
BACKUP=$(printf '%s' "$OUT" | sed -n 's/.*"backup_dir": *"\([^"]*\)".*/\1/p' | tail -1)
NEW_AUC=$(cat "$REPO/services/xgboost-ml/app/models/champion/auc.txt" 2>/dev/null)
say "새 챔피언 auc=$NEW_AUC · 백업=${BACKUP:-없음(승격 실패 가능)}"

# 6) 릴리스 후 검증 (즉시 1회 + 15분 뒤 1회)
verify(){
  say "  feed: $(curl -s --noproxy '*' -m 8 http://127.0.0.1:8090/health)"
  say "  loop: $(python3 -c "import json;d=json.load(open('/mnt/c/Users/jhshi/analyist_dd/trader-agent/loop_state.json'));print(d['last_cycle']['ts'], d['last_cycle']['phase'], 'halt=', d['loop']['halt'])" 2>/dev/null || echo '판독 실패')"
  python3 "$REPO/tools/release_precheck.py" --target champion 2>&1 | tail -6 | tee -a "$LOG"
}
step "6a. 릴리스 후 검증(즉시)"; verify
say "15분 후 재검증 실행: sleep 900; tools/release_champion.sh --candidate $CAND --skip-precheck --note verify (또는 위 verify 수동)"
sleep 900
step "6b. 릴리스 후 검증(15분)"; verify

# 7) 롤백 명령 기록 (+ 선택 실행)
ROLLBACK="tools/release_champion.sh --candidate ${BACKUP:-<backup-dir>} --min-improvement -0.01 --apply --skip-precheck"
step "7. 롤백 명령"; say "  $ROLLBACK"
if [ "$AUTO_ROLLBACK" -eq 1 ] && [ "${VERIFY_OK:-1}" -ne 0 ]; then
  say "auto-rollback 조건 충족 시 위 명령을 그대로 실행하라(자동 실행은 사람 승인 항목)."
fi

# 8) 릴리스 문서
{ echo "# 릴리스 $DATE — champion/$NOTE"; echo; echo "- 새 챔피언 auc: $NEW_AUC"; echo "- 백업: ${BACKUP:-?}"; echo "- 롤백: \`$ROLLBACK\`"; echo; echo '```'; cat "$LOG"; echo '```'; } > "$DOC"
say "릴리스 문서: $DOC"
exit 0

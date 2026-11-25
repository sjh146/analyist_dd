#!/bin/bash
# analyist_dd 피드 스냅샷 발행 (스크리너 산출물 → data/feed/screener_latest.json)
# trader-agent 가 폴링하는 파일을 갱신한다. 발행만 하므로 몇 초면 끝난다.
set -a; . /home/dduckbeagy/analyist_dd/.env; set +a
export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434
cd /home/dduckbeagy/analyist_dd || exit 1

# ① 켈리 사전확률(scoring_summary.screener_stats)을 먼저 만든다. 측정 기록이 없거나
#    축소 후 기대값이 음수(f*<=0)면 스크립트가 스스로 기록을 건너뛰고 근거만 남긴다(exit 3).
#    발행은 계속돼야 하므로 그 실패를 삼킨다(종료코드는 로그에 남긴다).
/usr/bin/python3 scripts/build_screener_stats.py 2>&1 | tail -5
echo "[feed_publish] screener_stats 종료코드=${PIPESTATUS[0]}"

# ② 피드 발행 (계약 v1.1: score_kind·valid_until·ml_* 포함)
/usr/bin/python3 scripts/feed_export.py
RC=$?
chmod 644 /home/jhshi/analyist_dd/data/feed/screener_latest.json 2>/dev/null
echo "[feed_publish] feed_export rc=$RC"

# ③ GitHub 배포 저장소(trader-feed)로 게시 — 고객 PC 가 이것을 pull 한다.
#    2026-11-24: Jenkins 의존을 제거하고 호스트 크론에서 직접 게시한다(젠킨스 로그인/기동 상태와 무관).
if [ "$RC" -eq 0 ]; then
  /usr/bin/python3 scripts/publish_feed_github.py --repo-dir /home/jhshi/trader-feed 2>&1 | tail -8
  echo "[feed_publish] publish_feed_github rc=${PIPESTATUS[0]}"
fi
exit "$RC"

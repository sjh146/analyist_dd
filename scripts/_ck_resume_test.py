"""패널 빌드 체크포인트/재개 검증용 스크립트 (임시 · 검증 재현용).

무엇을 검증하나: 컨테이너 재생성으로 `docker exec` 빌드가 SIGKILL 되어도(실측 2026-09-25:
150종목 빌드가 30,000/41,893 에서 전량 소실) 다음 실행이 **저장된 진척에서 이어서** 빌드하는지.

실행(1차 — 70초 뒤 SIGKILL 로 소실을 흉내낸다):
  docker exec -d stock_xgboost_ml sh -c 'cd /app && PANEL_CK_EVERY=50 OMP_NUM_THREADS=1 \
      python -u scripts/_ck_resume_test.py > /tmp/ck_test1.log 2>&1'
  sleep 70; docker top stock_xgboost_ml | awk '/_ck_resume_test/{print $2}'   # 호스트 PID
  /mnt/c/Windows/System32/wsl.exe -u root -- kill -9 <PID>                   # 컨테이너 프로세스는 root 소유
실행(2차 — 재개):
  docker exec stock_xgboost_ml sh -c 'cd /app && PANEL_CK_EVERY=50 OMP_NUM_THREADS=1 \
      python -u scripts/_ck_resume_test.py > /tmp/ck_test2.log 2>&1'
합격 기준(2026-09-25 실측):
  1차 로그 "체크포인트 저장: 50/183 rows=50", 2차 로그 "체크포인트 재개: processed=50/183 rows=50",
  2차 종료 "RESULT rows=183 cols=210", 성공 후 체크포인트 파일 삭제.
  ⚠ 재개 시 `list(df)`(컬럼 이름) 금지 — `to_dict("records")` 를 써야 한다(그 버그로 빌드 실패했었다).

사용: python scripts/_ck_resume_test.py [npz경로] [limit] [days]
"""
import logging
import sys
import time

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

import wf_wave as w  # noqa: E402

npz = sys.argv[1] if len(sys.argv) > 1 else "/tmp/ck_test.npz"
limit = int(sys.argv[2]) if len(sys.argv) > 2 else 3
days = int(sys.argv[3]) if len(sys.argv) > 3 else 90

t0 = time.time()
df, names = w.build_panel(npz, limit, days, market=None, since="2025-07-01",
                          min_days=250, min_value=100000000, order="value")
print(f"RESULT rows={len(df)} cols={len(names)} elapsed={time.time() - t0:.1f}s", flush=True)

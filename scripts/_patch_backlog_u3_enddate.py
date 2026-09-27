#!/usr/bin/env python3
"""_patch_backlog_u3_enddate — U3 를 '구간 고정 + 야간 창'용으로 정정한다.

실측(2026-09-28 04:30):
① 체크포인트 재개 확인 — `--end-date 2026-09-27` 를 주면
   "체크포인트 재개: processed=15000/32576 (46.0%)" 로 이어받는다.
   반면 기본(구간 끝 = 실행 시각)으로 두면 end_date 가 하루 밀려 **매일 체크포인트가 폐기**되고
   0% 부터 다시 시작한다(995일 창 = 32,576 페어 ÷ 실측 0.368 pair/s = 24.6시간).
   → 이 때문에 U3 는 47% 에서 두 번 소실되고도 영원히 완주하지 못하는 구조였다.
② 남은 작업 = 17,576 페어 ÷ 0.368 pair/s = 13.3시간 → 평일 장외 창(20:35~09:00 ≈ 12.4시간)보다 길다.
   그래서 컨테이너 timeout 을 42,000초(11.7시간)로 잘라 **개장 전에 반드시 종료**하고,
   2밤에 나눠 완주한다(체크포인트 500페어라 손실 ≤500페어).
"""
import json
import os
from datetime import datetime

PROJ = "/home/jhshi/analyist_dd"
PATH = os.path.join(PROJ, "docs/QUANT_MODEL_BACKLOG.json")
b = json.load(open(PATH, encoding="utf-8"))
now = datetime.now().astimezone().replace(microsecond=0).isoformat()

for it in b["items"]:
    if it["id"] != "U3":
        continue
    it["command"] = (
        "docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 timeout 42000 "
        "python -u scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_995.npz "
        "--days 995 --end-date 2026-09-27 --limit 50 --folds 5 --seeds 3 "
        "--only LS_quant_q30_h5'")
    it["est_minutes"] = 700          # 11.7시간 창(20:35~08:20) — 재생성 창·개장 전 종료 상한
    it["cost"] = ("남은 17,576 페어 ÷ 0.368 pair/s = 13.3시간 → 2밤 분할(밤당 ≤11.7시간). "
                  "timeout 42000s = 개장(09:00) 전 강제 종료, 체크포인트 500페어마다 저장")
    it["note"] = ((it.get("note") or "") +
                  " [2026-09-28 정정] ①구간 고정(--end-date 2026-09-27)을 넣어야 체크포인트가 "
                  "이어진다(실측: 재개 processed=15000/32576 확인). ②크론 틱은 --force 를 못 쓰므로 "
                  "U3 착수는 scripts/u3_launcher.sh(20:35~21:00 창)가 담당한다. "
                  "③ETA 가드 상한(est_minutes 700)이라 정상 틱도 21:00 이후엔 시작 가능.")
    it["launcher"] = "scripts/u3_launcher.sh (nohup) — data/reports/me_cycle/u3_launcher.log"
    break

b["updated_at"] = now
with open(PATH, "w", encoding="utf-8") as f:
    json.dump(b, f, ensure_ascii=False, indent=2)
print("U3 정정 완료:", now)
